"""Pure transformation costing (D-015, D-016). No ORM: Decimal in, Decimal out.

* Value to distribute = Σ input value + additional costs − expensed (abnormal) waste.
* Allocation weights: every output's cost share % when all outputs carry one (they must sum to 100), else the
  relative standard value (weight × standard cost) when every output has a standard cost, else the relative base
  quantity / actual weight. Non-main outputs get ``round_half_up(total × w / Σw, 4)``; the **main output takes the
  rounding remainder**, so ``Σ outputs + expensed waste = Σ inputs + additional costs`` exactly.
* Expensed waste value = abnormal waste quantity × input unit value (Σ input value / Σ input base qty), 4 decimals.
* Yield check (template): actual yield % = output weight / input qty × 100 per template output; outside
  ``expected ± tolerance`` (percentage points) → approval required (``transformation_yield_out_of_tolerance``).
* Receipt unit costs: ``unit_cost_for`` finds an 8-decimal unit cost whose receipt value
  (``round4(qty × unit_cost)``, the stock service rule) is exactly the allocated value, or reports that the
  quantity must be split (one base unit carries the remainder) — see ``receipt_split``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from inventory.valuation import core as val
from kernel.money import q_amount, q_unit_cost, round_half_up

ZERO = Decimal(0)
HUNDRED = Decimal(100)
CENT_UNIT = Decimal("0.00000001")


class Unbalanced(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OutputSpec:
    key: int
    weight_qty: Decimal  # actual weight or base quantity
    cost_share: Decimal | None = None
    standard_cost: Decimal | None = None
    is_main: bool = False


@dataclass(frozen=True, slots=True)
class YieldCheck:
    key: int
    expected: Decimal
    actual: Decimal
    tolerance: Decimal

    @property
    def deviation(self) -> Decimal:
        return abs(self.actual - self.expected)

    @property
    def within(self) -> bool:
        return self.deviation <= self.tolerance


def weights(outputs: Sequence[OutputSpec]) -> tuple[list[Decimal], str]:
    if not outputs:
        raise Unbalanced("no outputs")
    if all(o.cost_share is not None for o in outputs):
        shares = [o.cost_share or ZERO for o in outputs]
        if sum(shares, ZERO) != HUNDRED:
            raise Unbalanced(f"cost shares must sum to 100 (got {sum(shares, ZERO)})")
        return shares, "cost_share"
    if all(o.standard_cost is not None and o.standard_cost > 0 for o in outputs):
        return [o.weight_qty * (o.standard_cost or ZERO) for o in outputs], "standard_value"
    return [o.weight_qty for o in outputs], "quantity"


def main_index(outputs: Sequence[OutputSpec]) -> int:
    mains = [i for i, o in enumerate(outputs) if o.is_main]
    if len(mains) > 1:
        raise Unbalanced("only one main output")
    if mains:
        return mains[0]
    w, _ = weights(outputs)
    return max(range(len(outputs)), key=lambda i: (w[i], -i))


def allocate(total: Decimal, outputs: Sequence[OutputSpec]) -> tuple[list[Decimal], str]:
    """Split ``total`` (4 dp) over ``outputs``; the main output takes the remainder (D-016)."""
    total = q_amount(total)
    if total < 0:
        raise Unbalanced("value to distribute is negative (waste exceeds inputs)")
    w, basis = weights(outputs)
    wsum = sum(w, ZERO)
    if wsum <= 0:
        raise Unbalanced("allocation weights are zero")
    main = main_index(outputs)
    parts = [ZERO if i == main else round_half_up(total * wi / wsum, 4) for i, wi in enumerate(w)]
    parts[main] = q_amount(total - sum(parts, ZERO))
    if parts[main] < 0:
        raise Unbalanced("rounding left a negative main output")
    return parts, basis


def waste_value(abnormal_qty: Decimal, input_value: Decimal, input_qty: Decimal) -> Decimal:
    if abnormal_qty <= 0 or input_qty <= 0:
        return ZERO
    return round_half_up(abnormal_qty * input_value / input_qty, 4)


def yield_checks(
    input_qty: Decimal, actual: Sequence[tuple[int, Decimal, Decimal]], tolerance: Decimal
) -> list[YieldCheck]:
    """``actual`` = (key, expected yield %, output weight). Yield in percent, 6 dp."""
    out: list[YieldCheck] = []
    for key, expected, weight in actual:
        pct = round_half_up(weight * HUNDRED / input_qty, 6) if input_qty > 0 else ZERO
        out.append(YieldCheck(key, expected, pct, tolerance))
    return out


def unit_cost_for(qty: Decimal, value: Decimal) -> Decimal | None:
    """8-dp unit cost with ``receipt_value(qty, u) == value``, or ``None`` if the grid cannot hit it."""
    if qty <= 0:
        return None
    base = q_unit_cost(value / qty)
    for step in (0, 1, -1, 2, -2, 3, -3):
        u = base + CENT_UNIT * step
        if u >= 0 and val.receipt_value(qty, u) == value:
            return u
    return None


def receipt_split(qty: Decimal, value: Decimal) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Receipt pieces ``(qty, unit_cost, value)`` whose receipt values sum exactly to ``value``.

    One piece when an 8-dp unit cost hits the value; otherwise ``qty − 1`` at the rounded unit cost plus one base
    unit carrying the remainder (any 4-dp value is exact for one unit)."""
    u = unit_cost_for(qty, value)
    if u is not None:
        return [(qty, u, value)]
    if qty <= 1:
        raise Unbalanced(f"cannot value {qty} at exactly {value}")
    rest = qty - 1
    u1 = q_unit_cost(value / qty)
    v1 = val.receipt_value(rest, u1)
    v2 = q_amount(value - v1)
    if v2 < 0:
        u1 = q_unit_cost(u1 - CENT_UNIT * 10)
        v1 = val.receipt_value(rest, u1)
        v2 = q_amount(value - v1)
    if v2 < 0:
        raise Unbalanced(f"cannot value {qty} at exactly {value}")
    return [(rest, u1, v1), (Decimal(1), v2, v2)]
