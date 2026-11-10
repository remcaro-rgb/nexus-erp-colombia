"""Vacation balance (KPI ``ep_dias_vacaciones``) and request days.

* accrued = commercial days of service (30/360 convention, first contract start → ``on``) ×
  ``hr.vacaciones.dias_habiles_anio`` / 360 — the annual entitlement is a legal parameter read through
  ``kernel.parameters`` (UNKNOWN fails closed; fixture values are labelled NOT LEGALLY VERIFIED);
* taken = APPROVED vacation days whose last day is before ``on``; approved pending = APPROVED, not yet taken;
* available = accrued − taken − approved pending (the KPI); a new request must also fit the SUBMITTED ones;
* request days = business days (Mon–Fri, Colombian holidays excluded: :mod:`localization_co.holidays`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

from hr import services
from hr.models import Employee
from localization_co.holidays import is_business_day

PARAM_ANNUAL_DAYS = "hr.vacaciones.dias_habiles_anio"
TWO = Decimal("0.01")


def days360(start: dt.date, end: dt.date) -> int:
    """Commercial days from ``start`` to ``end`` inclusive (30/360)."""
    d1, d2 = min(start.day, 30), min(end.day, 30)
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1) + 1


def business_days(start: dt.date, end: dt.date) -> int:
    n, cur = 0, start
    while cur <= end:
        n += is_business_day(cur)
        cur += dt.timedelta(days=1)
    return n


@dataclass(frozen=True, slots=True)
class Balance:
    accrued: Decimal
    taken: Decimal
    approved_pending: Decimal
    submitted: Decimal
    parameter: dict[str, Any]

    @property
    def available(self) -> Decimal:
        return self.accrued - self.taken - self.approved_pending


def balance(employee: Employee, on: dt.date, exclude_id: Any = None) -> Balance:
    from timekeeping.models import VACATION, Absence

    first = services.first_contract(employee)
    param = services.parameter(PARAM_ANNUAL_DAYS, on, employee.company_id)
    accrued = Decimal(0)
    if first is not None and first.start_date <= on:
        latest = employee.contracts.filter(status__in=("APPROVED", "CANCELLED")).order_by("-start_date").first()
        ends = (latest.termination_date, latest.end_date) if latest is not None else ()
        last = min(d for d in (on, *ends) if d is not None)
        service = days360(first.start_date, last) if last >= first.start_date else 0
        accrued = (Decimal(service) * Decimal(str(param.value)) / Decimal(360)).quantize(TWO, ROUND_DOWN)
    taken = approved = submitted = Decimal(0)
    rows = Absence.objects.filter(employee=employee, absence_type=VACATION, status__in=("APPROVED", "SUBMITTED"))
    if exclude_id is not None:
        rows = rows.exclude(pk=exclude_id)
    for a in rows:
        if a.status == "SUBMITTED":
            submitted += a.days
        elif a.end_date < on:
            taken += a.days
        else:
            approved += a.days
    return Balance(accrued, taken, approved, submitted, param.snapshot())


def request_status(absence: Any, on: dt.date) -> str:
    if absence.status == "APPROVED" and absence.end_date < on:
        return "TAKEN"
    return str(absence.status)
