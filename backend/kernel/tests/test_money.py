from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kernel import money
from kernel.money import Money, MoneyError, allocate

amounts = st.decimals(min_value=Decimal("-1e12"), max_value=Decimal("1e12"), places=4, allow_nan=False)
weights = st.lists(st.decimals(min_value=0, max_value=Decimal("1e6"), places=6), min_size=1, max_size=12).filter(
    lambda w: sum(w) > 0
)


@settings(max_examples=300, deadline=None)
@given(total=amounts, w=weights)
def test_T_P01_01_allocation_preserves_sum_and_is_deterministic(total, w):
    parts = allocate(total, w)
    assert sum(parts) == money.q_amount(total)
    assert parts == allocate(total, w)
    unit = Decimal("0.0001")
    exact = [abs(money.q_amount(total)) * x / sum(w) for x in w]
    for p, e in zip(parts, exact, strict=True):
        assert abs(abs(p) - e) < unit  # each part within one unit of its exact share
        assert p == 0 or (p > 0) == (total > 0)


@settings(max_examples=200, deadline=None)
@given(a=amounts, b=amounts, c=amounts)
def test_T_P01_01_money_arithmetic_properties(a, b, c):
    x, y, z = Money(a, "COP"), Money(b, "COP"), Money(c, "COP")
    assert (x + y) + z == x + (y + z)
    assert x + y == y + x
    assert x - x == Money.zero("COP")
    assert (-x).__neg__() == x
    assert (x * 2).amount == a * 2


def test_T_P01_01_allocation_ties_go_to_lower_index():
    assert allocate(Decimal("0.0002"), [1, 1, 1]) == [Decimal("0.0001"), Decimal("0.0001"), Decimal("0.0000")]
    assert allocate(Decimal("10"), [1, 1, 1], places=0) == [Decimal(4), Decimal(3), Decimal(3)]
    assert allocate(Decimal("-10"), [1, 1, 1], places=0) == [Decimal(-4), Decimal(-3), Decimal(-3)]
    assert allocate(Decimal("100"), [0, 1], places=2) == [Decimal("0.00"), Decimal("100.00")]


@pytest.mark.parametrize(
    "bad",
    [[], [-1, 2], [0, 0]],
)
def test_T_P01_01_allocation_rejects_bad_weights(bad):
    with pytest.raises(MoneyError):
        allocate(Decimal(1), bad)


def test_T_P01_01_money_rejects_floats_and_mixed_currency():
    with pytest.raises(MoneyError):
        Money(0.1, "COP")  # type: ignore[arg-type]  # erp: allow-float(test of rejection)
    with pytest.raises(MoneyError):
        Money.of(1, "COP") + Money.of(1, "USD")
    with pytest.raises(MoneyError):
        Money.of(1) * Money.of(2)  # type: ignore[operator]
    with pytest.raises(MoneyError):
        Money.of(1, "cop")
    with pytest.raises(MoneyError):
        Money.of(1) + 1  # type: ignore[operator]
    with pytest.raises(MoneyError):
        money.to_decimal(True)
    with pytest.raises(MoneyError):
        money.to_decimal("abc")
    with pytest.raises(MoneyError):
        money.to_decimal("NaN")
    with pytest.raises(MoneyError):
        money.round_half_even(1, -1)


def test_T_P01_01_rounding_helpers():
    assert money.round_half_even("2.5", 0) == Decimal(2)
    assert money.round_half_even("3.5", 0) == Decimal(4)
    assert money.round_half_up("2.5", 0) == Decimal(3)
    assert money.q_amount("1.00005") == Decimal("1.0000")
    assert money.q_qty("1.0000005") == Decimal("1.000000")
    assert money.q_unit_cost("1.123456785") == Decimal("1.12345678")
    assert money.q_fx("4123.123456789050") == Decimal("4123.1234567890")
    assert money.q_pct("19.0000005") == Decimal("19.000000")
    assert money.minor_units("COP") == 2
    assert money.minor_units("JPY") == 0
    assert money.minor_units("XYZ") == 2


def test_T_P01_01_money_api():
    m = Money.of("1234.565", "COP")
    assert m.round_to_minor() == Money.of("1234.57", "COP")
    assert m.quantize(2) == Money.of("1234.56", "COP")
    assert abs(Money.of(-5)) == Money.of(5)
    assert Money.of(1) < Money.of(2) <= Money.of(2)
    assert Money.of(3) > Money.of(2) >= Money.of(2)
    assert Money.zero().is_zero()
    assert 2 * Money.of(3) == Money.of(6)
    assert Money.from_json(m.to_json()) == m
    assert m.to_json() == {"amount": "1234.565", "currency": "COP"}
    parts = Money.of(100).allocate([1, 2])
    assert sum(p.amount for p in parts) == Decimal(100)
    assert money.money_sum([Money.of(1), Money.of(2)], "COP") == Money.of(3)
    assert money.to_decimal(7) == Decimal(7)
