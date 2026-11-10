"""T-PORTAL-EMP-02 (integration, G2): the vacation request approval flow updates the balance KPI."""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_DOWN, Decimal

import pytest
from django.db import connection

from hr.vacations import days360
from kernel import clock

pytestmark = pytest.mark.django_db


def _kpis(api) -> dict[str, dict]:
    r = api.get("/api/portal/v1/employee/kpis")
    assert r.status_code == 200, r.content
    return {k["id"]: k for k in r.json()["kpis"]}


def _accrued(start: dt.date, on: dt.date) -> Decimal:
    return (Decimal(days360(start, on)) * Decimal(15) / Decimal(360)).quantize(Decimal("0.01"), ROUND_DOWN)


def test_T_PORTAL_EMP_02_vacation_request_approval_updates_balance_kpi(employees_world, payslips):
    w = employees_world
    api, jefe = w["api_a"], w["jefe"]
    today = clock.business_date()
    accrued = _accrued(dt.date(2024, 1, 2), today)

    k = _kpis(api)
    assert k["ep_dias_vacaciones"]["value"] == str(accrued) and k["ep_dias_vacaciones"]["unit"] == "days"
    assert k["ep_solicitudes_pendientes"]["value"] == "0"
    assert k["ep_ultimo_pago"]["state"] == "no_data"

    # submit 5 business days (Mon 2026-12-14 … Fri 2026-12-18)
    r = api.post("/api/portal/v1/employee/vacations", {"start_date": "2026-12-14", "end_date": "2026-12-18"})
    assert r.status_code == 201, r.content
    req = r.json()
    assert req["days"] == "5" and req["request_status"] == "SUBMITTED" and r["Location"].endswith(req["id"])
    k = _kpis(api)
    assert k["ep_dias_vacaciones"]["value"] == str(accrued)  # SUBMITTED does not reduce the balance
    assert k["ep_solicitudes_pendientes"]["value"] == "1"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT envelope->'payload'->>'days' FROM kernel_outbox"
            " WHERE event_type = 'portal.vacation_request.submitted.v1'"
        )
        assert [row[0] for row in cur.fetchall()] == ["5"]

    # holidays are not counted: Mon 2026-12-07 … Wed 2026-12-09 holds 2026-12-08 (holiday) → 2 days
    r2 = api.post("/api/portal/v1/employee/vacations", {"start_date": "2026-12-07", "end_date": "2026-12-09"})
    assert r2.status_code == 201 and r2.json()["days"] == "2"
    # more than the balance (accrued − submitted) → vacation_balance_insufficient
    r3 = api.post("/api/portal/v1/employee/vacations", {"start_date": "2027-01-04", "end_date": "2027-03-31"})
    assert r3.status_code == 422 and r3.json()["code"] == "vacation_balance_insufficient"
    # the portal cannot approve (portal tokens are refused by the internal API)
    assert api.post(f"/api/v1/timekeeping/absences/{req['id']}:approve", {}).status_code == 401

    # the manager (internal user with timekeeping.absence.approve) approves → balance KPI drops by 5
    ok = jefe.post(f"/api/v1/timekeeping/absences/{req['id']}:approve", {"comment": "aprobado"})
    assert ok.status_code == 200, ok.content
    assert ok.json()["status"] == "APPROVED"
    k = _kpis(api)
    assert k["ep_dias_vacaciones"]["value"] == str(accrued - 5)
    assert k["ep_solicitudes_pendientes"]["value"] == "1"
    mine = api.get(f"/api/portal/v1/employee/vacations/{req['id']}").json()
    assert mine["request_status"] == "APPROVED" and mine["status"] == "APPROVED"

    # rejection needs a reason; after rejection the request no longer counts as pending
    second = r2.json()["id"]
    bad = jefe.post(f"/api/v1/timekeeping/absences/{second}:reject", {})
    assert bad.status_code == 422 and bad.json()["code"] == "reason_required"
    assert jefe.post(f"/api/v1/timekeeping/absences/{second}:reject", {"reason": "cierre de año"}).status_code == 200
    k = _kpis(api)
    assert k["ep_solicitudes_pendientes"]["value"] == "0" and k["ep_dias_vacaciones"]["value"] == str(accrued - 5)
    assert api.get(f"/api/portal/v1/employee/vacations/{second}").json()["request_status"] == "REJECTED"
    # a decided request cannot be decided again
    again = jefe.post(f"/api/v1/timekeeping/absences/{req['id']}:approve", {})
    assert again.status_code == 409 and again.json()["code"] == "invalid_state_transition"

    # once the vacation is over it is TAKEN; the balance stays reduced (taken instead of approved pending)
    later = dt.datetime(2026, 12, 21, 15, 0, tzinfo=dt.UTC)
    with clock.frozen(later):
        assert api.get(f"/api/portal/v1/employee/vacations/{req['id']}").json()["request_status"] == "TAKEN"
        k = _kpis(api)
        assert k["ep_dias_vacaciones"]["value"] == str(_accrued(dt.date(2024, 1, 2), later.date()) - 5)

    listed = api.get("/api/portal/v1/employee/vacations").json()["items"]
    assert sorted(i["request_status"] for i in listed) == ["APPROVED", "REJECTED"]


def test_T_PORTAL_EMP_02_data_update_request_is_approved_by_hr(employees_world):
    w = employees_world
    api, jefe = w["api_a"], w["jefe"]
    r = api.post("/api/portal/v1/employee/data-updates", {"changes": {"phone": "+57 300 000 0000"}})
    assert r.status_code == 201, r.content
    assert r.json()["review_status"] == "SUBMITTED"
    assert _kpis(api)["ep_solicitudes_pendientes"]["value"] == "1"
    # salary / bank account cannot be changed from the portal
    bad = api.post("/api/portal/v1/employee/data-updates", {"changes": {"bank_account": "123"}})
    assert bad.status_code == 403 and bad.json()["code"] == "field_forbidden"
    ok = jefe.post(f"/api/v1/hr/data-updates/{r.json()['id']}:approve", {})
    assert ok.status_code == 200, ok.content
    assert ok.json()["review_status"] == "APPROVED"
    assert jefe.get(f"/api/v1/hr/employees/{w['a'].id}").json()["phone"] == "+57 300 000 0000"
    assert _kpis(api)["ep_solicitudes_pendientes"]["value"] == "0"
    assert api.get(f"/api/portal/v1/employee/data-updates/{r.json()['id']}").json()["review_status"] == "APPROVED"


def test_T_PORTAL_EMP_02_unknown_entitlement_parameter_fails_closed(employees_world, parameters):
    w = employees_world
    parameters.values.pop("hr.vacaciones.dias_habiles_anio")
    assert _kpis(w["api_a"])["ep_dias_vacaciones"]["state"] == "not_available"
    r = w["api_a"].post("/api/portal/v1/employee/vacations", {"start_date": "2026-12-14", "end_date": "2026-12-18"})
    assert r.status_code == 422 and r.json()["code"] == "parameter_unknown"
