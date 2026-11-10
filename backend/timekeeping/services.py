"""Time keeping services (every write inside the caller's unit of work).

* Clock events: ``:clock-in`` / ``:clock-out`` alternate per employee (``invalid_state_transition`` otherwise).
  ``BIOMETRIC`` needs the employee's explicit biometric consent (``consent_required``) and only the device-side
  match result is stored; WEB / KIOSK_PIN / KIOSK_QR / MOBILE are always available (R-DP-01).
* Absences / overtime / timesheets: DRAFT → (submit) SUBMITTED → (approve | reject with reason) APPROVED |
  REJECTED; only DRAFT documents can be edited or deleted; the approver is never the submitter (SoD).
  Vacations (``VACACIONES``) count business days and must fit the balance (``vacation_balance_insufficient``,
  re-checked under the employee row lock on approval).
* Timesheet submit classifies the month's clock intervals (:mod:`timekeeping.classification`) with the legal
  parameters of each date of work and stores the hours + parameter snapshots; approval emits
  ``timekeeping.timesheet.approved.v1`` (payroll reads approved hours through the HR interface).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from hr import services as hr
from hr import vacations
from hr.errors import ConsentRequired, InvalidStateTransition, ReasonRequired, VacationBalanceInsufficient
from hr.models import Employee
from iam.errors import SodViolation
from kernel import clock
from kernel.errors import ValidationFailed
from kernel.money import q_qty
from kernel.uow import UnitOfWork
from localization_co.holidays import is_holiday
from timekeeping import classification
from timekeeping.models import ABSENCE_TYPES, CLOCK_METHODS, VACATION, Absence, ClockEvent, OvertimeRequest, Shift, Timesheet

DOC_FIELDS = ("document_date", "posting_date", "currency", "notes", "lines")
ABSENCE_FIELDS = (*DOC_FIELDS, "employee_id", "absence_type", "start_date", "end_date", "medical_note")
OVERTIME_FIELDS = (*DOC_FIELDS, "employee_id", "hours")
TIMESHEET_FIELDS = (*DOC_FIELDS, "employee_id", "period")
PARAM_NIGHT_START = "hr.jornada.nocturna_inicio"
PARAM_NIGHT_END = "hr.jornada.nocturna_fin"
PARAM_ORDINARY = "hr.jornada.horas_diarias_ordinarias"
PARAM_OVERTIME_MAX = "hr.jornada.horas_extra_max_dia"


def _employee(company_id: uuid.UUID, value: Any) -> Employee:
    emp = hr._same_company(Employee, hr.parse_uuid_field(value, "employee_id"), company_id, "employee_id")
    if emp is None:
        raise ValidationFailed("employee_id is required", fields=["employee_id"])
    return emp  # type: ignore[no-any-return]  # _same_company returns the model instance


def _number(model: Any, prefix: str, company_id: uuid.UUID, year: int) -> str:
    n = model.objects.filter(company_id=company_id, number__startswith=f"{prefix}-{year}-").count() + 1
    return f"{prefix}-{year}-{n:05d}"


def _doc_common(doc: Any, data: Mapping[str, Any]) -> None:
    for name in ("document_date", "posting_date"):
        if name in data:
            setattr(doc, name, hr.parse_date(data[name], name, required=name == "document_date"))
    if "notes" in data:
        doc.notes = hr._text(data["notes"], "notes", 2000) or ""
    if "currency" in data and data["currency"] != "COP":
        raise ValidationFailed("currency must be COP", fields=["currency"])
    if data.get("lines"):
        raise ValidationFailed("time documents carry no lines", fields=["lines"])


def _require_draft(doc: Any) -> None:
    if doc.status != "DRAFT":
        raise InvalidStateTransition("only DRAFT documents can be changed")


# --------------------------------------------------------------------------- shifts


def _shift_times(shift: Shift, data: Mapping[str, Any]) -> None:
    for name in ("start_time", "end_time"):
        if name in data:
            try:
                setattr(shift, name, classification.parse_hhmm(data[name]))
            except (ValueError, TypeError):
                raise ValidationFailed(f"{name} must be HH:MM", fields=[name]) from None


def create_shift(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Shift:
    for required in ("code", "name", "start_time", "end_time"):
        if data.get(required) in (None, ""):
            raise ValidationFailed(f"{required} is required", fields=[required])
    shift = Shift(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, code=hr._check_code(Shift, company_id, data["code"]),
        name=hr._text(data["name"], "name", 200, required=True) or "",
        created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    _shift_times(shift, data)
    shift.save()
    hr.audit(uow, "timekeeping.shift.created", "timekeeping.shift", shift)
    return shift


def update_shift(uow: UnitOfWork, shift: Shift, data: Mapping[str, Any]) -> None:
    if "code" in data:
        shift.code = hr._check_code(Shift, shift.company_id, data["code"], exclude=shift.pk)
    if "name" in data:
        shift.name = hr._text(data["name"], "name", 200, required=True) or ""
    _shift_times(shift, data)
    shift.save_versioned(uow.ctx.principal_id, ["code", "name", "start_time", "end_time"])
    hr.audit(uow, "timekeeping.shift.updated", "timekeeping.shift", shift)


# --------------------------------------------------------------------------- clock events


def _clock(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], event_type: str) -> ClockEvent:
    emp = _employee(company_id, data.get("employee_id"))
    method = str(data.get("method") or "WEB")
    if method not in CLOCK_METHODS:
        raise ValidationFailed(f"method must be one of {', '.join(CLOCK_METHODS)}", fields=["method"])
    match = None
    if method == "BIOMETRIC":
        if emp.biometric_consent_at is None:
            raise ConsentRequired("biometric clocking needs explicit consent; use WEB, KIOSK_PIN, KIOSK_QR or MOBILE")
        if not isinstance(data.get("biometric_match"), bool):
            raise ValidationFailed("biometric_match (device-side result) is required", fields=["biometric_match"])
        match = bool(data["biometric_match"])
        if not match:
            raise ValidationFailed("biometric match failed; use an alternative method", fields=["biometric_match"])
    last = ClockEvent.objects.filter(employee=emp, status="ACTIVE").order_by("-at", "-id").first()
    expected = "OUT" if last is not None and last.event_type == "IN" else "IN"
    if event_type != expected:
        raise InvalidStateTransition(f"the next mark of this employee must be {expected}")
    event = ClockEvent.objects.create(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, employee=emp, event_type=event_type, at=clock.now(),
        method=method, device=hr._text(data.get("device"), "device", 120), geo=hr._text(data.get("geo"), "geo", 60),
        biometric_match=match, created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    hr.audit(uow, f"timekeeping.clock_event.{event_type.lower()}", "timekeeping.clock_event", event)
    return event


def create_clock_event(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> ClockEvent:
    event_type = data.get("event_type")
    if event_type not in ("IN", "OUT"):
        raise ValidationFailed("event_type must be IN or OUT", fields=["event_type"])
    return _clock(uow, company_id, data, str(event_type))


def _action_params(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    params = payload.get("parameters") or {}
    if not isinstance(params, Mapping):
        raise ValidationFailed("parameters must be an object", fields=["parameters"])
    return params


def clock_in(uow: UnitOfWork, company_id: uuid.UUID, payload: Mapping[str, Any]) -> ClockEvent:
    return _clock(uow, company_id, _action_params(payload), "IN")


def clock_out(uow: UnitOfWork, company_id: uuid.UUID, payload: Mapping[str, Any]) -> ClockEvent:
    return _clock(uow, company_id, _action_params(payload), "OUT")


# --------------------------------------------------------------------------- absences (incl. vacations)


def _absence_values(a: Absence, data: Mapping[str, Any]) -> None:
    _doc_common(a, data)
    if "absence_type" in data:
        if data["absence_type"] not in ABSENCE_TYPES:
            raise ValidationFailed(f"absence_type must be one of {', '.join(ABSENCE_TYPES)}", fields=["absence_type"])
        a.absence_type = data["absence_type"]
    for name in ("start_date", "end_date"):
        if name in data:
            setattr(a, name, hr.parse_date(data[name], name, required=True))
    if "medical_note" in data:
        a.medical_note = hr._text(data["medical_note"], "medical_note", 4000)
    if a.end_date < a.start_date:
        raise ValidationFailed("end_date is before start_date", fields=["end_date"])
    if a.absence_type == VACATION:
        a.days = Decimal(vacations.business_days(a.start_date, a.end_date))
        if a.days <= 0:
            raise ValidationFailed("the vacation has no business days", fields=["start_date"])
    else:
        a.days = Decimal((a.end_date - a.start_date).days + 1)


def _check_vacation_balance(a: Absence) -> None:
    if a.absence_type != VACATION:
        return
    bal = vacations.balance(a.employee, clock.business_date(), exclude_id=a.pk)
    if a.days > bal.available - bal.submitted:
        raise VacationBalanceInsufficient(
            f"{a.days} days requested, {bal.available - bal.submitted} available", available=str(bal.available)
        )


def new_absence(uow: UnitOfWork, employee: Employee, data: Mapping[str, Any], *, channel: str = "internal") -> Absence:
    for required in ("absence_type", "start_date", "end_date"):
        if data.get(required) in (None, ""):
            raise ValidationFailed(f"{required} is required", fields=[required])
    today = clock.business_date()
    a = Absence(
        tenant_id=uow.ctx.tenant_id, company_id=employee.company_id, employee=employee,
        document_date=today, channel=channel, created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
        start_date=today, end_date=today,
    )  # fmt: skip
    _absence_values(a, {**data})
    a.number = _number(Absence, "AUS", employee.company_id, a.document_date.year)
    a.save()
    hr.audit(uow, "timekeeping.absence.created", "timekeeping.absence", a)
    return a


def create_absence(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Absence:
    if data.get("document_date") in (None, ""):
        raise ValidationFailed("document_date is required", fields=["document_date"])
    return new_absence(uow, _employee(company_id, data.get("employee_id")), data)


def update_absence(uow: UnitOfWork, a: Absence, data: Mapping[str, Any]) -> None:
    _require_draft(a)
    if "employee_id" in data and str(data["employee_id"]) != str(a.employee_id):
        raise ValidationFailed("the employee of an absence never changes", fields=["employee_id"])
    _absence_values(a, data)
    a.save_versioned(uow.ctx.principal_id, [f.name for f in a._meta.concrete_fields if f.name not in ("id", "version")])


def submit_absence(uow: UnitOfWork, a: Absence, payload: Mapping[str, Any] | None = None) -> None:
    _require_draft(a)
    _check_vacation_balance(a)
    _mark(uow, a, "SUBMITTED")
    if a.absence_type == VACATION:
        uow.emit(
            "portal.vacation_request.submitted.v1", {"request_id": str(a.id), "days": str(a.days)}, company_id=a.company_id
        )


def _decide(uow: UnitOfWork, doc: Any, approve: bool, payload: Mapping[str, Any]) -> None:
    if doc.status != "SUBMITTED":
        raise InvalidStateTransition("only SUBMITTED documents can be approved or rejected")
    if uow.ctx.principal_id in (doc.submitted_by, doc.created_by):
        raise SodViolation("the requester cannot approve or reject their own request")
    reason = str(payload.get("reason") or "").strip()
    if not approve and not reason:
        raise ReasonRequired("a rejection needs a reason")
    doc.decided_by, doc.decided_at, doc.decision_reason = uow.ctx.principal_id, clock.now(), reason[:255]
    doc.status = "APPROVED" if approve else "REJECTED"
    doc.save_versioned(uow.ctx.principal_id, ["status", "decided_by", "decided_at", "decision_reason"])
    hr.audit(uow, f"{doc._meta.db_table.replace('_', '.', 1)}.{doc.status.lower()}", "timekeeping", doc)


def _mark(uow: UnitOfWork, doc: Any, status: str) -> None:
    doc.status, doc.submitted_by, doc.submitted_at = status, uow.ctx.principal_id, clock.now()
    doc.save_versioned(uow.ctx.principal_id, ["status", "submitted_by", "submitted_at"])
    hr.audit(uow, f"{doc._meta.db_table.replace('_', '.', 1)}.submitted", "timekeeping", doc)


def approve_absence(uow: UnitOfWork, a: Absence, payload: Mapping[str, Any]) -> None:
    if a.absence_type == VACATION and a.status == "SUBMITTED":
        Employee.objects.select_for_update().filter(pk=a.employee_id).first()  # serialise balance checks
        bal = vacations.balance(a.employee, clock.business_date(), exclude_id=a.pk)
        if a.days > bal.available:
            raise VacationBalanceInsufficient(f"{a.days} days requested, {bal.available} available")
    _decide(uow, a, True, payload)


def reject_absence(uow: UnitOfWork, a: Absence, payload: Mapping[str, Any]) -> None:
    _decide(uow, a, False, payload)


def delete_draft(uow: UnitOfWork, doc: Any) -> None:
    _require_draft(doc)
    hr.audit(uow, f"{doc._meta.db_table.replace('_', '.', 1)}.deleted", "timekeeping", doc)
    doc.delete()


# --------------------------------------------------------------------------- overtime


def _hours(value: Any) -> Decimal:
    if isinstance(value, float) or value in (None, ""):
        raise ValidationFailed("hours must be a decimal string", fields=["hours"])
    try:
        hours = q_qty(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        raise ValidationFailed("hours must be a decimal string", fields=["hours"]) from None
    if hours <= 0 or hours > 24:
        raise ValidationFailed("hours must be between 0 and 24", fields=["hours"])
    return hours


def create_overtime(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> OvertimeRequest:
    emp = _employee(company_id, data.get("employee_id"))
    o = OvertimeRequest(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, employee=emp, hours=_hours(data.get("hours")),
        document_date=clock.business_date(), created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    _doc_common(o, data)
    if o.document_date is None:
        raise ValidationFailed("document_date is required", fields=["document_date"])
    o.number = _number(OvertimeRequest, "HE", company_id, o.document_date.year)
    o.save()
    hr.audit(uow, "timekeeping.overtime_request.created", "timekeeping.overtime_request", o)
    return o


def update_overtime(uow: UnitOfWork, o: OvertimeRequest, data: Mapping[str, Any]) -> None:
    _require_draft(o)
    if "employee_id" in data and str(data["employee_id"]) != str(o.employee_id):
        raise ValidationFailed("the employee never changes", fields=["employee_id"])
    _doc_common(o, data)
    if "hours" in data:
        o.hours = _hours(data["hours"])
    o.save_versioned(uow.ctx.principal_id, ["document_date", "posting_date", "notes", "hours"])


def submit_doc(uow: UnitOfWork, doc: Any, payload: Mapping[str, Any]) -> None:
    _require_draft(doc)
    _mark(uow, doc, "SUBMITTED")


def approve_doc(uow: UnitOfWork, doc: Any, payload: Mapping[str, Any]) -> None:
    _decide(uow, doc, True, payload)


def reject_doc(uow: UnitOfWork, doc: Any, payload: Mapping[str, Any]) -> None:
    _decide(uow, doc, False, payload)


# --------------------------------------------------------------------------- timesheets


def _period(value: Any) -> tuple[dt.date, dt.date]:
    try:
        year, month = (int(p) for p in str(value).split("-"))
        start = dt.date(year, month, 1)
    except ValueError:
        raise ValidationFailed("period must be YYYY-MM", fields=["period"]) from None
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return start, end


def create_timesheet(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Timesheet:
    emp = _employee(company_id, data.get("employee_id"))
    _period(data.get("period"))
    if Timesheet.objects.filter(employee=emp, period=data["period"]).exclude(status="REJECTED").exists():
        raise ValidationFailed("a timesheet for this period already exists", fields=["period"])
    t = Timesheet(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, employee=emp, period=str(data["period"]),
        document_date=clock.business_date(), created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    _doc_common(t, data)
    t.number = _number(Timesheet, "TS", company_id, t.document_date.year)
    t.save()
    hr.audit(uow, "timekeeping.timesheet.created", "timekeeping.timesheet", t)
    return t


def update_timesheet(uow: UnitOfWork, t: Timesheet, data: Mapping[str, Any]) -> None:
    _require_draft(t)
    if "employee_id" in data and str(data["employee_id"]) != str(t.employee_id):
        raise ValidationFailed("the employee never changes", fields=["employee_id"])
    _doc_common(t, data)
    if "period" in data:
        _period(data["period"])
        t.period = str(data["period"])
    t.save_versioned(uow.ctx.principal_id, ["document_date", "posting_date", "notes", "period"])


class _Rules:
    """Per-date legal parameters for the classification (cached per date; snapshots kept)."""

    def __init__(self, company_id: uuid.UUID) -> None:
        self.company_id = company_id
        self.cache: dict[dt.date, classification.DayRules] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}

    def _get(self, code: str, on: dt.date) -> Any:
        value = hr.parameter(code, on, self.company_id)
        snap = value.snapshot()
        self.snapshots[f"{code}@{snap['valid_from']}"] = snap
        return value.value

    def __call__(self, on: dt.date) -> classification.DayRules:
        if on not in self.cache:
            self.cache[on] = classification.DayRules(
                night_start=classification.parse_hhmm(self._get(PARAM_NIGHT_START, on)),
                night_end=classification.parse_hhmm(self._get(PARAM_NIGHT_END, on)),
                ordinary_hours=Decimal(str(self._get(PARAM_ORDINARY, on))),
                overtime_max=Decimal(str(self._get(PARAM_OVERTIME_MAX, on))),
            )
        return self.cache[on]


def classify_timesheet(t: Timesheet) -> classification.Classification:
    start, end = _period(t.period)
    tz = clock.tz()
    lo = clock.local_midnight_utc(start)
    hi = clock.local_midnight_utc(end + dt.timedelta(days=1))
    events = list(
        ClockEvent.objects.filter(employee=t.employee, status="ACTIVE", at__gte=lo, at__lt=hi).order_by("at", "id")
    )
    intervals, warnings, open_in = [], [], None
    for ev in events:
        if ev.event_type == "IN":
            open_in = ev.at.astimezone(tz)
        elif open_in is not None:
            intervals.append((open_in, ev.at.astimezone(tz)))
            open_in = None
    if open_in is not None:
        warnings.append("unclosed_clock_in")
    contract = hr.contract_in_force(t.employee, end) or hr.first_contract(t.employee)
    rest_weekday = contract.rest_day if contract is not None else 6
    rules = _Rules(t.company_id)
    result = classification.classify(intervals, rules, lambda d: d.weekday() == rest_weekday or is_holiday(d))
    t.parameters = sorted(rules.snapshots.values(), key=lambda s: (s["code"], str(s["valid_from"])))
    return classification.Classification(result.hours, (*result.warnings, *warnings))


def submit_timesheet(uow: UnitOfWork, t: Timesheet, payload: Mapping[str, Any]) -> None:
    _require_draft(t)
    result = classify_timesheet(t)
    t.hours = {k: str(v) for k, v in sorted(result.hours.items())}
    t.warnings = list(result.warnings)
    t.save_versioned(uow.ctx.principal_id, ["hours", "warnings", "parameters"])
    _mark(uow, t, "SUBMITTED")


def approve_timesheet(uow: UnitOfWork, t: Timesheet, payload: Mapping[str, Any]) -> None:
    _decide(uow, t, True, payload)
    uow.emit(
        "timekeeping.timesheet.approved.v1",
        {"timesheet_id": str(t.id), "employee_id": str(t.employee_id)},
        company_id=t.company_id,
    )
