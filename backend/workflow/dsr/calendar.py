"""Business-day arithmetic for Ley 1581 SLAs over a holiday calendar.

The holiday calendar is owned by P06 (``tax`` localization). Until it is
registered with ``kernel.services`` under :class:`HolidayCalendar`, callers
must pass one explicitly; :class:`FixtureHolidayCalendar` holds labelled
synthetic dates for tests (NOT LEGALLY VERIFIED). No date is invented here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from kernel import services
from kernel.errors import ParameterUnknown

FIXTURE_LABEL = "FIXTURE — NOT LEGALLY VERIFIED"


@runtime_checkable
class HolidayCalendar(Protocol):
    def is_holiday(self, day: dt.date) -> bool: ...


class FixtureHolidayCalendar:
    label = FIXTURE_LABEL

    def __init__(self, days: Iterable[dt.date]) -> None:
        self.days = frozenset(days)

    def is_holiday(self, day: dt.date) -> bool:
        return day in self.days


def calendar() -> HolidayCalendar:
    cal = services.optional(HolidayCalendar)
    if cal is None:
        raise ParameterUnknown("no holiday calendar registered (P06); business-day SLAs fail closed")
    return cal


def is_business_day(day: dt.date, cal: HolidayCalendar) -> bool:
    return day.weekday() < 5 and not cal.is_holiday(day)


def add_business_days(start: dt.date, days: int, cal: HolidayCalendar) -> dt.date:
    """The ``days``-th business day after ``start`` (the receipt day itself is not counted)."""
    if days < 0:
        raise ValueError("days must be >= 0")
    current, left = start, days
    while left > 0:
        current += dt.timedelta(days=1)
        if is_business_day(current, cal):
            left -= 1
    return current


def business_days_between(start: dt.date, end: dt.date, cal: HolidayCalendar) -> int:
    n, current = 0, start
    while current < end:
        current += dt.timedelta(days=1)
        if is_business_day(current, cal):
            n += 1
    return n
