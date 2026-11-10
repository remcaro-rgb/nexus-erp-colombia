"""HR internal API: employees (consent, encryption, masking), contracts (overlap, integral fail-closed, amend,
terminate, events), positions/org units, and the payroll-facing HR interface."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.db import connection

from hr.directory import DbPayrollEmployeeSource
from hr.interfaces import PayrollEmployeeSource
from hr.models import ContractHistory, Employee
from kernel import services, tenancy
from timekeeping.models import Timesheet

pytestmark = pytest.mark.django_db

EMP = {
    "code": "E-100",
    "full_name": "Diana Gómez (sintética)",
    "document": {"document_type": "CC", "number": "52000100"},
    "email": "diana@example.invalid",
    "bank_account": "001234567890",
    "health_entity": "EPS SINTÉTICA",
    "data_consent": True,
}


def test_P13a_employee_crud_consent_encryption_and_etag(employees_world):
    jefe = employees_world["jefe"]
    r = jefe.post("/api/v1/hr/employees", {**EMP, "data_consent": False})
    assert r.status_code == 422 and r.json()["code"] == "consent_required"
    r = jefe.post("/api/v1/hr/employees", EMP)
    assert r.status_code == 201, r.content
    emp = r.json()
    assert emp["bank_account"] == "001234567890" and emp["document"]["number"] == "52000100"
    assert "data_consent" not in emp
    with tenancy.scope(employees_world["world"].ctx(["ALFA"])):
        stored = Employee.objects.get(pk=emp["id"]).bank_account_enc
    assert stored and "001234567890" not in stored  # encrypted at rest
    dup = jefe.post("/api/v1/hr/employees", {**EMP, "code": "E-101"})
    assert dup.status_code == 422  # same document
    detail = jefe.get(f"/api/v1/hr/employees/{emp['id']}")
    etag = detail["ETag"]
    assert jefe.patch(f"/api/v1/hr/employees/{emp['id']}", {"phone": "+57 1"}).status_code == 428
    ok = jefe.patch(f"/api/v1/hr/employees/{emp['id']}", {"phone": "+57 1"}, HTTP_IF_MATCH=etag)
    assert ok.status_code == 200 and ok.json()["phone"] == "+57 1"
    assert jefe.delete(f"/api/v1/hr/employees/{emp['id']}").status_code == 204
    assert jefe.get(f"/api/v1/hr/employees/{emp['id']}").json()["status"] == "ARCHIVED"
    # another company never sees it
    other = jefe.get("/api/v1/hr/employees").json()["items"]
    assert emp["id"] in {i["id"] for i in other}
    assert str(employees_world["c"].id) not in {i["id"] for i in other}


def test_P13a_employee_bulk_is_all_or_nothing(employees_world):
    jefe = employees_world["jefe"]
    bad = {
        "items": [{**EMP, "code": "B-1"}, {**EMP, "code": "B-2", "document": {"document_type": "CC", "number": "?"}}]
    }
    r = jefe.post("/api/v1/hr/employees:bulk", bad)
    assert r.status_code == 422
    assert not any(i["code"].startswith("B-") for i in jefe.get("/api/v1/hr/employees").json()["items"])
    good = {
        "items": [
            {**EMP, "code": "B-1"},
            {**EMP, "code": "B-2", "document": {"document_type": "CC", "number": "52000102"}},
        ]
    }
    r = jefe.post("/api/v1/hr/employees:bulk", good)
    assert r.status_code == 200 and [i["status"] for i in r.json()["items"]] == [201, 201]


def test_P13a_contract_lifecycle_history_events_and_overlap(employees_world):
    w = employees_world
    jefe = w["jefe"]
    emp = jefe.post("/api/v1/hr/employees", EMP).json()
    body = {
        "document_date": "2026-02-01", "employee_id": emp["id"], "contract_type": "INDEFINIDO",
        "start_date": "2026-02-01", "salary": {"amount": "3000000", "currency": "COP"}, "arl_risk_class": "II",
    }  # fmt: skip
    r = jefe.post("/api/v1/hr/contracts", body)
    assert r.status_code == 201, r.content
    c = r.json()
    assert c["status"] == "APPROVED" and c["salary"]["amount"] == "3000000.0000" and c["number"].startswith("CT-2026-")
    overlap = jefe.post("/api/v1/hr/contracts", {**body, "start_date": "2026-06-01"})
    assert overlap.status_code == 422 and overlap.json()["code"] == "contract_overlap"
    # prestación de servicios is not payroll: no overlap rule, not returned to payroll
    ps = jefe.post("/api/v1/hr/contracts", {**body, "contract_type": "PRESTACION_SERVICIOS"})
    assert ps.status_code == 201
    float_salary = jefe.post("/api/v1/hr/contracts", {**body, "salary": 3000000.5})
    assert float_salary.status_code == 422

    amend = jefe.post(
        f"/api/v1/hr/contracts/{c['id']}:amend",
        {"effective_date": "2026-07-01", "reason": "ascenso", "parameters": {"salary": "3600000"}},
    )
    assert amend.status_code == 200, amend.content
    assert amend.json()["salary"]["amount"] == "3600000.0000"
    bad = jefe.post(f"/api/v1/hr/contracts/{c['id']}:amend", {"parameters": {"employee_id": w["b"].id.hex}})
    assert bad.status_code == 422
    assert jefe.post(f"/api/v1/hr/contracts/{c['id']}:terminate", {}).json()["code"] == "reason_required"
    t = jefe.post(f"/api/v1/hr/contracts/{c['id']}:terminate", {"reason": "renuncia", "effective_date": "2026-10-31"})
    assert t.status_code == 200 and t.json()["status"] == "CANCELLED" and t.json()["end_date"] == "2026-10-31"
    assert jefe.delete(f"/api/v1/hr/contracts/{c['id']}").status_code == 409
    with tenancy.scope(w["world"].ctx(["ALFA"])):
        changes = list(
            ContractHistory.objects.filter(contract_id=c["id"]).order_by("at").values_list("change", flat=True)
        )
    assert changes == ["created", "amended", "terminated"]
    with connection.cursor() as cur:
        cur.execute(
            "SELECT envelope->'payload'->>'change' FROM kernel_outbox WHERE event_type = 'hr.contract.changed.v1'"
            " AND envelope->'payload'->>'contract_id' = %s ORDER BY created_at",
            [c["id"]],
        )
        assert [r[0] for r in cur.fetchall()] == ["created", "amended", "terminated"]


def test_P13a_integral_salary_needs_the_legal_minimum_and_fails_closed(employees_world, parameters):
    jefe = employees_world["jefe"]
    emp = jefe.post("/api/v1/hr/employees", EMP).json()
    body = {
        "document_date": "2026-02-01", "employee_id": emp["id"], "contract_type": "INDEFINIDO",
        "start_date": "2026-02-01", "salary": "12000000", "salary_type": "INTEGRAL",
    }  # fmt: skip
    low = jefe.post("/api/v1/hr/contracts", body)  # fixture: 13 × 1.000.000
    assert low.status_code == 422 and low.json()["code"] == "validation_failed"
    parameters.values.pop("hr.salario_integral.min_smmlv")
    unknown = jefe.post("/api/v1/hr/contracts", {**body, "salary": "14000000"})
    assert unknown.status_code == 422 and unknown.json()["code"] == "parameter_unknown"


def test_P13a_positions_and_org_units(employees_world):
    jefe = employees_world["jefe"]
    unit = jefe.post("/api/v1/hr/org-units", {"code": "ADM", "name": "Administración"}).json()
    child = jefe.post("/api/v1/hr/org-units", {"code": "NOM", "name": "Nómina", "parent_id": unit["id"]}).json()
    assert child["parent_id"] == unit["id"]
    etag = jefe.get(f"/api/v1/hr/org-units/{unit['id']}")["ETag"]
    cycle = jefe.patch(f"/api/v1/hr/org-units/{unit['id']}", {"parent_id": child["id"]}, HTTP_IF_MATCH=etag)
    assert cycle.status_code == 422
    pos = jefe.post("/api/v1/hr/positions", {"code": "ANA", "name": "Analista", "org_unit_id": unit["id"]})
    assert pos.status_code == 201 and pos.json()["org_unit_id"] == unit["id"]
    assert jefe.post("/api/v1/hr/positions", {"code": "ana", "name": "Otro"}).status_code == 422  # case-insensitive
    foreign = jefe.post("/api/v1/hr/positions", {"code": "X", "name": "X", "org_unit_id": str(uuid.uuid4())})
    assert foreign.status_code == 422


def test_P13a_payroll_interface_returns_payroll_contracts_with_approved_hours(employees_world):
    w = employees_world
    src = services.get(PayrollEmployeeSource)
    assert isinstance(src, DbPayrollEmployeeSource) and isinstance(src, PayrollEmployeeSource)
    world = w["world"]
    with tenancy.scope(world.ctx(["ALFA"])):
        Timesheet.objects.create(
            tenant_id=world.tenant_id, company_id=world.companies["ALFA"], employee=w["a"], number="TS-1",
            document_date=dt.date(2026, 10, 31), period="2026-10", status="APPROVED",
            hours={"overtime_day": "3.5", "ordinary_night": "2"},
        )  # fmt: skip
        Timesheet.objects.create(
            tenant_id=world.tenant_id, company_id=world.companies["ALFA"], employee=w["b"], number="TS-2",
            document_date=dt.date(2026, 10, 31), period="2026-10", status="SUBMITTED", hours={"overtime_day": "9"},
        )  # fmt: skip
        rows = src.employees_for_period(world.companies["ALFA"], dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    by_emp = {r.employee_id: r for r in rows}
    assert set(by_emp) == {w["a"].id, w["b"].id}  # C is another company
    assert by_emp[w["a"].id].salary == Decimal("2500000.0000") and by_emp[w["a"].id].contract_type == "INDEFINIDO"
    assert by_emp[w["a"].id].hours == {"overtime_day": Decimal("3.5"), "ordinary_night": Decimal("2")}
    assert by_emp[w["b"].id].hours == {}  # only APPROVED timesheets
    with tenancy.scope(world.ctx(["ALFA"])):
        only = src.employees_for_period(
            world.companies["ALFA"], dt.date(2026, 10, 1), dt.date(2026, 10, 31), [w["b"].id]
        )
    assert [r.employee_id for r in only] == [w["b"].id]


def test_P13a_permissions_deny_by_default(employees_world, world, login):
    world.add_user("nadie", ["ALFA"], subject="nadie-" + str(world.tenant_id))
    nobody = login("nadie", "ALFA")
    assert nobody.get("/api/v1/hr/employees").status_code == 403
    r = nobody.post("/api/v1/hr/employees", EMP)
    assert r.status_code == 403
    assert nobody.get(f"/api/v1/hr/employees/{employees_world['a'].id}").status_code in (403, 404)
