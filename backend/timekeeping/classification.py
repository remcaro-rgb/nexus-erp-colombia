"""Time classification engine (pure functions; parameters are inputs, never constants).

Worked intervals (local time, America/Bogota) are split at midnight, at the night-window boundaries of the
**date of work** and at the point where the day's ordinary hours are exhausted, then summed into buckets::

    {ordinary|overtime}_{day|night}            on a working day
    rest_{ordinary|overtime}_{day|night}       on the rest day of the contract (default Sunday) or a holiday

Parameters per date (read through ``kernel.parameters`` by :func:`parameters_for`; UNKNOWN fails closed):

* ``hr.jornada.nocturna_inicio`` / ``hr.jornada.nocturna_fin`` — night window as ``HH:MM`` (e.g. a value valid
  until 2025-12-24 and another from 2025-12-25: intervals around the change date are split by date);
* ``hr.jornada.horas_diarias_ordinarias`` — ordinary hours of a working day before overtime starts;
* ``hr.jornada.horas_extra_max_dia`` — daily overtime limit (exceeding it is a warning ``overtime_daily_limit``).

Surcharge **rates** are not applied here (payroll formulas read them as parameters); this engine only produces
hours. Limits (weekly maximum, Sunday habituality, compensatory rest) are reported as warnings by the caller.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

BUCKETS = tuple(
    f"{prefix}{kind}_{part}"
    for prefix in ("", "rest_")
    for kind in ("ordinary", "overtime")
    for part in ("day", "night")
)
SIXTY = Decimal(60)


@dataclass(frozen=True, slots=True)
class DayRules:
    night_start: dt.time  # e.g. 19:00
    night_end: dt.time  # e.g. 06:00 (next morning)
    ordinary_hours: Decimal  # per working day
    overtime_max: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    hours: dict[str, Decimal]
    warnings: tuple[str, ...]


def _minutes(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def parse_hhmm(value: object) -> dt.time:
    text = str(value).strip()
    hh, _, mm = text.partition(":")
    return dt.time(int(hh), int(mm or 0))


def _pieces(start: dt.datetime, end: dt.datetime) -> Iterable[tuple[dt.date, int, int]]:
    """(work date, start minute, end minute) per calendar day."""
    cur = start
    while cur < end:
        day = cur.date()
        midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=cur.tzinfo)
        stop = min(end, midnight)
        s = cur.hour * 60 + cur.minute
        e = 1440 if stop == midnight else stop.hour * 60 + stop.minute
        if e > s:
            yield day, s, e
        cur = stop


def classify(
    intervals: Iterable[tuple[dt.datetime, dt.datetime]],
    rules_for: Callable[[dt.date], DayRules],
    is_rest_day: Callable[[dt.date], bool],
) -> Classification:
    hours_min: dict[str, int] = dict.fromkeys(BUCKETS, 0)
    worked: dict[dt.date, int] = {}
    overtime: dict[dt.date, int] = {}
    warnings: list[str] = []
    for start, end in sorted(intervals):
        if end <= start:
            raise ValueError("an interval must end after it starts")
        for day, s, e in _pieces(start, end):
            rules = rules_for(day)
            rest = "rest_" if is_rest_day(day) else ""
            ns, ne = _minutes(rules.night_start), _minutes(rules.night_end)
            ordinary_left = int(rules.ordinary_hours * SIXTY) - worked.get(day, 0)
            cuts = sorted({s, e, *(c for c in (ne, ns) if s < c < e)})
            if 0 < ordinary_left < e - s:
                cuts = sorted({*cuts, s + ordinary_left})
            for a, b in itertools.pairwise(cuts):
                night = a < ne or a >= ns
                kind = "ordinary" if worked.get(day, 0) < int(rules.ordinary_hours * SIXTY) else "overtime"
                hours_min[f"{rest}{kind}_{'night' if night else 'day'}"] += b - a
                worked[day] = worked.get(day, 0) + (b - a)
                if kind == "overtime":
                    overtime[day] = overtime.get(day, 0) + (b - a)
            if rules.overtime_max is not None and overtime.get(day, 0) > int(rules.overtime_max * SIXTY):
                warnings.append(f"overtime_daily_limit:{day.isoformat()}")
    hours = {k: (Decimal(v) / SIXTY).quantize(Decimal("0.000001")) for k, v in hours_min.items() if v}
    return Classification(hours, tuple(dict.fromkeys(warnings)))
