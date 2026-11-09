"""Tax calendar: due dates by obligation and NIT last digit(s) (R-TX-07, yearly data import).

``due_dates`` JSON::

    {"periods": [{"period": "2026-01", "by_digit": {"0": "2026-02-10", "1": "2026-02-11", ...}}]}

Keys of ``by_digit`` are the last one or two digits of the NIT (without the DV).
The yearly calendar decree is an external input (S-11 not located): the data is
imported by the tenant; nothing is pre-loaded as official. Reminders go through
the P07 ``SchedulerService`` (a daily job per calendar).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.scheduler import SchedulerService
from kernel.tenancy import TenantContext
from localization_co import nit
from tax.models import TaxCalendar

REMINDER_TASK = "tax.calendar.remind"


def validate_due_dates(due_dates: Any, year: int | None = None) -> None:
    periods = due_dates.get("periods") if isinstance(due_dates, dict) else None
    if not isinstance(periods, list):
        raise ValidationFailed("due_dates.periods must be a list", field="due_dates")
    for i, period in enumerate(periods):
        by_digit = period.get("by_digit") if isinstance(period, dict) else None
        if not isinstance(period, dict) or not isinstance(period.get("period"), str) or not isinstance(by_digit, dict):
            raise ValidationFailed(f"due_dates.periods[{i}] needs period and by_digit", field="due_dates")
        for digits, date in by_digit.items():
            if not (isinstance(digits, str) and digits.isdigit() and 1 <= len(digits) <= 2):
                raise ValidationFailed(f"due_dates.periods[{i}]: key {digits!r} is not 1-2 digits", field="due_dates")
            try:
                dt.date.fromisoformat(str(date))
            except ValueError as exc:
                raise ValidationFailed(f"due_dates.periods[{i}]: {date!r} is not a date", field="due_dates") from exc


def due_date(calendar: TaxCalendar, period: str, nit_number: str) -> dt.date:
    digits = nit.normalize(nit_number)
    for row in calendar.due_dates.get("periods", []):
        if row.get("period") != period:
            continue
        by_digit = row.get("by_digit", {})
        for width in (2, 1):
            key = digits[-width:]
            if key in by_digit:
                return dt.date.fromisoformat(by_digit[key])
    raise NotFound(f"no due date for period {period} and NIT ending {digits[-2:]}")


def upcoming(calendar: TaxCalendar, nit_number: str, today: dt.date, days: int = 15) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in calendar.due_dates.get("periods", []):
        try:
            date = due_date(calendar, row["period"], nit_number)
        except NotFound:
            continue
        if today <= date <= today + dt.timedelta(days=days):
            out.append({"obligation": calendar.obligation, "period": row["period"], "due_date": date.isoformat()})
    return out


def schedule_reminders(scheduler: SchedulerService, ctx: TenantContext, company_id: uuid.UUID,
                       calendar: TaxCalendar) -> Any:  # fmt: skip
    """One daily job (07:00 Bogotá) per calendar; the task notifies due dates within 15 days."""
    return scheduler.schedule(
        ctx,
        company_id,
        f"tax-calendar-{calendar.code}-{calendar.year}",
        "0 7 * * *",
        REMINDER_TASK,
        {"calendar_id": str(calendar.id), "days": 15},
    )
