"""T-INV-PRES-01 — factor / precision validation; exact Decimal conversion; fractional packages rejected (unit)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from inventory.presentations import core
from inventory.valuation import core as val

D = Decimal


def _code(fn, *args):
    with pytest.raises(Exception) as exc:
        fn(*args)
    return getattr(exc.value, "code", None)


def test_T_INV_PRES_01_factor_and_precision_validation():
    assert core.validate_factor("12") == D(12)
    assert core.validate_factor(D("0.5")) == D("0.5")
    assert core.validate_factor("0.000001") == D("0.000001")
    for bad in ("0", "-1", "abc", "0.0000001", None, 12.0, True, "Infinity", "NaN", "1e20"):
        assert _code(core.validate_factor, bad) == "presentation_factor_invalid", bad
    assert core.validate_precision(0) == 0 and core.validate_precision(6) == 6
    for bad in (-1, 7, "2", 1.0, True):
        assert _code(core.validate_precision, bad) == "uom_precision_violation", bad


def test_T_INV_PRES_01_conversion_is_exact_with_decimal():
    # 3 boxes x 12 = 36 base units, exactly; 0.1 + 0.2 style float errors impossible
    assert core.to_base(D(3), D(12), 0, 0) == D(36)
    assert core.to_base(D("2.5"), D("0.2"), 1, 3) == D("0.5")
    assert core.to_packages(D(36), D(12), 0) == D(3)
    assert core.to_packages(D("1.5"), D("0.5"), 0) == D(3)
    assert core.to_base(D("0.333"), D(3), 3, 3) == D("0.999")
    # round trip for a 500 g bag in a kg item with 3 decimals
    bag = D("0.5")
    for n in range(1, 50):
        assert core.to_packages(core.to_base(D(n), bag, 0, 3), bag, 0) == D(n)


def test_T_INV_PRES_01_rounding_to_presentation_precision_rejects_fractional_packages():
    # 30 units are 2.5 boxes of 12: rejected with precision 0, accepted with precision 1
    assert _code(core.to_packages, D(30), D(12), 0) == "uom_precision_violation"
    assert core.to_packages(D(30), D(12), 1) == D("2.5")
    # 1.5 packages entered for a whole-package presentation
    assert _code(core.to_base, D("1.5"), D(12), 0, 0) == "uom_precision_violation"
    # base quantity finer than the base UoM precision (UND precision 0)
    assert _code(core.to_base, D(1), D("0.5"), 0, 0) == "uom_precision_violation"
    assert _code(core.check_precision, D("1.0001"), 3) == "uom_precision_violation"
    assert core.check_precision(D("1.000"), 0) == D("1.000")  # trailing zeros are not extra precision


def test_T_INV_PRES_01_packaged_invariant_and_unpack_plan():
    box = core.Packaged("box", D(2), D(12))
    six = core.Packaged("six", D(1), D(6))
    assert core.loose(D(40), [box, six]) == D(10)
    core.check_invariant(D(30), [box, six])
    assert _code(core.check_invariant, D(29), [box, six]) == "packaged_exceeds_canonical"
    # need 15 loose: 10 loose + open the six-pack (smallest factor first)
    assert core.unpack_plan(D(40), [box, six], D(15)) == [("six", D(1))]
    # need 30 loose: open six-pack and two boxes
    assert core.unpack_plan(D(40), [box, six], D(30)) == [("six", D(1)), ("box", D(2))]
    reserved_box = core.Packaged("box", D(2), D(12), reserved_packages=D(2))
    assert _code(core.unpack_plan, D(40), [reserved_box, six], D(30)) == "stock_insufficient"


def test_T_INV_PRES_01_valuation_math_shared_with_fakes():
    pos = val.Position(D(0), D(0), D(0))
    pos, v1 = val.wac_receive(pos, D(10), D("1000"))
    pos, v2 = val.wac_receive(pos, D(5), D("1300"))
    assert (v1, v2, pos.qty, pos.value, pos.avg_cost) == (D("10000.0000"), D("6500.0000"), D(15), D("16500.0000"), D("1100.00000000"))
    pos, out = val.wac_issue(pos, D(3))
    assert out == D("3300.0000") and pos.value == D("13200.0000")
    pos, out = val.wac_issue(pos, D(12))  # residual-on-zero
    assert pos.qty == 0 and pos.value == 0 and out == D("13200.0000")
