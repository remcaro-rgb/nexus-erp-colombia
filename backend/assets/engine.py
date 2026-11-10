"""Depreciation engine (pure, Decimal only; P14 rules).

* Every charge is rounded half-even to the currency's minor units (COP: 2 decimals, shared/07 §8).
* The *anchor* (base, periods, units) is fixed when depreciation starts and re-set prospectively after a
  revaluation, an impairment or a change in estimate (NIIF: changes in estimates are prospective). Between
  anchors the straight-line charge is constant and the **last period of the anchor absorbs the residual**, so
  Σ charges = depreciable base exactly.
* Methods: STRAIGHT_LINE, DECLINING (factor × SL rate on the carrying amount, switching to SL on the remaining
  periods when that is larger; never below salvage), UNITS (units of production), SUM_OF_YEARS [A].
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal

from kernel.money import minor_units, round_half_even

ZERO = Decimal(0)
STRAIGHT_LINE, DECLINING, UNITS, SUM_OF_YEARS = "STRAIGHT_LINE", "DECLINING", "UNITS", "SUM_OF_YEARS"
METHODS = frozenset({STRAIGHT_LINE, DECLINING, UNITS, SUM_OF_YEARS})


class EngineError(ValueError):
    """Invalid engine input (mapped to ``validation_failed`` by the services)."""

    def __init__(self, detail: str, reason: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BookState:
    method: str
    cost: Decimal
    salvage: Decimal = ZERO
    accumulated: Decimal = ZERO
    impairment: Decimal = ZERO
    anchor_base: Decimal = ZERO
    anchor_periods: int = 0
    periods_since_anchor: int = 0
    anchor_units: Decimal | None = None
    units_since_anchor: Decimal = ZERO
    declining_factor: Decimal = Decimal(2)
    currency: str = "COP"

    @property
    def nbv(self) -> Decimal:
        return self.cost - self.accumulated - self.impairment

    @property
    def remaining_base(self) -> Decimal:
        rest = self.nbv - self.salvage
        return rest if rest > ZERO else ZERO

    @property
    def periods_left(self) -> int:
        return max(self.anchor_periods - self.periods_since_anchor, 0)

    @property
    def exhausted(self) -> bool:
        return self.remaining_base == ZERO


def _round(value: Decimal, currency: str) -> Decimal:
    return round_half_even(value, minor_units(currency))


def anchor(state: BookState, periods: int, units: Decimal | None = None) -> BookState:
    """(Re)anchor prospectively: the remaining depreciable base over ``periods`` (and ``units``)."""
    if state.method not in METHODS:
        raise EngineError(f"unknown depreciation method {state.method}", "method_unknown")
    if periods < 1:
        raise EngineError("the remaining useful life must be at least one month", "life_exhausted")
    if state.method == UNITS and (units is None or units <= ZERO):
        raise EngineError("units of production need the remaining total units", "units_required")
    return replace(
        state,
        anchor_base=state.remaining_base,
        anchor_periods=periods,
        periods_since_anchor=0,
        anchor_units=units if state.method == UNITS else None,
        units_since_anchor=ZERO,
    )


def charge(state: BookState, units: Decimal | None = None) -> Decimal:
    """Depreciation of the next period (not applied)."""
    rest = state.remaining_base
    if rest == ZERO:
        return ZERO
    cur = state.currency
    if state.method == UNITS:
        if units is None or units < ZERO:
            raise EngineError("units of production need the period's units", "units_required")
        total = state.anchor_units or ZERO
        if total <= ZERO:
            raise EngineError("units of production need the remaining total units", "units_required")
        if units == ZERO:
            return ZERO
        if state.units_since_anchor + units >= total:
            return rest
        return min(_round(state.anchor_base * units / total, cur), rest)
    left = state.periods_left
    if left <= 1:
        return rest  # the last period absorbs the residual
    if state.method == STRAIGHT_LINE:
        amount = _round(state.anchor_base / state.anchor_periods, cur)
    elif state.method == SUM_OF_YEARS:
        n = state.anchor_periods
        digits = Decimal(n * (n + 1) // 2)
        amount = _round(state.anchor_base * Decimal(n - state.periods_since_anchor) / digits, cur)
    elif state.method == DECLINING:
        rate = state.declining_factor / Decimal(state.anchor_periods)
        declining = _round(state.nbv * rate, cur)
        straight = _round(rest / Decimal(left), cur)
        amount = max(declining, straight)
    else:  # pragma: no cover - anchor() validates the method
        raise EngineError(f"unknown depreciation method {state.method}", "method_unknown")
    return min(amount, rest)


def apply(state: BookState, amount: Decimal, units: Decimal | None = None) -> BookState:
    return replace(
        state,
        accumulated=state.accumulated + amount,
        periods_since_anchor=state.periods_since_anchor + 1,
        units_since_anchor=state.units_since_anchor + (units or ZERO),
    )


def schedule(state: BookState, periods: int | None = None, units: list[Decimal] | None = None) -> list[Decimal]:
    """Projected charges until exhaustion (or ``periods``); UNITS needs the per-period ``units``."""
    out: list[Decimal] = []
    limit = periods if periods is not None else state.periods_left + 1
    i = 0
    while i < limit and not state.exhausted:
        u = None
        if state.method == UNITS:
            if units is None or i >= len(units):
                break
            u = units[i]
        amount = charge(state, u)
        state = apply(state, amount, u)
        out.append(amount)
        i += 1
        if state.method != UNITS and state.periods_left == 0:
            break
    return out


# --------------------------------------------------------------------------- calendar helpers


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def add_months(day: dt.date, months: int) -> dt.date:
    idx = day.year * 12 + day.month - 1 + months
    return dt.date(idx // 12, idx % 12 + 1, 1)


def month_end(day: dt.date) -> dt.date:
    return add_months(day, 1) - dt.timedelta(days=1)


def depreciation_start(in_service: dt.date, convention: str) -> dt.date:
    """First day of the first depreciated month (FOLLOWING_MONTH default, SAME_MONTH option)."""
    first = month_start(in_service)
    return first if convention == "SAME_MONTH" else add_months(first, 1)


def months_between(start: dt.date, end: dt.date) -> int:
    """Whole months from the month of ``start`` up to and including the month of ``end``."""
    return (end.year - start.year) * 12 + end.month - start.month + 1
