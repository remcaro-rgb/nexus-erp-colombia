"""Time keeping API: clock marks (biometric consent + alternatives), timesheet classification and approval,
overtime and absences (SoD, states)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection

from kernel import tenancy
from timekeeping.models import ClockEvent

pytestmark = pytest.mark.django_db


def _mark(api, action, employee, **params):
    return api.post(
        f"/api/v1/timekeeping/clock-events:{action}", {"parameters": {"employee_id": str(employee.id), **params}}
    )


def _past_marks(w, emp, start, end):
    """Historical marks of October (the API stamps marks with the current time)."""
    world = w["world"]
    with tenancy.scope(world.ctx(["ALFA"])):
        for kind, at in (("IN", start), ("OUT", end)):
            ClockEvent.objects.create(
                tenant_id=world.tenant_id, company_id=world.companies["ALFA"], employee=emp, event_type=kind, at=at
            )


def test_P13a_clock_marks_alternate_and_biometric_needs_consent(employees_world):
    w = employees_world
    jefe, a = w["jefe"], w["a"]
    r = _mark(jefe, "clock-in", a, method="BIOMETRIC", biometric_match=True)
    assert r.status_code == 422 and r.json()["code"] == "consent_required"
    # the non-biometric alternative is always available
    assert _mark(jefe, "clock-in", a, method="KIOSK_PIN", device="kiosco-1").status_code == 200
    again = _mark(jefe, "clock-in", a)
    assert again.status_code == 409 and again.json()["code"] == "invalid_state_transition"
    assert _mark(jefe, "clock-out", a).status_code == 200
    # with explicit consent biometric works and only the device-side match result is kept
    etag = jefe.get(f"/api/v1/hr/employees/{a.id}")["ETag"]
    assert (
        jefe.patch(f"/api/v1/hr/employees/{a.id}", {"biometric_consent": True}, HTTP_IF_MATCH=etag).status_code == 200
    )
    r = _mark(jefe, "clock-in", a, method="BIOMETRIC", biometric_match=True)
    assert r.status_code == 200 and r.json()["event_type"] == "IN"
    events = jefe.get("/api/v1/timekeeping/clock-events").json()["items"]
    assert [e["event_type"] for e in events] == ["IN", "OUT", "IN"]


def test_P13a_timesheet_classifies_with_parameters_and_approval_emits_event(employees_world, parameters, login):
    w = employees_world
    jefe, a = w["jefe"], w["a"]
    _past_marks(w, a, dt.datetime(2026, 10, 5, 13, 0, tzinfo=dt.UTC), dt.datetime(2026, 10, 5, 23, 0, tzinfo=dt.UTC))
    ts = jefe.post(
        "/api/v1/timekeeping/timesheets", {"document_date": "2026-10-31", "employee_id": str(a.id), "period": "2026-10"}
    )
    assert ts.status_code == 201, ts.content
    tid = ts.json()["id"]
    sub = jefe.post(f"/api/v1/timekeeping/timesheets/{tid}:submit", {})
    assert sub.status_code == 200, sub.content
    # 08:00–18:00 Bogotá on a Monday: 8 ordinary + 2 overtime (fixture ordinary hours 8)
    assert sub.json()["hours"] == {"ordinary_day": "8.000000", "overtime_day": "2.000000"}
    assert {p["code"] for p in sub.json()["parameters"]} >= {
        "hr.jornada.nocturna_inicio",
        "hr.jornada.horas_diarias_ordinarias",
    }
    # SoD: the submitter cannot approve
    own = jefe.post(f"/api/v1/timekeeping/timesheets/{tid}:approve", {})
    assert own.status_code == 403 and own.json()["code"] == "sod_violation"
    w["world"].add_user("sup", ["ALFA"], subject="sup-" + str(w["world"].tenant_id))
    w["world"].assign("sup", "jefe_rrhh", "ALFA")
    ok = login("sup", "ALFA").post(f"/api/v1/timekeeping/timesheets/{tid}:approve", {})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT envelope->'payload'->>'timesheet_id' FROM kernel_outbox"
            " WHERE event_type = 'timekeeping.timesheet.approved.v1'"
        )
        assert [r[0] for r in cur.fetchall()] == [tid]


def test_P13a_timesheet_fails_closed_without_parameters(employees_world, parameters):
    w = employees_world
    parameters.values.pop("hr.jornada.nocturna_inicio")
    jefe = w["jefe"]
    _past_marks(
        w, w["a"], dt.datetime(2026, 10, 6, 13, 0, tzinfo=dt.UTC), dt.datetime(2026, 10, 6, 15, 0, tzinfo=dt.UTC)
    )
    tid = jefe.post(
        "/api/v1/timekeeping/timesheets",
        {"document_date": "2026-10-31", "employee_id": str(w["a"].id), "period": "2026-10"},
    ).json()["id"]
    r = jefe.post(f"/api/v1/timekeeping/timesheets/{tid}:submit", {})
    assert r.status_code == 422 and r.json()["code"] == "parameter_unknown"


def test_P13a_overtime_and_absence_lifecycle(employees_world):
    w = employees_world
    jefe = w["jefe"]
    o = jefe.post(
        "/api/v1/timekeeping/overtime-requests",
        {"document_date": "2026-10-01", "employee_id": str(w["a"].id), "hours": "2.5"},
    )
    assert o.status_code == 201 and o.json()["hours"] == "2.5" and o.json()["status"] == "DRAFT"
    assert (
        jefe.post(
            "/api/v1/timekeeping/overtime-requests",
            {"document_date": "2026-10-01", "employee_id": str(w["a"].id), "hours": 2.5},
        ).status_code
        == 422
    )
    oid = o.json()["id"]
    assert jefe.post(f"/api/v1/timekeeping/overtime-requests/{oid}:approve", {}).status_code == 409
    assert jefe.post(f"/api/v1/timekeeping/overtime-requests/{oid}:submit", {}).json()["status"] == "SUBMITTED"
    etag = jefe.get(f"/api/v1/timekeeping/overtime-requests/{oid}")["ETag"]
    assert (
        jefe.patch(f"/api/v1/timekeeping/overtime-requests/{oid}", {"hours": "3"}, HTTP_IF_MATCH=etag).status_code
        == 409
    )
    a = jefe.post("/api/v1/timekeeping/absences", {
        "document_date": "2026-10-01", "employee_id": str(w["a"].id), "absence_type": "INCAPACIDAD_GENERAL",
        "start_date": "2026-10-01", "end_date": "2026-10-03", "medical_note": "nota sintética",
    })  # fmt: skip
    assert a.status_code == 201 and a.json()["days"] == "3" and a.json()["medical_note"] == "nota sintética"
    aid = a.json()["id"]
    assert jefe.delete(f"/api/v1/timekeeping/absences/{aid}").status_code == 204
    assert jefe.get(f"/api/v1/timekeeping/absences/{aid}").status_code == 404


def test_P13a_team_manager_sees_absences_but_not_medical_notes(employees_world, world, login):
    world.add_role("lider_equipo", ["timekeeping.absence.read"])
    world.add_user("lider2", ["ALFA"], subject="lider2-" + str(world.tenant_id))
    world.assign("lider2", "lider_equipo", "ALFA")
    jefe = employees_world["jefe"]
    jefe.post("/api/v1/timekeeping/absences", {
        "document_date": "2026-10-01", "employee_id": str(employees_world["a"].id),
        "absence_type": "INCAPACIDAD_GENERAL",
        "start_date": "2026-10-01", "end_date": "2026-10-02", "medical_note": "diagnóstico sintético",
    })  # fmt: skip
    items = login("lider2", "ALFA").get("/api/v1/timekeeping/absences").json()["items"]
    assert items and all("medical_note" not in i for i in items)
    assert "diagnóstico" not in str(items)
