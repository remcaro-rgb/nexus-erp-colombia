"""Colombian public holidays (Ley 51/1983, source S-41, R-LB-11 VERIFIED-PRIMARY).

* Fixed dates that never move: 1 Jan, 1 May, 20 Jul, 7 Aug, 8 Dec, 25 Dec.
* Listed holidays falling on a day other than Monday move to the following
  Monday (Ley 51/1983, "Ley Emiliani"): 6 Jan, 19 Mar, 29 Jun, 15 Aug, 12 Oct,
  1 Nov, 11 Nov, and the Easter-based Ascensión, Corpus Christi and Sagrado
  Corazón.
* Jueves and Viernes Santo are Easter-based and never move.

The tenant may override the computed list (add / remove a date) through
``localization_co.models.HolidayOverride``; :func:`holidays_for_year` takes the
overrides as plain data so the algorithm stays pure and testable.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

SOURCE_ID = "S-41"


@dataclass(frozen=True, slots=True)
class Holiday:
    date: dt.date
    name: str
    rule: str  # FIXED | MOVED_TO_MONDAY | EASTER | EASTER_MOVED | OVERRIDE

    def to_json(self) -> dict[str, str]:
        return {"date": self.date.isoformat(), "name": self.name, "rule": self.rule}


def easter_sunday(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    if year < 1583:
        raise ValueError("the Gregorian computus is defined from 1583")
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def next_monday(day: dt.date) -> dt.date:
    """``day`` itself when it is a Monday, else the following Monday."""
    return day + dt.timedelta(days=(7 - day.weekday()) % 7)


FIXED: tuple[tuple[int, int, str], ...] = (
    (1, 1, "Año Nuevo"),
    (5, 1, "Día del Trabajo"),
    (7, 20, "Día de la Independencia"),
    (8, 7, "Batalla de Boyacá"),
    (12, 8, "Inmaculada Concepción"),
    (12, 25, "Navidad"),
)

MOVABLE: tuple[tuple[int, int, str], ...] = (
    (1, 6, "Día de los Reyes Magos"),
    (3, 19, "Día de San José"),
    (6, 29, "San Pedro y San Pablo"),
    (8, 15, "La Asunción de la Virgen"),
    (10, 12, "Día de la Raza"),
    (11, 1, "Todos los Santos"),
    (11, 11, "Independencia de Cartagena"),
)

# (days after Easter Sunday, name, moves to Monday?)
EASTER_BASED: tuple[tuple[int, str, bool], ...] = (
    (-3, "Jueves Santo", False),
    (-2, "Viernes Santo", False),
    (39, "Ascensión del Señor", True),
    (60, "Corpus Christi", True),
    (68, "Sagrado Corazón de Jesús", True),
)


@dataclass(frozen=True, slots=True)
class Override:
    """Admin override: ``add`` a holiday on ``date`` or remove (``add=False``) a computed one."""

    date: dt.date
    add: bool
    name: str = ""


def computed_holidays(year: int) -> list[Holiday]:
    result: list[Holiday] = [Holiday(dt.date(year, m, d), name, "FIXED") for m, d, name in FIXED]
    result += [Holiday(next_monday(dt.date(year, m, d)), name, "MOVED_TO_MONDAY") for m, d, name in MOVABLE]
    easter = easter_sunday(year)
    for offset, name, moves in EASTER_BASED:
        day = easter + dt.timedelta(days=offset)
        result.append(Holiday(next_monday(day), name, "EASTER_MOVED") if moves else Holiday(day, name, "EASTER"))
    return sorted(result, key=lambda h: (h.date, h.name))


def holidays_for_year(year: int, overrides: Iterable[Override] = ()) -> list[Holiday]:
    by_date: dict[dt.date, list[Holiday]] = {}
    for h in computed_holidays(year):
        by_date.setdefault(h.date, []).append(h)
    for o in sorted(overrides, key=lambda o: (o.date, o.add)):
        if o.date.year != year:
            continue
        if o.add:
            by_date.setdefault(o.date, []).append(Holiday(o.date, o.name or "Festivo (ajuste)", "OVERRIDE"))
        else:
            by_date.pop(o.date, None)
    return [h for d in sorted(by_date) for h in by_date[d]]


def is_holiday(day: dt.date, overrides: Iterable[Override] = ()) -> bool:
    return any(h.date == day for h in holidays_for_year(day.year, overrides))


def is_business_day(day: dt.date, overrides: Iterable[Override] = (), *, saturday_is_business: bool = False) -> bool:
    if day.weekday() == 6 or (day.weekday() == 5 and not saturday_is_business):
        return False
    return not is_holiday(day, overrides)


def add_business_days(start: dt.date, days: int, overrides: Iterable[Override] = ()) -> dt.date:
    """Business-day arithmetic on the Bogotá calendar (Mon–Fri minus holidays)."""
    if days < 0:
        raise ValueError("days must be >= 0")
    ov = tuple(overrides)
    current = start
    remaining = days
    while remaining:
        current += dt.timedelta(days=1)
        if is_business_day(current, ov):
            remaining -= 1
    return current
