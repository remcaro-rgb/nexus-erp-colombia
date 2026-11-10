"""Time keeping API (``/api/v1/timekeeping/*``, contract ``contracts/openapi/modules/timekeeping.yaml``)."""

from __future__ import annotations

from typing import Any

from django.urls import URLPattern

from hr.api.crud import Action, CollectionAction, Spec, audit_dict, routes
from timekeeping import services
from timekeeping.models import Absence, ClockEvent, OvertimeRequest, Shift, Timesheet


def _doc(d: Any) -> dict[str, Any]:
    return {
        "id": d.id,
        "tenant_id": d.tenant_id,
        "company_id": d.company_id,
        "number": d.number,
        "document_date": d.document_date,
        "posting_date": d.posting_date,
        "status": d.status,
        "currency": d.currency,
        "total": None,
        "notes": d.notes,
        "source": None,
        "employee_id": str(d.employee_id),
        "lines": [],
        "version": d.version,
        "audit": audit_dict(d),
    }


def absence_dict(a: Absence) -> dict[str, Any]:
    return {
        **_doc(a),
        "absence_type": a.absence_type,
        "start_date": a.start_date,
        "end_date": a.end_date,
        "days": format(a.days.normalize(), "f"),
        "medical_note": a.medical_note,
    }


def overtime_dict(o: OvertimeRequest) -> dict[str, Any]:
    return {**_doc(o), "hours": format(o.hours.normalize(), "f")}


def timesheet_dict(t: Timesheet) -> dict[str, Any]:
    return {**_doc(t), "period": t.period, "hours": t.hours, "warnings": t.warnings, "parameters": t.parameters}


def clock_event_dict(e: ClockEvent) -> dict[str, Any]:
    return {
        "id": e.id, "tenant_id": e.tenant_id, "company_id": e.company_id, "status": e.status,
        "employee_id": str(e.employee_id), "event_type": e.event_type, "at": e.at, "device": e.device,
        "method": e.method, "geo": e.geo, "version": e.version, "audit": audit_dict(e),
    }  # fmt: skip


def shift_dict(s: Shift) -> dict[str, Any]:
    return {
        "id": s.id, "tenant_id": s.tenant_id, "company_id": s.company_id, "code": s.code, "name": s.name,
        "status": s.status, "start_time": s.start_time.strftime("%H:%M"), "end_time": s.end_time.strftime("%H:%M"),
        "version": s.version, "audit": audit_dict(s),
    }  # fmt: skip


DOC_FILTERS = {"status": "status", "employee_id": "employee_id", "document_date": "document_date"}
DOC_SORTS = ("number", "document_date", "status")

CLOCK_EVENTS = Spec(
    resource="timekeeping.clock_event",
    url="api/v1/timekeeping/clock-events",
    model=ClockEvent,
    to_dict=clock_event_dict,
    create=services.create_clock_event,
    input_fields=("employee_id", "event_type", "device", "geo"),
    filters={"employee_id": "employee_id", "event_type": "event_type", "at": "at"},
    sorts=("at",),
    collection_actions={
        "clock-in": CollectionAction("timekeeping.clock_event.create", services.clock_in),
        "clock-out": CollectionAction("timekeeping.clock_event.create", services.clock_out),
    },
)
SHIFTS = Spec(
    resource="timekeeping.shift",
    url="api/v1/timekeeping/shifts",
    model=Shift,
    to_dict=shift_dict,
    create=services.create_shift,
    update=services.update_shift,
    delete=services.hr.archive,
    input_fields=("code", "name", "start_time", "end_time"),
    filters={"code": "code", "status": "status"},
    sorts=("code", "name"),
    search=("code", "name"),
)
TIMESHEETS = Spec(
    resource="timekeeping.timesheet",
    url="api/v1/timekeeping/timesheets",
    model=Timesheet,
    to_dict=timesheet_dict,
    create=services.create_timesheet,
    update=services.update_timesheet,
    delete=services.delete_draft,
    input_fields=services.TIMESHEET_FIELDS,
    filters={**DOC_FILTERS, "period": "period"},
    sorts=(*DOC_SORTS, "period"),
    actions={
        "submit": Action("timekeeping.timesheet.update", services.submit_timesheet),
        "approve": Action("timekeeping.timesheet.approve", services.approve_timesheet),
    },
)
OVERTIME = Spec(
    resource="timekeeping.overtime_request",
    url="api/v1/timekeeping/overtime-requests",
    model=OvertimeRequest,
    to_dict=overtime_dict,
    create=services.create_overtime,
    update=services.update_overtime,
    delete=services.delete_draft,
    input_fields=services.OVERTIME_FIELDS,
    filters=DOC_FILTERS,
    sorts=DOC_SORTS,
    actions={
        "submit": Action("timekeeping.overtime_request.update", services.submit_doc),
        "approve": Action("timekeeping.overtime_request.approve", services.approve_doc),
        "reject": Action("timekeeping.overtime_request.approve", services.reject_doc),
    },
)
ABSENCES = Spec(
    resource="timekeeping.absence",
    url="api/v1/timekeeping/absences",
    model=Absence,
    to_dict=absence_dict,
    create=services.create_absence,
    update=services.update_absence,
    delete=services.delete_draft,
    input_fields=services.ABSENCE_FIELDS,
    filters={**DOC_FILTERS, "absence_type": "absence_type", "start_date": "start_date"},
    sorts=(*DOC_SORTS, "start_date"),
    select_related=("employee",),
    actions={
        "submit": Action("timekeeping.absence.update", services.submit_absence),
        "approve": Action("timekeeping.absence.approve", services.approve_absence),
        "reject": Action("timekeeping.absence.approve", services.reject_absence),
    },
)


def all_routes() -> list[URLPattern]:
    return [*routes(CLOCK_EVENTS), *routes(SHIFTS), *routes(TIMESHEETS), *routes(OVERTIME), *routes(ABSENCES)]
