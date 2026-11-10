"""Time classification engine (pure): midnight split, night window by date of work, overtime, rest days."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from timekeeping.classification import BUCKETS, DayRules, classify

TZ = dt.timezone(dt.timedelta(hours=-5))
OLD = DayRules(dt.time(21), dt.time(6), Decimal(8), Decimal(2))
NEW = DayRules(dt.time(19), dt.time(6), Decimal(8), Decimal(2))
CHANGE = dt.date(2025, 12, 25)


def rules(day: dt.date) -> DayRules:
    return NEW if day >= CHANGE else OLD


def at(y: int, m: int, d: int, h: int, mi: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ)


def no_rest(day: dt.date) -> bool:
    return False


def test_P13a_day_shift_with_overtime():
    r = classify([(at(2026, 3, 2, 8), at(2026, 3, 2, 18))], rules, no_rest)
    assert r.hours == {"ordinary_day": Decimal(8), "overtime_day": Decimal(2)}
    assert r.warnings == ()


def test_P13a_night_window_follows_the_date_of_work_across_the_rule_change():
    # 2025-12-24 uses 21:00, 2025-12-25 uses 19:00 (synthetic rules standing in for the parameters)
    before = classify([(at(2025, 12, 24, 12), at(2025, 12, 24, 20))], rules, no_rest)
    after = classify([(at(2025, 12, 25, 12), at(2025, 12, 25, 20))], rules, no_rest)
    assert before.hours == {"ordinary_day": Decimal(8)}
    assert after.hours == {"ordinary_day": Decimal(7), "ordinary_night": Decimal(1)}


def test_P13a_interval_crossing_midnight_is_split_and_rest_day_bucketed():
    sunday = lambda d: d.weekday() == 6  # noqa: E731
    # Saturday 22:00 → Sunday 07:00: 2 h Saturday night, 6 h Sunday night, 1 h Sunday day
    r = classify([(at(2026, 3, 7, 22), at(2026, 3, 8, 7))], rules, sunday)
    assert r.hours == {
        "ordinary_night": Decimal(2),
        "rest_ordinary_night": Decimal(6),
        "rest_ordinary_day": Decimal(1),
    }


def test_P13a_overtime_limit_is_a_warning():
    r = classify([(at(2026, 3, 2, 6), at(2026, 3, 2, 19))], rules, no_rest)
    assert r.hours == {"ordinary_day": Decimal(8), "overtime_day": Decimal(5)}
    assert r.warnings == ("overtime_daily_limit:2026-03-02",)


def test_P13a_invalid_interval_rejected():
    with pytest.raises(ValueError):
        classify([(at(2026, 3, 2, 9), at(2026, 3, 2, 8))], rules, no_rest)


@given(
    start=st.datetimes(min_value=dt.datetime(2025, 12, 20), max_value=dt.datetime(2026, 1, 5)),
    minutes=st.integers(min_value=1, max_value=36 * 60),
)
def test_P13a_property_classification_conserves_worked_time(start, minutes):
    s = start.replace(second=0, microsecond=0, tzinfo=TZ)
    e = s + dt.timedelta(minutes=minutes)
    r = classify([(s, e)], rules, lambda d: d.weekday() == 6)
    assert set(r.hours) <= set(BUCKETS)
    assert abs(sum(r.hours.values()) - Decimal(minutes) / 60) < Decimal("0.00001")
