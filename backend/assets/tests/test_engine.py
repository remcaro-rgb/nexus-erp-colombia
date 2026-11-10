"""Depreciation engine: exact charges, residual absorption (property-based), methods, prospective re-anchor."""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from assets import engine

D = Decimal


def anchored(method: str, cost: str, life: int, salvage: str = "0", units: str | None = None) -> engine.BookState:
    state = engine.BookState(method=method, cost=D(cost), salvage=D(salvage))
    return engine.anchor(state, life, D(units) if units else None)


def test_T_P14_02_residual_absorbed_in_last_period_s8() -> None:
    charges = engine.schedule(anchored("STRAIGHT_LINE", "4800000", 36))
    assert len(charges) == 36
    assert charges[:35] == [D("133333.33")] * 35
    assert charges[-1] == D("133333.45")
    assert sum(charges) == D("4800000")


@settings(max_examples=200, deadline=None)
@given(
    cost=st.decimals(min_value=D("1.00"), max_value=D("999999999.99"), places=2),
    life=st.integers(min_value=1, max_value=480),
    salvage_pct=st.integers(min_value=0, max_value=50),
    method=st.sampled_from(["STRAIGHT_LINE", "DECLINING", "SUM_OF_YEARS"]),
)
def test_T_P14_02_property_total_equals_depreciable_base(
    cost: Decimal, life: int, salvage_pct: int, method: str
) -> None:
    salvage = (cost * salvage_pct / 100).quantize(D("0.01"))
    charges = engine.schedule(anchored(method, str(cost), life, str(salvage)))
    assert sum(charges, D(0)) == cost - salvage  # Σ = depreciable base exactly
    assert len(charges) <= life
    assert all(c >= 0 and c == c.quantize(D("0.01")) for c in charges)  # COP minor units, never negative


@settings(max_examples=100, deadline=None)
@given(
    cost=st.decimals(min_value=D("100.00"), max_value=D("99999999.99"), places=2),
    units=st.lists(st.integers(min_value=0, max_value=5000), min_size=1, max_size=40),
)
def test_T_P14_07_property_units_of_production_never_exceed_base(cost: Decimal, units: list[int]) -> None:
    total = sum(units) or 1
    state = anchored("UNITS", str(cost), 60, units=str(total))
    charges = engine.schedule(state, units=[D(u) for u in units])
    assert sum(charges, D(0)) <= cost
    if sum(units) == total:
        assert sum(charges, D(0)) == cost  # all the units consumed → fully depreciated


def test_T_P14_07_units_of_production_exact() -> None:
    state = anchored("UNITS", "1000000", 60, units="100000")  # synthetic machine: 100,000 hours
    first = engine.charge(state, D("12500"))
    assert first == D("125000.00")
    state = engine.apply(state, first, D("12500"))
    assert engine.charge(state, D(0)) == 0
    assert engine.charge(state, D("87500")) == D("875000.00")  # the last units absorb the residual
    with pytest.raises(engine.EngineError):
        engine.charge(state, None)


def test_declining_balance_switches_to_straight_line_and_keeps_salvage() -> None:
    charges = engine.schedule(anchored("DECLINING", "1000000", 12, "100000"))
    assert sum(charges) == D("900000")
    assert charges[0] == D("166666.67")  # 2/12 of the carrying amount
    assert all(a >= b for a, b in itertools.pairwise(charges) if b != charges[-1])


def test_sum_of_years_digits() -> None:
    charges = engine.schedule(anchored("SUM_OF_YEARS", "78000", 12))
    assert charges[0] == D("12000.00")  # 78,000 × 12/78
    assert sum(charges) == D("78000")


def test_T_P14_08_reanchor_is_prospective() -> None:
    state = anchored("STRAIGHT_LINE", "4800000", 36)
    for _ in range(12):
        state = engine.apply(state, engine.charge(state))
    assert state.accumulated == D("1599999.96")
    # new estimate: 12 more months instead of 24; the past stays, the rest is re-spread
    state = engine.anchor(state, 12)
    rest = engine.schedule(state)
    assert len(rest) == 12
    assert sum(rest) == D("3200000.04")
    assert rest[0] == D("266666.67")


def test_calendar_helpers() -> None:
    assert engine.depreciation_start(dt.date(2026, 3, 10), "FOLLOWING_MONTH") == dt.date(2026, 4, 1)
    assert engine.depreciation_start(dt.date(2026, 3, 10), "SAME_MONTH") == dt.date(2026, 3, 1)
    assert engine.depreciation_start(dt.date(2026, 12, 31), "FOLLOWING_MONTH") == dt.date(2027, 1, 1)
    assert engine.month_end(dt.date(2028, 2, 3)) == dt.date(2028, 2, 29)
    assert engine.months_between(dt.date(2026, 4, 1), dt.date(2026, 11, 30)) == 8
    with pytest.raises(engine.EngineError):
        engine.anchor(engine.BookState(method="STRAIGHT_LINE", cost=D(1)), 0)
    with pytest.raises(engine.EngineError):
        engine.anchor(engine.BookState(method="NOPE", cost=D(1)), 3)
