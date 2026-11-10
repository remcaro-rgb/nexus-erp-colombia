"""T-PORTAL-EMP-03 (security, G2): employee A cannot access employee B's payslips (IDOR) nor any salary aggregate."""

from __future__ import annotations

import pytest

from hr.models import DataUpdateRequest
from iam.policy.engine import get_engine
from kernel import tenancy
from timekeeping.models import Absence

from .conftest import add_payslip, portal_ctx

pytestmark = pytest.mark.django_db

INTERNAL_SALARY_ENDPOINTS = (
    "/api/v1/hr/employees",
    "/api/v1/hr/contracts",
    "/api/v1/hr/self-service/payslips",
    "/api/v1/payroll/payslips",
    "/api/v1/payroll/runs",
    "/api/v1/payroll/reports/summary",
    "/api/v1/kpis/nomina",
    "/api/v1/kpis/talento",
    "/api/v1/timekeeping/absences",
)


def test_T_PORTAL_EMP_03_employee_a_cannot_read_employee_b_documents(employees_world, payslips, renderer):
    w = employees_world
    api_a, api_b = w["api_a"], w["api_b"]
    add_payslip(payslips, w["a"], "2026-10", "2350000.0000")
    b_slip = add_payslip(payslips, w["b"], "2026-10", "9100000.0000")
    c_slip = add_payslip(payslips, w["c"], "2026-10", "1800000.0000")  # same employee-id space, other company
    b_vac = api_b.post(
        "/api/portal/v1/employee/vacations", {"start_date": "2026-12-14", "end_date": "2026-12-15"}
    ).json()
    b_upd = api_b.post("/api/portal/v1/employee/data-updates", {"changes": {"address": "Calle B 1-23"}}).json()
    b_certs = [c["id"] for c in api_b.get("/api/portal/v1/employee/certificates").json()["items"]]
    assert b_certs and b_vac["id"] and b_upd["id"]

    secrets = {str(w["b"].id), w["b"].full_name, "9100000", "9800000", str(b_slip.id), b_vac["id"], b_upd["id"],
               "Calle B", *b_certs, str(c_slip.id), str(w["c"].id)}  # fmt: skip
    targets = {
        "payslips": [str(b_slip.id), str(c_slip.id)],
        "certificates": b_certs,
        "vacations": [b_vac["id"]],
        "data-updates": [b_upd["id"]],
    }
    calls = 0
    for resource, ids_ in targets.items():
        for target in ids_:
            suffixes = ("", "/pdf") if resource in ("payslips", "certificates") else ("",)
            for suffix in suffixes:
                url = f"/api/portal/v1/employee/{resource}/{target}{suffix}"
                r = api_a.get(url)
                assert r.status_code == 404 and r.json()["code"] == "portal_document_not_owned", (url, r.status_code)
                assert not any(s in r.content.decode().replace(url, "") for s in secrets), url
                calls += 1
    assert calls == 10 and renderer.calls == []  # nothing of B was ever rendered

    # lists, profile and KPIs of A never contain B's data
    for url in ("payslips", "certificates", "vacations", "data-updates", "profile", "kpis"):
        r = api_a.get(f"/api/portal/v1/employee/{url}")
        assert r.status_code == 200, (url, r.content)
        assert not any(s in r.content.decode() for s in secrets), url

    # naming B (or another company) in the query string or the body is refused before any handler logic
    for name, value in (("employee_id", w["b"].id), ("company_id", w["c"].company_id), ("party_id", w["b"].id)):
        for url in ("payslips", "certificates", "vacations", "kpis", "profile"):
            r = api_a.get(f"/api/portal/v1/employee/{url}", **{name: str(value)})
            assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation", (url, name)
    r = api_a.post(
        "/api/portal/v1/employee/vacations",
        {"start_date": "2026-12-14", "end_date": "2026-12-15", "employee_id": str(w["b"].id)},
    )
    assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation"
    r = api_a.post("/api/portal/v1/employee/data-updates", {"changes": {"phone": "1"}, "employee_id": str(w["b"].id)})
    assert r.status_code == 403
    # nothing was created for B by A's refused requests
    with tenancy.scope(portal_ctx(world_of(w), api_a)):
        assert not Absence.objects.filter(employee_id=w["b"].id).exclude(pk=b_vac["id"]).exists()
        assert not DataUpdateRequest.objects.filter(employee_id=w["b"].id).exclude(pk=b_upd["id"]).exists()


