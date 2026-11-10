"""Time keeping tables (owner P13; this part P13a).

* :class:`Shift` — shift templates (start/end local time; crossing midnight allowed).
* :class:`ClockEvent` — IN/OUT marks (web, kiosk PIN/QR, mobile, biometric only with explicit consent;
  device-side matching: only the match result is kept, never a template — R-DP-01).
* :class:`Absence` — absences including vacations (``VACACIONES``); DRAFT → SUBMITTED → APPROVED | REJECTED.
  A vacation request from the employee portal is created SUBMITTED; an approved vacation whose last day is in
  the past is reported as TAKEN by the portal.
* :class:`OvertimeRequest` — DRAFT → SUBMITTED → APPROVED | REJECTED.
* :class:`Timesheet` — per employee and month; ``hours`` holds the classified hours
  (:mod:`timekeeping.classification`) computed on submit; APPROVED hours feed payroll through the HR interface.
"""

from __future__ import annotations

from django.db import models

from hr.models import DOC_STATUSES, MASTER_STATUSES, Employee
from kernel import clock
from kernel.fields import CurrencyField, QtyField
from kernel.models import AuditedModel, CompanyModel

ABSENCE_TYPES = (
    "VACACIONES",
    "INCAPACIDAD_GENERAL",
    "INCAPACIDAD_LABORAL",
    "LICENCIA_MATERNIDAD",
    "LICENCIA_PATERNIDAD",
    "LICENCIA_REMUNERADA",
    "LICENCIA_NO_REMUNERADA",
    "CALAMIDAD",
)
VACATION = "VACACIONES"
CLOCK_METHODS = ("WEB", "KIOSK_PIN", "KIOSK_QR", "MOBILE", "BIOMETRIC")


def _choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(v, v) for v in values]


class Shift(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MASTER_STATUSES, default="ACTIVE")
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        db_table = "timekeeping_shift"


class ClockEvent(CompanyModel, AuditedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="clock_events")
    event_type = models.CharField(max_length=3, choices=[("IN", "IN"), ("OUT", "OUT")])
    at = models.DateTimeField(default=clock.now)
    method = models.CharField(max_length=12, choices=_choices(CLOCK_METHODS), default="WEB")
    device = models.CharField(max_length=120, null=True)
    geo = models.CharField(max_length=60, null=True)  # sensitive-pii; only when geofencing is enabled
    biometric_match = models.BooleanField(null=True)  # device-side match result only
    status = models.CharField(max_length=10, default="ACTIVE")

    class Meta:
        db_table = "timekeeping_clock_event"


class _Document(CompanyModel, AuditedModel):
    number = models.CharField(max_length=40)
    document_date = models.DateField()
    posting_date = models.DateField(null=True)
    status = models.CharField(max_length=10, choices=_choices(DOC_STATUSES), default="DRAFT")
    currency = CurrencyField(default="COP")
    notes = models.TextField(blank=True, default="")
    submitted_by = models.UUIDField(null=True)
    submitted_at = models.DateTimeField(null=True)
    decided_by = models.UUIDField(null=True)
    decided_at = models.DateTimeField(null=True)
    decision_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        abstract = True


class Absence(_Document):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="absences")
    absence_type = models.CharField(max_length=30, choices=_choices(ABSENCE_TYPES))
    start_date = models.DateField()
    end_date = models.DateField()
    days = QtyField(default=0)  # business days for vacations, calendar days otherwise
    medical_note = models.TextField(null=True)  # sensitive-pii
    channel = models.CharField(max_length=10, default="internal")  # internal | portal

    class Meta:
        db_table = "timekeeping_absence"


class OvertimeRequest(_Document):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="overtime_requests")
    hours = QtyField()

    class Meta:
        db_table = "timekeeping_overtime_request"


class Timesheet(_Document):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="timesheets")
    period = models.CharField(max_length=7)  # YYYY-MM
    hours = models.JSONField(default=dict)  # {bucket: "decimal string"}
    warnings = models.JSONField(default=list)
    parameters = models.JSONField(default=list)  # parameter snapshots used by the classification

    class Meta:
        db_table = "timekeeping_timesheet"
