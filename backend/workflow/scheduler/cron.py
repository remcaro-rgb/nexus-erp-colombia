"""5-field cron (minute hour day-of-month month day-of-week) evaluated in an IANA timezone.

DST-safe: wall-clock times that do not exist (spring-forward gap) run at the
first instant after the gap; ambiguous times (fall-back) run once (first
occurrence). Bogotá has no DST, other zones are supported.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kernel.errors import ValidationFailed

RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
MAX_YEARS = 5


def _field(text: str, lo: int, hi: int, name: str) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValidationFailed(f"cron {name}: invalid step")
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ValidationFailed(f"cron {name}: invalid range")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
            if step != 1:
                end = hi
        else:
            raise ValidationFailed(f"cron {name}: invalid value {part!r}")
        if name == "day-of-week" and end == 7:
            end = 6 if start <= 6 else 0
            values.add(0)
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise ValidationFailed(f"cron {name}: out of range {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, expr: str) -> Cron:
        expr = ALIASES.get(expr.strip(), expr.strip())
        parts = expr.split()
        if len(parts) != 5:
            raise ValidationFailed("cron needs 5 fields: minute hour day month weekday")
        names = ("minute", "hour", "day-of-month", "month", "day-of-week")
        sets = [_field(p, lo, hi if n != "day-of-week" else 7, n) for p, (lo, hi), n in zip(parts, RANGES, names, strict=True)]
        return cls(*sets, dom_any=parts[2] == "*", dow_any=parts[4] == "*")

    def _day_matches(self, day: dt.date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow  # classic cron: either matches

    def next_after(self, after: dt.datetime, timezone: str) -> dt.datetime:
        if after.tzinfo is None:
            raise ValidationFailed("after must be timezone-aware")
        tz = zone(timezone)
        local = after.astimezone(tz)
        day = local.date()
        for _ in range(366 * MAX_YEARS):
            if self._day_matches(day):
                for h in sorted(self.hours):
                    for m in sorted(self.minutes):
                        naive = dt.datetime(day.year, day.month, day.day, h, m)
                        instant = naive.replace(tzinfo=tz, fold=0).astimezone(dt.UTC)
                        if instant > after.astimezone(dt.UTC):
                            return instant
            day += dt.timedelta(days=1)
        raise ValidationFailed("cron never fires within 5 years")


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationFailed(f"unknown timezone {name!r}") from None