def world_of(w):
    return w["world"]


def test_T_PORTAL_EMP_03_no_salary_aggregate_reachable_from_the_portal(employees_world, payslips, world):
    w = employees_world
    api_a = w["api_a"]
    add_payslip(payslips, w["a"], "2026-10", "2350000.0000")
    add_payslip(payslips, w["b"], "2026-10", "9100000.0000")

    # every internal endpoint (HR, payroll, KPI aggregates) refuses portal tokens
    for url in INTERNAL_SALARY_ENDPOINTS:
        r = api_a.get(url)
        assert r.status_code in (401, 404), (url, r.status_code)
        if r.status_code == 401:
            assert r.json()["code"] == "unauthenticated", url
        assert "9100000" not in r.content.decode() and "9800000" not in r.content.decode()

    # the portal KPI is the employee's own last net pay only (no company / team totals)
    kpis = {k["id"]: k for k in api_a.get("/api/portal/v1/employee/kpis").json()["kpis"]}
    assert kpis["ep_ultimo_pago"]["value"] == "2350000.0000"
    assert set(kpis) == {"ep_dias_vacaciones", "ep_ultimo_pago", "ep_solicitudes_pendientes"}

    # policy layer: the employee principal holds no internal or aggregate permission and no salary field
    me = portal_ctx(world, api_a)
    engine = get_engine()
    with tenancy.scope(me):
        for code in (
            "hr.contract.read", "hr.employee.read", "payroll.payslip.read", "payroll.run.read",
            "payroll.report.read", "reporting.report.read", "portal.supplier_invoice.read",
        ):  # fmt: skip
            assert not engine.authorize(me, code).allowed, code
        for resource in ("hr.contract", "hr.employee", "payroll.payslip"):
            assert engine.field_rules(me, resource)[0] == set(), resource
        # repository layer: even without a view, querysets are reduced to the employee's own rows
        qs = engine.scope_queryset(me, "portal.employee_vacation.read", Absence.objects.all())
        assert set(qs.values_list("employee_id", flat=True)) <= {w["a"].id}
        assert engine.scope_queryset(me, "timekeeping.absence.read", Absence.objects.all()).count() == 0
        assert not engine.authorize(
            me, "portal.employee_payslip.read", {"company_id": w["a"].company_id, "employee_id": w["b"].id}
        ).allowed
        assert engine.authorize(
            me, "portal.employee_payslip.read", {"company_id": w["a"].company_id, "employee_id": w["a"].id}
        ).allowed


def test_T_PORTAL_EMP_03_internal_manager_without_payroll_clearance_sees_no_salary(employees_world, world, login):
    """Field policy: a team manager (max classification internal) reads contracts and absences but no salary."""
    world.add_role("jefe_equipo", ["hr.contract.read", "hr.employee.read", "timekeeping.absence.read"])
    world.add_user("lider", ["ALFA"], subject="lider-" + str(world.tenant_id))
    world.assign("lider", "jefe_equipo", "ALFA")
    lider = login("lider", "ALFA")
    items = lider.get("/api/v1/hr/contracts").json()["items"]
    assert items and all("salary" not in i and i.get("total") is None for i in items)
    assert "9800000" not in lider.get("/api/v1/hr/contracts").content.decode()
    emp = lider.get(f"/api/v1/hr/employees/{employees_world['a'].id}").json()
    assert "bank_account" not in emp and "health_entity" not in emp and "full_name" not in emp
    # the HR manager with sensitive clearance does see the salary
    full = employees_world["jefe"].get("/api/v1/hr/contracts").json()["items"]
    assert {i["salary"]["amount"] for i in full} == {"2500000.0000", "9800000.0000"}
