"""Pure BOM math (no ORM, no I/O): cycle detection, validity overlap, exact multi-level explosion.

Explosion semantics (F-INV-BOM, D-016):

* one batch of a BOM nominally produces ``output_qty`` of the output item; the expected good output is
  ``output_qty × yield %``;
* producing ``Q`` good units needs ``batches = Q / (output_qty × yield / 100)``;
* a component line of ``qty`` per batch with ``scrap %`` needs ``qty × batches × (1 + scrap / 100)``;
* a byproduct line of ``qty`` per batch yields ``qty × batches`` (never exploded further);
* a component that itself has an ACTIVE production BOM on the date is exploded (multi-level); one without
  is a leaf (bought or stocked item).

All intermediate arithmetic uses :class:`fractions.Fraction`, so the result is exact; only the leaf
quantities are quantized (NUMERIC(20,6), half-up) when they leave this module.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

MAX_LEVELS = 20
HUNDRED = Fraction(100)


class BomCycle(ValueError):
    def __init__(self, path: list[uuid.UUID]) -> None:
        super().__init__("cycle: " + " → ".join(str(p) for p in path))
        self.path = path


@dataclass(frozen=True, slots=True)
class Component:
    item_id: uuid.UUID
    quantity: Decimal  # per batch, base UoM
    scrap_percent: Decimal = Decimal(0)
    is_byproduct: bool = False
    cost_share_percent: Decimal | None = None
    presentation_id: uuid.UUID | None = None
    is_optional: bool = False
    line_no: int = 0


@dataclass(frozen=True, slots=True)
class BomSpec:
    id: uuid.UUID
    item_id: uuid.UUID
    output_qty: Decimal
    yield_percent: Decimal
    components: tuple[Component, ...]
    version_no: int = 1
    code: str = ""


@dataclass(slots=True)
class Node:
    """One exploded requirement. ``children`` is empty for leaves (and byproducts)."""

    item_id: uuid.UUID
    qty: Fraction
    level: int
    bom_id: uuid.UUID | None = None
    line_no: int = 0
    scrap_percent: Decimal = Decimal(0)
    is_byproduct: bool = False
    cost_share_percent: Decimal | None = None
    children: list[Node] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children and not self.is_byproduct

    @property
    def quantity(self) -> Decimal:
        return to_decimal(self.qty)


def frac(value: Decimal | int | str) -> Fraction:
    return Fraction(Decimal(value))


def to_decimal(value: Fraction, places: int = 6) -> Decimal:
    """Exact fraction → Decimal quantized half-up to ``places`` decimals."""
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal(value.numerator) / Decimal(value.denominator)
        return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def batches(spec: BomSpec, qty: Decimal | Fraction) -> Fraction:
    good_per_batch = frac(spec.output_qty) * frac(spec.yield_percent) / HUNDRED
    if good_per_batch <= 0:
        raise ValueError("output_qty × yield must be positive")
    return (qty if isinstance(qty, Fraction) else frac(qty)) / good_per_batch


def requirement(c: Component, n_batches: Fraction) -> Fraction:
    base = frac(c.quantity) * n_batches
    return base if c.is_byproduct else base * (1 + frac(c.scrap_percent) / HUNDRED)


# --------------------------------------------------------------------------- graph checks


def find_cycle(edges: Mapping[uuid.UUID, Iterable[uuid.UUID]], start: uuid.UUID) -> list[uuid.UUID] | None:
    """Path ``start → … → start`` through ``edges`` (output item → component items), or ``None``."""
    stack: list[tuple[uuid.UUID, list[uuid.UUID]]] = [(start, [start])]
    seen: set[uuid.UUID] = set()
    while stack:
        node, path = stack.pop()
        for nxt in sorted(edges.get(node, ()), key=str):
            if nxt == start:
                return [*path, nxt]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, [*path, nxt]))
    return None


def overlaps(a_from: dt.date, a_to: dt.date | None, b_from: dt.date, b_to: dt.date | None) -> bool:
    """Closed date ranges, ``None`` = open-ended."""
    return (b_to is None or a_from <= b_to) and (a_to is None or b_from <= a_to)


# --------------------------------------------------------------------------- explosion


def explode(
    spec: BomSpec, qty: Decimal, resolve: Callable[[uuid.UUID], BomSpec | None], *, multi_level: bool = True
) -> Node:
    """Tree of requirements to produce ``qty`` good units of ``spec.item_id`` (``BomCycle`` on a cycle)."""
    root = Node(item_id=spec.item_id, qty=frac(qty), level=0, bom_id=spec.id)
    _expand(root, spec, resolve, [spec.item_id], multi_level)
    return root


def _expand(
    node: Node, spec: BomSpec, resolve: Callable[[uuid.UUID], BomSpec | None], path: list[uuid.UUID], multi: bool
) -> None:
    if len(path) > MAX_LEVELS:
        raise BomCycle(path)
    n = batches(spec, node.qty)
    for c in sorted(spec.components, key=lambda x: x.line_no):
        child = Node(
            item_id=c.item_id,
            qty=requirement(c, n),
            level=node.level + 1,
            line_no=c.line_no,
            scrap_percent=c.scrap_percent,
            is_byproduct=c.is_byproduct,
            cost_share_percent=c.cost_share_percent,
        )
        node.children.append(child)
        if c.is_byproduct or not multi:
            continue
        if c.item_id in path:
            raise BomCycle([*path, c.item_id])
        sub = resolve(c.item_id)
        if sub is not None:
            child.bom_id = sub.id
            _expand(child, sub, resolve, [*path, c.item_id], multi)


def leaves(root: Node) -> list[Node]:
    """Leaf requirements (aggregated per item, first-seen order); byproducts excluded."""
    out: dict[uuid.UUID, Node] = {}

    def walk(n: Node) -> None:
        for ch in n.children:
            if ch.is_byproduct:
                continue
            if ch.children:
                walk(ch)
            elif ch.item_id in out:
                out[ch.item_id].qty += ch.qty
            else:
                out[ch.item_id] = Node(item_id=ch.item_id, qty=ch.qty, level=ch.level)

    walk(root)
    return list(out.values())
