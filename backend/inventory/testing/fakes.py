"""In-memory ``Inventory`` fake for other modules' tests (P10, P11, P12, P14, P16; P09b/P09c).

It shares the pure math with the real service — ``inventory.valuation.core`` (weighted average with the
residual-on-zero rule) and ``inventory.presentations.core`` (exact presentation conversion) — so the fake and
:class:`inventory.stock.service.InventoryStockService` cannot diverge on quantities or values. Keys are
``(item, warehouse, location, lot)``; ``location_id=None`` means the warehouse's default bin. No persistence,
no GL; errors use the real codes (``stock_insufficient``, ``reservation_conflict``, ``transfer_cross_company``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal

from inventory.errors import error
from inventory.valuation import core as val
from kernel import ids
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import Availability, StockLine, StockMoveResult
from kernel.money import q_amount
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
Key = tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None]


class FakeInventoryService:
    """Implements :class:`kernel.interfaces.stock.Inventory` in memory."""

    def __init__(
        self, warehouse_company: dict[uuid.UUID, uuid.UUID] | None = None, allow_negative: bool = False
    ) -> None:
        self.warehouse_company = dict(warehouse_company or {})
        self.allow_negative = allow_negative
        self.on_hand: dict[Key, Decimal] = defaultdict(Decimal)
        self.positions: dict[tuple[uuid.UUID, uuid.UUID], val.Position] = {}
        self.reservations: dict[uuid.UUID, tuple[Key, Decimal, DocumentRef, str]] = {}
        self.moves: dict[tuple[DocumentRef, str], list[tuple[uuid.UUID, StockLine, Decimal]]] = {}
        self.transfers: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, list[StockLine], list[uuid.UUID]]] = {}
        self.held: set[uuid.UUID] = set()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _key(line: StockLine) -> Key:
        return (line.item_id, line.warehouse_id, line.location_id, line.lot_id)

    def _reserved(self, key: Key) -> Decimal:
        return sum((q for k, q, _s, _r in self.reservations.values() if k == key), ZERO)

    def _pos(self, company_id: uuid.UUID, item_id: uuid.UUID) -> val.Position:
        return self.positions.get((company_id, item_id), val.Position(ZERO, ZERO, ZERO))

    def _check_company(self, company_id: uuid.UUID, warehouse_id: uuid.UUID) -> None:
        owner = self.warehouse_company.get(warehouse_id)
        if owner is not None and owner != company_id:
            raise error("transfer_cross_company", "warehouse of another company")

    def _result(self, source: DocumentRef, kind: str) -> StockMoveResult:
        rec = self.moves[(source, kind)]
        return StockMoveResult(tuple(m for m, _l, _v in rec), q_amount(sum((abs(v) for _m, _l, v in rec), ZERO)))

    # ------------------------------------------------------------ StockService
    def receive(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        if (source, "in") in self.moves:
            return self._result(source, "in")
        rec = []
        for line in sorted(lines, key=lambda x: (x.item_id, x.warehouse_id, x.lot_id or uuid.UUID(int=0))):
            self._check_company(company_id, line.warehouse_id)
            if line.quantity <= 0 or line.unit_cost is None:
                raise ValidationFailed("receipt needs positive quantity and unit_cost")
            pos, value_in = val.wac_receive(self._pos(company_id, line.item_id), line.quantity, line.unit_cost)
            self.positions[(company_id, line.item_id)] = pos
            self.on_hand[self._key(line)] += line.quantity
            rec.append((ids.new_id(), line, value_in))
        self.moves[(source, "in")] = rec
        return self._result(source, "in")

    def issue(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        if (source, "out") in self.moves:
            return self._result(source, "out")
        rec = []
        for line in sorted(lines, key=lambda x: (x.item_id, x.warehouse_id, x.lot_id or uuid.UUID(int=0))):
            key = self._key(line)
            own = [
                rid
                for rid, (k, _q, s, r) in self.reservations.items()
                if s == source and r == line.line_ref and k == key
            ]
            own_qty = sum((self.reservations[r][1] for r in own), ZERO)
            available = self.on_hand[key] - (self._reserved(key) - own_qty)
            if line.quantity > available and not self.allow_negative:
                raise error("stock_insufficient", f"{line.quantity} requested, {available} available")
            for rid in own:
                self.reservations.pop(rid, None)
            pos, value_out = val.wac_issue(self._pos(company_id, line.item_id), line.quantity)
            self.positions[(company_id, line.item_id)] = pos
            self.on_hand[key] -= line.quantity
            rec.append((ids.new_id(), line, -value_out))
        self.moves[(source, "out")] = rec
        return self._result(source, "out")

    def return_moves(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, original: DocumentRef
    ) -> StockMoveResult:
        if (source, "ret") in self.moves:
            return self._result(source, "ret")
        originals = self.moves.get((original, "out"), []) + self.moves.get((original, "in"), [])
        if not originals:
            raise NotFound("original moves not found")
        rec = []
        for _mid, line, value in originals:
            key = self._key(line)
            pos = self._pos(company_id, line.item_id)
            if value < 0:  # an issue comes back at its original cost
                qty, amount = pos.qty + line.quantity, pos.value - value
                self.on_hand[key] += line.quantity
            else:
                qty, amount = pos.qty - line.quantity, pos.value - value
                self.on_hand[key] -= line.quantity
            self.positions[(company_id, line.item_id)] = val.Position(
                qty, q_amount(amount), val.average_of(qty, amount, pos.avg_cost)
            )
            rec.append((ids.new_id(), line, -value))
        self.moves[(source, "ret")] = rec
        return self._result(source, "ret")

    # ------------------------------------------------------------ Inventory
    def availability(
        self, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> list[Availability]:
        totals: dict[uuid.UUID, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        for key, qty in self.on_hand.items():
            if key[0] == item_id and (warehouse_id is None or key[1] == warehouse_id):
                totals[key[1]][0] += qty
                totals[key[1]][1] += self._reserved(key)
        return [Availability(item_id, w, t[0], t[1], t[0] - t[1]) for w, t in sorted(totals.items())]

    def reserve(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine]
    ) -> list[uuid.UUID]:
        uow.ctx.require_company(company_id)
        out = []
        for line in lines:
            same = [rid for rid, (_k, q, s, r) in self.reservations.items() if s == source and r == line.line_ref]
            if same:
                if self.reservations[same[0]][1] != line.quantity:
                    raise error("reservation_conflict", "another reservation exists for this source line")
                out.append(same[0])
                continue
            key = self._key(line)
            if line.quantity > self.on_hand[key] - self._reserved(key):
                raise error("stock_insufficient", "not enough available stock to reserve")
            rid = ids.new_id()
            self.reservations[rid] = (key, line.quantity, source, line.line_ref)
            out.append(rid)
        return out

    def release(self, uow: UnitOfWork, reservation_ids: list[uuid.UUID]) -> None:
        for rid in reservation_ids:
            self.reservations.pop(rid, None)

    def create_transfer(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        source: DocumentRef,
        from_warehouse: uuid.UUID,
        to_warehouse: uuid.UUID,
        lines: list[StockLine],
    ) -> uuid.UUID:
        self._check_company(company_id, from_warehouse)
        self._check_company(company_id, to_warehouse)
        if from_warehouse == to_warehouse:
            raise ValidationFailed("a transfer needs two different warehouses")
        tid = ids.new_id()
        doc = DocumentRef("inventory.transfer", tid)
        rids = self.reserve(
            uow,
            company_id,
            doc,
            [
                StockLine(f"L{i}", ln.item_id, from_warehouse, ln.quantity, ln.location_id, ln.lot_id)
                for i, ln in enumerate(lines, 1)
            ],
        )
        self.transfers[tid] = (from_warehouse, to_warehouse, list(lines), rids)
        return tid

    def quality_hold(self, uow: UnitOfWork, company_id: uuid.UUID, move_ids: list[uuid.UUID], reason: str) -> None:
        if not reason:
            raise ValidationFailed("reason required")
        self.held.update(move_ids)
