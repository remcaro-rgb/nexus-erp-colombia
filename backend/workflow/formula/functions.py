"""Whitelisted functions of the formula DSL.

The table maps a lower-case name to a signature and a pure Python callable
over already-evaluated Decimal/str/bool/date values. ``if`` and ``lookup`` are
special forms handled by the evaluator (lazy branches / table resolution).
Nothing here resolves names dynamically.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)
from typing import Any

from workflow.formula.errors import FormulaTypeError
from workflow.formula.limits import MAX_ROUND_PLACES

NUMBER, STRING, BOOLEAN, DATE = "number", "string", "boolean", "date"
ANY = "any"  # same type as the other branch (if)

ROUNDING_MODES: dict[str, str] = {
    "HALF_UP": ROUND_HALF_UP,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "HALF_DOWN": ROUND_HALF_DOWN,
    "UP": ROUND_UP,
    "DOWN": ROUND_DOWN,
    "CEILING": ROUND_CEILING,
    "FLOOR": ROUND_FLOOR,
    "05UP": ROUND_05UP,
}
DEFAULT_ROUNDING = "HALF_UP"
MIN_ROUND_PLACES = -9


@dataclass(frozen=True, slots=True)
class Signature:
    params: tuple[str, ...]
    returns: str
    min_args: int
    variadic: bool = False  # repeats the last param type
    impl: Callable[..., Any] | None = None


def _integral(value: Decimal, what: str) -> int:
    if value != value.to_integral_value():
        raise FormulaTypeError(f"{what} must be a whole number")
    return int(value)


def round_value(x: Decimal, places: Decimal = Decimal(0), mode: str = DEFAULT_ROUNDING) -> Decimal:
    n = _integral(places, "round() places")
    if not MIN_ROUND_PLACES <= n <= MAX_ROUND_PLACES:
        raise FormulaTypeError(f"round() places must be between {MIN_ROUND_PLACES} and {MAX_ROUND_PLACES}")
    rounding = ROUNDING_MODES.get(mode.upper())
    if rounding is None:
        raise FormulaTypeError(f"unknown rounding mode '{mode}' (use {', '.join(ROUNDING_MODES)})")
    q = x.quantize(Decimal(1).scaleb(-n), rounding=rounding)
    if n < 0:
        return Decimal(int(q))
    return q


def _min(*xs: Decimal) -> Decimal:
    return min(xs)


def _max(*xs: Decimal) -> Decimal:
    return max(xs)


def _abs(x: Decimal) -> Decimal:
    return abs(x)


def _floor(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_FLOOR)


def _ceil(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_CEILING)


def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    if lo > hi:
        raise FormulaTypeError("clamp(): the lower bound is greater than the upper bound")
    return min(max(x, lo), hi)


def _percent(x: Decimal, p: Decimal) -> Decimal:
    return x * p / Decimal(100)


def _days_between(a: dt.date, b: dt.date) -> Decimal:
    return Decimal((b - a).days)


def _year(d: dt.date) -> Decimal:
    return Decimal(d.year)


def _month(d: dt.date) -> Decimal:
    return Decimal(d.month)


def _day(d: dt.date) -> Decimal:
    return Decimal(d.day)


def parse_date(text: str) -> dt.date:
    if len(text) != 10:
        raise FormulaTypeError(f"'{text[:20]}' is not an ISO date (YYYY-MM-DD)")
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise FormulaTypeError(f"'{text}' is not an ISO date (YYYY-MM-DD)") from exc


def _date(text: str) -> dt.date:
    return parse_date(text)


def _add_days(d: dt.date, n: Decimal) -> dt.date:
    days = _integral(n, "add_days() days")
    if abs(days) > 36_600:
        raise FormulaTypeError("add_days(): at most 100 years")
    try:
        return d + dt.timedelta(days=days)
    except OverflowError as exc:
        raise FormulaTypeError("add_days(): date out of range") from exc


FUNCTIONS: dict[str, Signature] = {
    "if": Signature((BOOLEAN, ANY, ANY), ANY, 3),
    "lookup": Signature((STRING, ANY, ANY), ANY, 2),
    "min": Signature((NUMBER,), NUMBER, 1, variadic=True, impl=_min),
    "max": Signature((NUMBER,), NUMBER, 1, variadic=True, impl=_max),
    "abs": Signature((NUMBER,), NUMBER, 1, impl=_abs),
    "round": Signature((NUMBER, NUMBER, STRING), NUMBER, 1, impl=round_value),
    "floor": Signature((NUMBER,), NUMBER, 1, impl=_floor),
    "ceil": Signature((NUMBER,), NUMBER, 1, impl=_ceil),
    "clamp": Signature((NUMBER, NUMBER, NUMBER), NUMBER, 3, impl=_clamp),
    "percent": Signature((NUMBER, NUMBER), NUMBER, 2, impl=_percent),
    "days_between": Signature((DATE, DATE), NUMBER, 2, impl=_days_between),
    "year": Signature((DATE,), NUMBER, 1, impl=_year),
    "month": Signature((DATE,), NUMBER, 1, impl=_month),
    "day": Signature((DATE,), NUMBER, 1, impl=_day),
    "date": Signature((STRING,), DATE, 1, impl=_date),
    "add_days": Signature((DATE, NUMBER), DATE, 2, impl=_add_days),
}
