"""Pure valuation math shared by the real StockService and the fakes (shared/07 §7, ADR-008).

No ORM, no I/O: Decimal in, Decimal out. Quantities NUMERIC(20,6), unit cost NUMERIC(24,8)
(half-even to 8 decimals), values NUMERIC(20,4) (half-even to 4 decimals).

* Weighted average (perpetual): receipt value = round4(qty × unit_cost);
  ``new_avg = round8((value_on_hand + value_in) / (qty_on_hand + qty_in))``.
  Issue value = round4(qty × avg); **residual-on-zero**: when the issue empties the stock the whole
  remaining value leaves with it, so value = 0 exactly when qty = 0.
* FIFO: layers ordered by (receipt time, id); an issue consumes the oldest first; a partially
  consumed layer keeps ``remaining_qty``/``remaining_value`` and the last unit of a layer takes its
  residual value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from kernel.money import q_amount, q_qty, q_unit_cost

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Position:
    qty: Decimal
    value: Decimal
    avg_cost: Decimal


@dataclass(frozen=True, slots=True)
class LayerState:
    remaining_qty: Decimal
    remaining_value: Decimal


@dataclass(frozen=True, slots=True)
class LayerTake:
    index: int
    qty: Decimal
    value: Decimal


def receipt_value(qty: Decimal, unit_cost: Decimal) -> Decimal:
    return q_amount(q_qty(qty) * q_unit_cost(unit_cost))


def average_of(qty: Decimal, value: Decimal, fallback: Decimal = ZERO) -> Decimal:
    return q_unit_cost(value / qty) if qty > 0 else fallback


def wac_receive(pos: Position, qty_in: Decimal, unit_cost: Decimal) -> tuple[Position, Decimal]:
    """New position after a receipt; returns (position, value_in)."""
    if qty_in <= 0:
        raise ValueError("receipt quantity must be positive")
    if unit_cost < 0:
        raise ValueError("unit cost must not be negative")
    value_in = receipt_value(qty_in, unit_cost)
    qty = q_qty(pos.qty + qty_in)
    value = q_amount(pos.value + value_in)
    if qty == 0:
        return Position(ZERO, ZERO, q_unit_cost(unit_cost)), value_in
    avg = average_of(qty, value, q_unit_cost(unit_cost)) if qty > 0 else q_unit_cost(unit_cost)
    return Position(qty, value, avg), value_in


def wac_issue(pos: Position, qty_out: Decimal) -> tuple[Position, Decimal]:
    """New position after an issue; returns (position, value_out ≥ 0). Residual-on-zero applies."""
    if qty_out <= 0:
        raise ValueError("issue quantity must be positive")
    if qty_out == pos.qty:
        return Position(ZERO, ZERO, pos.avg_cost), pos.value
    value_out = q_amount(qty_out * pos.avg_cost)
    if qty_out < pos.qty and value_out > pos.value:
        value_out = pos.value
    qty = q_qty(pos.qty - qty_out)
    return Position(qty, q_amount(pos.value - value_out), pos.avg_cost), value_out


def fifo_consume(layers: Sequence[LayerState], qty_out: Decimal, fallback_cost: Decimal = ZERO) -> list[LayerTake]:
    """Takes from ``layers`` (oldest first). Quantity beyond the layers (negative stock allowed) is
    returned as one take with ``index = -1`` valued at ``fallback_cost``."""
    if qty_out <= 0:
        raise ValueError("issue quantity must be positive")
    takes: list[LayerTake] = []
    left = q_qty(qty_out)
    for i, layer in enumerate(layers):
        if left <= 0:
            break
        if layer.remaining_qty <= 0:
            continue
        if left >= layer.remaining_qty:
            takes.append(LayerTake(i, layer.remaining_qty, layer.remaining_value))
            left = q_qty(left - layer.remaining_qty)
        else:
            unit = layer.remaining_value / layer.remaining_qty
            takes.append(LayerTake(i, left, q_amount(left * unit)))
            left = ZERO
    if left > 0:
        takes.append(LayerTake(-1, left, q_amount(left * fallback_cost)))
    return takes


def apply_takes(layers: Sequence[LayerState], takes: Sequence[LayerTake]) -> list[LayerState]:
    out = list(layers)
    for t in takes:
        if t.index < 0:
            continue
        cur = out[t.index]
        out[t.index] = LayerState(q_qty(cur.remaining_qty - t.qty), q_amount(cur.remaining_value - t.value))
    return out


def fifo_issue(pos: Position, layers: Sequence[LayerState], qty_out: Decimal) -> tuple[Position, list[LayerTake]]:
    takes = fifo_consume(layers, qty_out, pos.avg_cost)
    value_out = q_amount(sum((t.value for t in takes), ZERO))
    qty = q_qty(pos.qty - qty_out)
    value = ZERO if qty == 0 else q_amount(pos.value - value_out)
    if qty == 0:
        value_out = pos.value  # residual-on-zero
    return Position(qty, value, average_of(qty, value, pos.avg_cost)), takes


def cost_preview(pos: Position, qty: Decimal) -> Decimal:
    """Value an issue of ``qty`` would carry under the average method (no state change)."""
    return wac_issue(pos, qty)[1] if qty > 0 else ZERO
