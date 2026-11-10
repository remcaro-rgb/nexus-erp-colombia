"""``InventoryStockService`` — the real implementation of ``kernel.interfaces.stock.Inventory`` (P09a).

All calls run inside the caller's unit of work (``uow``) — one transaction for document + stock +
valuation (+ GL by the caller or by the transfer service). Locking order (shared/07 §7): the
``inventory_item_valuation`` rows of every item of the document in ascending ``item_id`` order, then
quants / packaged quants in ascending ``(item_id, location_id, lot_id)`` order. Every
stock-consuming step re-checks availability under those row locks (D-032), so concurrent
documents never take the same units twice and stock never goes negative unless the warehouse
allows it.

Idempotency: a move is unique per ``(source.type, source.id, line_ref, kind)``; a second call
for the same source returns the existing moves.

Presentations (D-013): packaged stock is a sub-ledger of the canonical quant; a base-unit issue
takes loose stock first and opens whole unreserved packages when needed; a presentation issue
takes whole packages. Locations (F-INV-LOC): moves post only to active leaf bins; receipts without
a bin use putaway (rule → category rule → item default bin → RECEPCION).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import F, Q, Sum

from inventory.errors import error
from inventory.locations import service as locations
from inventory.models import (
    Item,
    ItemValuation,
    Location,
    Lot,
    PackagedQuant,
    Presentation,
    Reservation,
    Status,
    StockMove,
    StockQuant,
    ValuationLayer,
    Warehouse,
)
from inventory.presentations import core as pres
from inventory.valuation import core as val
from kernel import clock
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import Availability, StockLine, StockMoveResult
from kernel.money import q_amount, q_qty, q_unit_cost
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
NIL = uuid.UUID(int=0)
HOLD_SOURCE = "inventory.quality_hold"


def _sort_key(line: StockLine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return (line.item_id, line.warehouse_id, line.lot_id or NIL)


@dataclass
class _Ctx:
    uow: UnitOfWork
    company_id: uuid.UUID
    valuations: dict[uuid.UUID, ItemValuation] = field(default_factory=dict)
    moves: list[StockMove] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PackagedAvailability:
    presentation_id: uuid.UUID
    code: str
    factor: Decimal
    packages: Decimal
    reserved_packages: Decimal
    available_packages: Decimal


@dataclass(frozen=True, slots=True)
class BinAvailability:
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    location_id: uuid.UUID
    location_code: str
    location_type: str
    lot_id: uuid.UUID | None
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    packaged_base: Decimal
    loose: Decimal
    packaged: tuple[PackagedAvailability, ...]


class InventoryStockService:
    """Implements :class:`kernel.interfaces.stock.Inventory` (+ internal moves, repack, detail availability)."""

    # ------------------------------------------------------------------ lookups
    @staticmethod
    def _warehouse(company_id: uuid.UUID, warehouse_id: uuid.UUID) -> Warehouse:
        wh = Warehouse.objects.filter(company_id=company_id, id=warehouse_id).first()
        if wh is None:
            raise NotFound("warehouse not found")
        if wh.status != Status.ACTIVE:
            raise ValidationFailed("the warehouse is archived", pointer="/warehouse_id", reason="archived")
        return wh

    @staticmethod
    def _item(company_id: uuid.UUID, item_id: uuid.UUID) -> Item:
        item = Item.objects.select_related("base_uom").filter(company_id=company_id, id=item_id).first()
        if item is None:
            raise NotFound("item not found")
        if item.item_type not in (Item.ItemType.STOCKABLE, Item.ItemType.CONSUMABLE):
            raise ValidationFailed("only stockable/consumable items move stock", pointer="/item_id", reason="item_type")
        return item

    @staticmethod
    def _location(company_id: uuid.UUID, location_id: uuid.UUID, warehouse_id: uuid.UUID) -> Location:
        loc = Location.objects.filter(company_id=company_id, id=location_id).first()
        if loc is None:
            raise error("location_cross_warehouse", "unknown location")
        return locations.assert_postable(loc, warehouse_id)

    @staticmethod
    def _lot(item: Item, lot_id: uuid.UUID | None) -> Lot | None:
        if lot_id is None:
            if item.tracking == Item.Tracking.LOT:
                raise error("lot_required", f"item {item.code} is lot-tracked")
            return None
        lot = Lot.objects.filter(company_id=item.company_id, id=lot_id, item_id=item.id).first()
        if lot is None:
            raise ValidationFailed("unknown lot for this item", pointer="/lot_id", reason="not_found")
        return lot

    @staticmethod
    def _presentation(item: Item, presentation_id: uuid.UUID | None) -> Presentation | None:
        if presentation_id is None:
            return None
        p = Presentation.objects.filter(company_id=item.company_id, id=presentation_id, item_id=item.id).first()
        if p is None:
            raise ValidationFailed("unknown presentation for this item", pointer="/presentation_id", reason="not_found")
        if p.status != Status.ACTIVE:
            raise ValidationFailed("the presentation is archived", pointer="/presentation_id", reason="archived")
        return p

    # ------------------------------------------------------------------ locks
    def _lock_valuations(self, c: _Ctx, item_ids: Iterable[uuid.UUID]) -> None:
        ctx = c.uow.ctx
        for item_id in sorted(set(item_ids)):
            if item_id in c.valuations:
                continue
            item = Item.objects.get(company_id=c.company_id, id=item_id)
            row, _ = ItemValuation.objects.select_for_update().get_or_create(
                tenant_id=ctx.tenant_id,
                company_id=c.company_id,
                item_id=item_id,
                defaults={"method": item.valuation_method},
            )
            c.valuations[item_id] = row

    @staticmethod
    def _quant(c: _Ctx, item: Item, loc: Location, lot: Lot | None) -> StockQuant:
        row, _ = StockQuant.objects.select_for_update().get_or_create(
            item_id=item.id,
            location_id=loc.id,
            lot_id=lot.id if lot else None,
            defaults={
                "tenant_id": c.uow.ctx.tenant_id,
                "company_id": c.company_id,
                "warehouse_id": loc.warehouse_id,
            },
        )
        return row

    @staticmethod
    def _packaged(c: _Ctx, item: Item, p: Presentation, loc: Location, lot: Lot | None) -> PackagedQuant:
        row, _ = PackagedQuant.objects.select_for_update().get_or_create(
            presentation_id=p.id,
            location_id=loc.id,
            lot_id=lot.id if lot else None,
            defaults={
                "tenant_id": c.uow.ctx.tenant_id,
                "company_id": c.company_id,
                "item_id": item.id,
                "warehouse_id": loc.warehouse_id,
            },
        )
        return row

    @staticmethod
    def _packaged_rows(item: Item, loc: Location, lot: Lot | None) -> list[PackagedQuant]:
        return list(
            PackagedQuant.objects.select_for_update()
            .select_related("presentation")
            .filter(item_id=item.id, location_id=loc.id, lot_id=lot.id if lot else None)
            .order_by("presentation_id")
        )

    # ------------------------------------------------------------------ moves / events
    def _record(
        self,
        c: _Ctx,
        *,
        kind: str,
        item: Item,
        warehouse: Warehouse,
        source: DocumentRef,
        line_ref: str,
        quantity: Decimal,
        move_date: dt.date,
        from_loc: Location | None = None,
        to_loc: Location | None = None,
        lot: Lot | None = None,
        presentation: Presentation | None = None,
        packages: Decimal | None = None,
        unit_cost: Decimal = ZERO,
        value: Decimal = ZERO,
        original: StockMove | None = None,
    ) -> StockMove:
        now = clock.now()
        move = StockMove.objects.create(
            tenant_id=c.uow.ctx.tenant_id,
            company_id=c.company_id,
            kind=kind,
            state=StockMove.State.DONE,
            posted=True,
            item=item,
            warehouse=warehouse,
            from_location=from_loc,
            to_location=to_loc,
            lot=lot,
            presentation=presentation,
            qty_packages=packages,
            quantity=q_qty(quantity),
            uom=item.base_uom.code,
            unit_cost=q_unit_cost(unit_cost),
            value=q_amount(value),
            source_type=source.type,
            source_id=source.id,
            source_number=source.number or "",
            line_ref=line_ref[:60],
            original_move=original,
            move_date=move_date,
            done_at=now,
            created_at=now,
            created_by=c.uow.ctx.principal_id,
        )
        payload: dict[str, object] = {
            "move_id": str(move.id),
            "item_id": str(item.id),
            "quantity": {"value": format(move.quantity, "f"), "uom": move.uom},
            "value": {"amount": format(abs(move.value), "f"), "currency": "COP"},
            "source": {"type": source.type, "id": str(source.id), "number": source.number},
        }
        if from_loc is not None:
            payload["from_location_id"] = str(from_loc.id)
        if to_loc is not None:
            payload["to_location_id"] = str(to_loc.id)
        c.uow.emit("inventory.move.done.v1", payload, company_id=c.company_id)
        c.moves.append(move)
        return move

    def _valuation_event(self, c: _Ctx, v: ItemValuation) -> None:
        c.uow.emit(
            "inventory.valuation.changed.v1",
            {"item_id": str(v.item_id), "new_unit_cost": format(v.avg_cost, "f"), "method": v.method},
            company_id=c.company_id,
        )

    @staticmethod
    def _result(moves: Sequence[StockMove]) -> StockMoveResult:
        return StockMoveResult(
            tuple(m.id for m in moves), q_amount(sum((abs(m.value) for m in moves), ZERO)), None
        )

    @staticmethod
    def _existing(company_id: uuid.UUID, source: DocumentRef, kinds: Sequence[str]) -> list[StockMove]:
        return list(
            StockMove.objects.filter(
                company_id=company_id, source_type=source.type, source_id=source.id, kind__in=kinds
            ).order_by("created_at", "id")
        )

    # ------------------------------------------------------------------ valuation primitives
    def _value_in(self, c: _Ctx, item: Item, qty: Decimal, unit_cost: Decimal) -> Decimal:
        """Valuation row update for a receipt (before the move is written); returns the value in."""
        v = c.valuations[item.id]
        pos, value_in = val.wac_receive(val.Position(v.qty, v.value, v.avg_cost), qty, unit_cost)
        v.qty, v.value, v.avg_cost, v.last_cost = pos.qty, pos.value, pos.avg_cost, q_unit_cost(unit_cost)
        v.updated_at = clock.now()
        v.save(update_fields=["qty", "value", "avg_cost", "last_cost", "updated_at"])
        self._valuation_event(c, v)
        return value_in

    def _layer(self, c: _Ctx, item: Item, move: StockMove, qty: Decimal, unit_cost: Decimal, value: Decimal) -> None:
        fifo = c.valuations[item.id].method == Item.Valuation.FIFO
        ValuationLayer.objects.create(
            tenant_id=c.uow.ctx.tenant_id,
            company_id=c.company_id,
            item=item,
            move=move,
            qty=q_qty(qty),
            unit_cost=q_unit_cost(unit_cost),
            value=value,
            remaining_qty=q_qty(qty) if fifo else ZERO,
            remaining_value=value if fifo else ZERO,
            created_at=clock.now(),
        )

    def _value_out(self, c: _Ctx, item: Item, qty: Decimal) -> Decimal:
        v = c.valuations[item.id]
        pos = val.Position(v.qty, v.value, v.avg_cost)
        if v.method == Item.Valuation.FIFO:
            layers = list(
                ValuationLayer.objects.select_for_update()
                .filter(company_id=c.company_id, item_id=item.id, remaining_qty__gt=0)
                .order_by("created_at", "id")
            )
            states = [val.LayerState(lyr.remaining_qty, lyr.remaining_value) for lyr in layers]
            new_pos, takes = val.fifo_issue(pos, states, qty)
            for t in takes:
                if t.index < 0:
                    continue
                lyr = layers[t.index]
                lyr.remaining_qty = q_qty(lyr.remaining_qty - t.qty)
                lyr.remaining_value = q_amount(lyr.remaining_value - t.value)
                lyr.save(update_fields=["remaining_qty", "remaining_value"])
            value_out = q_amount(pos.value - new_pos.value)
        else:
            new_pos, value_out = val.wac_issue(pos, qty)
        v.qty, v.value, v.avg_cost = new_pos.qty, new_pos.value, new_pos.avg_cost
        v.updated_at = clock.now()
        v.save(update_fields=["qty", "value", "avg_cost", "updated_at"])
        self._valuation_event(c, v)
        return value_out

    # ------------------------------------------------------------------ quant primitives
    def _add(
        self, c: _Ctx, item: Item, loc: Location, lot: Lot | None, qty: Decimal,
        presentation: Presentation | None, packages: Decimal | None,
    ) -> None:  # fmt: skip
        quant = self._quant(c, item, loc, lot)
        if presentation is not None and packages:
            pq = self._packaged(c, item, presentation, loc, lot)
            PackagedQuant.objects.filter(id=pq.id).update(qty_packages=F("qty_packages") + packages)
        StockQuant.objects.filter(id=quant.id).update(on_hand=F("on_hand") + qty, version=F("version") + 1)

    def _take(
        self,
        c: _Ctx,
        item: Item,
        wh: Warehouse,
        loc: Location,
        lot: Lot | None,
        qty: Decimal,
        presentation: Presentation | None,
        packages: Decimal | None,
        own: Reservation | None = None,
    ) -> None:
        """Remove ``qty`` from the bin under lock (availability re-checked; packaged sub-ledger kept)."""
        quant = self._quant(c, item, loc, lot)
        own_qty = own.quantity if own is not None else ZERO
        own_packages = (own.qty_packages or ZERO) if own is not None else ZERO
        available = quant.on_hand - (quant.reserved - own_qty)
        if qty > available and not wh.allow_negative:
            if quant.reserved - own_qty > 0 and qty <= quant.on_hand:
                raise error(
                    "stock_insufficient",
                    f"{item.code}: {qty} requested, {available} available in {loc.code} (rest reserved)",
                    reason="reserved",
                )
            raise error("stock_insufficient", f"{item.code}: {qty} requested, {available} available in {loc.code}")
        rows = self._packaged_rows(item, loc, lot)
        if presentation is not None:
            pq = next((r for r in rows if r.presentation_id == presentation.id), None)
            free = (pq.qty_packages - (pq.reserved_packages - own_packages)) if pq is not None else ZERO
            if pq is None or (packages or ZERO) > free:
                raise error(
                    "stock_insufficient",
                    f"{item.code}: {packages} × {presentation.code} requested, {free} packages available in {loc.code}",
                )
            PackagedQuant.objects.filter(id=pq.id).update(
                qty_packages=F("qty_packages") - (packages or ZERO),
                reserved_packages=F("reserved_packages") - own_packages,
            )
        else:
            plan_rows = [
                pres.Packaged(r.id, r.qty_packages, r.presentation.factor, r.reserved_packages) for r in rows
            ]
            if not wh.allow_negative or qty <= quant.on_hand:
                for row_id, opened in pres.unpack_plan(quant.on_hand, plan_rows, qty):
                    PackagedQuant.objects.filter(id=row_id).update(qty_packages=F("qty_packages") - opened)
        StockQuant.objects.filter(id=quant.id).update(
            on_hand=F("on_hand") - qty, reserved=F("reserved") - own_qty, version=F("version") + 1
        )
        if own is not None:
            own.state = Reservation.State.CONSUMED
            own.save_versioned(c.uow.ctx.principal_id, ["state"])

    def _own_reservation(self, c: _Ctx, source: DocumentRef, line_ref: str) -> Reservation | None:
        return (
            Reservation.objects.select_for_update()
            .filter(
                company_id=c.company_id,
                source_type=source.type,
                source_id=source.id,
                line_ref=line_ref,
                state=Reservation.State.ACTIVE,
            )
            .first()
        )

    def _pick_bins(
        self, item: Item, wh: Warehouse, lot: Lot | None, qty: Decimal, presentation: Presentation | None
    ) -> list[tuple[Location, Lot | None, Decimal]]:
        """Bins to issue from when the line names none: FEFO by lot expiry, then bin path."""
        qs = (
            StockQuant.objects.filter(
                item_id=item.id,
                warehouse_id=wh.id,
                location__location_type=Location.LocationType.INTERNAL,
                location__status=Status.ACTIVE,
                on_hand__gt=F("reserved"),
            )
            .select_related("location", "lot")
            .order_by(F("lot__expiry_date").asc(nulls_last=True), "location__path", "lot_id")
        )
        if lot is not None:
            qs = qs.filter(lot_id=lot.id)
        out: list[tuple[Location, Lot | None, Decimal]] = []
        left = qty
        for q in qs:
            if presentation is not None:
                pq = PackagedQuant.objects.filter(presentation_id=presentation.id, location_id=q.location_id, lot_id=q.lot_id).first()
                if pq is None or pq.qty_packages - pq.reserved_packages <= 0:
                    continue
                take = min(left, (pq.qty_packages - pq.reserved_packages) * presentation.factor, q.on_hand - q.reserved)
                take = (take // presentation.factor) * presentation.factor
            else:
                take = min(left, q.on_hand - q.reserved)
            if take <= 0:
                continue
            out.append((q.location, q.lot, take))
            left -= take
            if left <= 0:
                return out
        if wh.allow_negative:
            return [*out, (locations.by_code(wh, locations.RECEIVING_CODE), lot, left)] if left > 0 else out
        raise error("stock_insufficient", f"{item.code}: {qty} requested, {qty - left} available in {wh.code}")

    def _packages(self, item: Item, presentation: Presentation | None, qty: Decimal) -> Decimal | None:
        pres.check_precision(qty, item.base_uom.precision)
        if presentation is None:
            return None
        return pres.to_packages(qty, presentation.factor, presentation.precision)

    # ================================================================== StockService
    def receive(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        existing = self._existing(company_id, source, [StockMove.Kind.RECEIPT])
        if existing:
            return self._result(existing)
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, (ln.item_id for ln in lines))
        for line in sorted(lines, key=_sort_key):
            if line.quantity <= 0:
                raise ValidationFailed("quantity must be positive", pointer=f"/lines/{line.line_ref}", reason="positive")
            if line.unit_cost is None or line.unit_cost < 0:
                raise ValidationFailed("receipts need unit_cost ≥ 0", pointer=f"/lines/{line.line_ref}", reason="required")
            wh = self._warehouse(company_id, line.warehouse_id)
            item = self._item(company_id, line.item_id)
            lot = self._lot(item, line.lot_id)
            presentation = self._presentation(item, line.presentation_id)
            packages = self._packages(item, presentation, line.quantity)
            loc = (
                self._location(company_id, line.location_id, wh.id)
                if line.location_id
                else locations.putaway(wh, item)
            )
            self._add(c, item, loc, lot, line.quantity, presentation, packages)
            value_in = self._value_in(c, item, line.quantity, line.unit_cost)
            move = self._record(
                c, kind=StockMove.Kind.RECEIPT, item=item, warehouse=wh, source=source, line_ref=line.line_ref,
                quantity=line.quantity, move_date=move_date, to_loc=loc, lot=lot, presentation=presentation,
                packages=packages, unit_cost=line.unit_cost, value=value_in,
            )  # fmt: skip
            self._layer(c, item, move, line.quantity, line.unit_cost, value_in)
        return self._result(c.moves)

    def issue(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        existing = self._existing(company_id, source, [StockMove.Kind.ISSUE])
        if existing:
            return self._result(existing)
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, (ln.item_id for ln in lines))
        for line in sorted(lines, key=_sort_key):
            if line.quantity <= 0:
                raise ValidationFailed("quantity must be positive", pointer=f"/lines/{line.line_ref}", reason="positive")
            wh = self._warehouse(company_id, line.warehouse_id)
            item = self._item(company_id, line.item_id)
            lot = self._lot(item, line.lot_id) if line.lot_id else None
            if lot is None and line.location_id and item.tracking == Item.Tracking.LOT:
                raise error("lot_required", f"item {item.code} is lot-tracked: name the lot of the bin")
            presentation = self._presentation(item, line.presentation_id)
            self._packages(item, presentation, line.quantity)
            own = self._own_reservation(c, source, line.line_ref)
            if line.location_id:
                plan = [(self._location(company_id, line.location_id, wh.id), lot, line.quantity)]
            elif own is not None:
                plan = [(own.location, own.lot, line.quantity)]
            else:
                plan = self._pick_bins(item, wh, lot, line.quantity, presentation)
            for n, (loc, bin_lot, qty) in enumerate(plan):
                packages = self._packages(item, presentation, qty)
                use_own = own if (own is not None and own.location_id == loc.id and own.lot_id == (bin_lot.id if bin_lot else None)) else None
                self._take(c, item, wh, loc, bin_lot, qty, presentation, packages, use_own)
                value_out = self._value_out(c, item, qty)
                self._record(
                    c, kind=StockMove.Kind.ISSUE, item=item, warehouse=wh, source=source,
                    line_ref=line.line_ref if n == 0 else f"{line.line_ref}#{n}", quantity=qty, move_date=move_date,
                    from_loc=loc, lot=bin_lot, presentation=presentation, packages=packages,
                    unit_cost=val.average_of(qty, value_out), value=-value_out,
                )  # fmt: skip
        return self._result(c.moves)

    def return_moves(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, original: DocumentRef
    ) -> StockMoveResult:
        """Reverse the RECEIPT/ISSUE moves of ``original`` at their original cost, bin and lot."""
        uow.ctx.require_company(company_id)
        existing = self._existing(company_id, source, [StockMove.Kind.RETURN])
        if existing:
            return self._result(existing)
        originals = self._existing(company_id, original, [StockMove.Kind.RECEIPT, StockMove.Kind.ISSUE])
        if not originals:
            raise NotFound("original moves not found")
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, (m.item_id for m in originals))
        for m in sorted(originals, key=lambda x: (x.item_id, x.warehouse_id, x.lot_id or NIL, x.id)):
            item = self._item(company_id, m.item_id)
            wh = Warehouse.objects.get(id=m.warehouse_id)
            presentation = m.presentation
            if m.kind == StockMove.Kind.ISSUE:
                assert m.from_location is not None  # noqa: S101 - issue moves always have a source bin
                loc = locations.assert_postable(m.from_location, wh.id)
                self._add(c, item, loc, m.lot, m.quantity, presentation, m.qty_packages)
                unit = val.average_of(m.quantity, abs(m.value))
                value_in = abs(m.value)
                v = c.valuations[item.id]
                v.qty, v.value = q_qty(v.qty + m.quantity), q_amount(v.value + value_in)
                v.avg_cost = val.average_of(v.qty, v.value, v.avg_cost)
                v.save(update_fields=["qty", "value", "avg_cost"])
                self._valuation_event(c, v)
                move = self._record(
                    c, kind=StockMove.Kind.RETURN, item=item, warehouse=wh, source=source, line_ref=str(m.id)[:60],
                    quantity=m.quantity, move_date=m.move_date, to_loc=loc, lot=m.lot, presentation=presentation,
                    packages=m.qty_packages, unit_cost=unit, value=value_in, original=m,
                )  # fmt: skip
                self._layer(c, item, move, m.quantity, unit, value_in)
            else:
                assert m.to_location is not None  # noqa: S101 - receipt moves always have a target bin
                self._take(c, item, wh, m.to_location, m.lot, m.quantity, presentation, m.qty_packages)
                value_out = self._value_out(c, item, m.quantity)
                self._record(
                    c, kind=StockMove.Kind.RETURN, item=item, warehouse=wh, source=source, line_ref=str(m.id)[:60],
                    quantity=m.quantity, move_date=m.move_date, from_loc=m.to_location, lot=m.lot,
                    presentation=presentation, packages=m.qty_packages,
                    unit_cost=val.average_of(m.quantity, value_out), value=-value_out, original=m,
                )  # fmt: skip
        return self._result(c.moves)

    # ================================================================== Inventory extension
    def availability(
        self, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> list[Availability]:
        qs = StockQuant.objects.filter(
            company_id=company_id, item_id=item_id, location__location_type=Location.LocationType.INTERNAL
        )
        if warehouse_id is not None:
            qs = qs.filter(warehouse_id=warehouse_id)
        rows = qs.values("warehouse_id").annotate(on_hand=Sum("on_hand"), reserved=Sum("reserved")).order_by("warehouse_id")
        return [
            Availability(item_id, r["warehouse_id"], r["on_hand"], r["reserved"], r["on_hand"] - r["reserved"])
            for r in rows
        ]

    def detailed_availability(
        self,
        company_id: uuid.UUID,
        *,
        item_id: uuid.UUID | None = None,
        warehouse_id: uuid.UUID | None = None,
        location_id: uuid.UUID | None = None,
        lot_id: uuid.UUID | None = None,
        presentation_id: uuid.UUID | None = None,
        include_non_internal: bool = True,
    ) -> list[BinAvailability]:
        """Per (item, bin, lot): base on hand / reserved / available, packaged per presentation, loose."""
        qs = StockQuant.objects.filter(company_id=company_id).select_related("location")
        for key, value in (("item_id", item_id), ("warehouse_id", warehouse_id), ("location_id", location_id), ("lot_id", lot_id)):
            if value is not None:
                qs = qs.filter(**{key: value})
        if not include_non_internal:
            qs = qs.filter(location__location_type=Location.LocationType.INTERNAL)
        qs = qs.filter(~Q(on_hand=0) | ~Q(reserved=0)).order_by("item_id", "location__path", "lot_id")
        packaged: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None], list[PackagedQuant]] = defaultdict(list)
        pq = PackagedQuant.objects.filter(company_id=company_id).select_related("presentation")
        if item_id is not None:
            pq = pq.filter(item_id=item_id)
        if presentation_id is not None:
            pq = pq.filter(presentation_id=presentation_id)
        for p in pq:
            packaged[(p.item_id, p.location_id, p.lot_id)].append(p)
        out: list[BinAvailability] = []
        for q in qs:
            rows = sorted(packaged.get((q.item_id, q.location_id, q.lot_id), []), key=lambda r: r.presentation.code)
            if presentation_id is not None and not rows:
                continue
            pk = tuple(
                PackagedAvailability(
                    r.presentation_id, r.presentation.code, r.presentation.factor, r.qty_packages,
                    r.reserved_packages, r.qty_packages - r.reserved_packages,
                )  # fmt: skip
                for r in rows
            )
            base = sum((r.qty_packages * r.presentation.factor for r in rows), ZERO)
            all_rows = packaged.get((q.item_id, q.location_id, q.lot_id), [])
            total_packaged = sum((r.qty_packages * r.presentation.factor for r in all_rows), ZERO)
            internal = q.location.location_type == Location.LocationType.INTERNAL
            out.append(
                BinAvailability(
                    q.item_id, q.warehouse_id, q.location_id, q.location.code, q.location.location_type, q.lot_id,
                    q.on_hand, q.reserved, q_qty((q.on_hand - q.reserved) if internal else ZERO), q_qty(base),
                    q_qty(q.on_hand - total_packaged), pk,
                )  # fmt: skip
            )
        return out

    def reserve(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine]
    ) -> list[uuid.UUID]:
        """Reserve under row lock; never exceeds availability (``stock_insufficient`` / ``reservation_conflict``)."""
        uow.ctx.require_company(company_id)
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, (ln.item_id for ln in lines))
        out: list[uuid.UUID] = []
        for line in sorted(lines, key=_sort_key):
            same = Reservation.objects.filter(
                company_id=company_id, source_type=source.type, source_id=source.id,
                line_ref=line.line_ref, state=Reservation.State.ACTIVE,
            ).first()  # fmt: skip
            if same is not None:
                if same.quantity != line.quantity or same.item_id != line.item_id:
                    raise error("reservation_conflict", "another reservation exists for this source line")
                out.append(same.id)
                continue
            if line.quantity <= 0:
                raise ValidationFailed("quantity must be positive", pointer=f"/lines/{line.line_ref}", reason="positive")
            wh = self._warehouse(company_id, line.warehouse_id)
            item = self._item(company_id, line.item_id)
            lot = self._lot(item, line.lot_id) if line.lot_id else None
            if lot is None and line.location_id and item.tracking == Item.Tracking.LOT:
                raise error("lot_required", f"item {item.code} is lot-tracked: name the lot of the bin")
            presentation = self._presentation(item, line.presentation_id)
            self._packages(item, presentation, line.quantity)
            if line.location_id:
                plan = [(self._location(company_id, line.location_id, wh.id), lot, line.quantity)]
            else:
                plan = self._pick_bins(item, wh, lot, line.quantity, presentation)
            for n, (loc, bin_lot, qty) in enumerate(plan):
                packages = self._packages(item, presentation, qty)
                quant = self._quant(c, item, loc, bin_lot)
                if qty > quant.on_hand - quant.reserved:
                    raise error(
                        "stock_insufficient",
                        f"{item.code}: {qty} requested, {quant.on_hand - quant.reserved} available in {loc.code}",
                    )
                if presentation is not None:
                    pq = self._packaged(c, item, presentation, loc, bin_lot)
                    if (packages or ZERO) > pq.qty_packages - pq.reserved_packages:
                        raise error(
                            "stock_insufficient",
                            f"{packages} × {presentation.code} requested, "
                            f"{pq.qty_packages - pq.reserved_packages} packages free in {loc.code}",
                        )
                    PackagedQuant.objects.filter(id=pq.id).update(reserved_packages=F("reserved_packages") + packages)
                StockQuant.objects.filter(id=quant.id).update(reserved=F("reserved") + qty, version=F("version") + 1)
                res = Reservation.objects.create(
                    tenant_id=uow.ctx.tenant_id, company_id=company_id, item=item, warehouse=wh, location=loc,
                    lot=bin_lot, presentation=presentation, qty_packages=packages, quantity=qty,
                    source_type=source.type, source_id=source.id,
                    line_ref=line.line_ref if n == 0 else f"{line.line_ref}#{n}",
                    created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
                )  # fmt: skip
                out.append(res.id)
        return out

    def release(self, uow: UnitOfWork, reservation_ids: list[uuid.UUID]) -> None:
        rows = list(
            Reservation.objects.select_for_update()
            .filter(id__in=reservation_ids, state=Reservation.State.ACTIVE)
            .order_by("item_id", "location_id", "lot_id", "id")
        )
        for res in rows:
            uow.ctx.require_company(res.company_id)
            StockQuant.objects.filter(item_id=res.item_id, location_id=res.location_id, lot_id=res.lot_id).update(
                reserved=F("reserved") - res.quantity, version=F("version") + 1
            )
            if res.presentation_id and res.qty_packages:
                PackagedQuant.objects.filter(
                    presentation_id=res.presentation_id, location_id=res.location_id, lot_id=res.lot_id
                ).update(reserved_packages=F("reserved_packages") - res.qty_packages)
            res.state = Reservation.State.RELEASED
            res.save_versioned(uow.ctx.principal_id, ["state"])

    def create_transfer(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        source: DocumentRef,
        from_warehouse: uuid.UUID,
        to_warehouse: uuid.UUID,
        lines: list[StockLine],
    ) -> uuid.UUID:
        from inventory.transfers import service as transfers

        t = transfers.create(
            uow,
            company_id,
            transfers.TransferInput(
                from_warehouse_id=from_warehouse,
                to_warehouse_id=to_warehouse,
                document_date=clock.business_date(),
                lines=tuple(
                    transfers.TransferLineInput(
                        item_id=ln.item_id, quantity=ln.quantity, presentation_id=ln.presentation_id,
                        lot_id=ln.lot_id, from_location_id=ln.location_id,
                    )  # fmt: skip
                    for ln in lines
                ),
                source=source,
            ),
        )
        transfers.submit(uow, t)
        return t.id

    def quality_hold(self, uow: UnitOfWork, company_id: uuid.UUID, move_ids: list[uuid.UUID], reason: str) -> None:
        """Received stock on hold = an ACTIVE reservation (``inventory.quality_hold``) on its bin: not available."""
        if not reason or not reason.strip():
            raise ValidationFailed("reason required", pointer="/reason", reason="required")
        moves = StockMove.objects.filter(company_id=company_id, id__in=move_ids, kind=StockMove.Kind.RECEIPT)
        lines = [
            StockLine(
                line_ref=str(m.id)[:60], item_id=m.item_id, warehouse_id=m.warehouse_id, quantity=m.quantity,
                location_id=m.to_location_id, lot_id=m.lot_id,
            )  # fmt: skip
            for m in moves
        ]
        if len(lines) != len(set(move_ids)):
            raise NotFound("receipt move not found")
        hold = DocumentRef(HOLD_SOURCE, uuid.uuid5(uuid.NAMESPACE_URL, f"hold:{company_id}:{reason[:200]}"), reason[:60])
        self.reserve(uow, company_id, hold, lines)

    # ================================================================== bin-to-bin / repack
    def internal_move(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        source: DocumentRef,
        *,
        line_ref: str,
        item_id: uuid.UUID,
        from_location_id: uuid.UUID,
        to_location_id: uuid.UUID,
        quantity: Decimal,
        move_date: dt.date,
        lot_id: uuid.UUID | None = None,
        presentation_id: uuid.UUID | None = None,
        kind: str = StockMove.Kind.INTERNAL,
        reservation: Reservation | None = None,
        cross_warehouse: bool = False,
    ) -> StockMove:
        """Move stock between leaf bins. Same warehouse: no valuation change and no journal entry."""
        uow.ctx.require_company(company_id)
        prior = StockMove.objects.filter(
            company_id=company_id, source_type=source.type, source_id=source.id, line_ref=line_ref[:60], kind=kind
        ).first()
        if prior is not None:
            return prior
        if quantity <= 0:
            raise ValidationFailed("quantity must be positive", pointer="/quantity", reason="positive")
        src = Location.objects.filter(company_id=company_id, id=from_location_id).first()
        dst = Location.objects.filter(company_id=company_id, id=to_location_id).first()
        if src is None or dst is None:
            raise error("location_cross_warehouse", "unknown location")
        locations.assert_postable(src)
        locations.assert_postable(dst)
        if src.id == dst.id:
            raise ValidationFailed("source and target bins must differ", pointer="/to_location_id", reason="same")
        if src.warehouse_id != dst.warehouse_id and not cross_warehouse:
            raise error("location_cross_warehouse", "bin-to-bin moves stay inside one warehouse (use a transfer)")
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, [item_id])
        item = self._item(company_id, item_id)
        wh = self._warehouse(company_id, src.warehouse_id)
        lot = self._lot(item, lot_id) if lot_id or item.tracking == Item.Tracking.LOT else None
        presentation = self._presentation(item, presentation_id)
        packages = self._packages(item, presentation, quantity)
        if not dst.allow_mixed_lots and lot is not None:
            other = StockQuant.objects.filter(location_id=dst.id, on_hand__gt=0).exclude(lot_id=lot.id).exists()
            if other:
                raise ValidationFailed("the target bin does not allow mixed lots", pointer="/to_location_id", reason="mixed_lots")
        # quants in ascending location order (deadlock-free)
        first, second = sorted([src, dst], key=lambda x: x.id)
        self._quant(c, item, first, lot)
        self._quant(c, item, second, lot)
        self._take(c, item, wh, src, lot, quantity, presentation, packages, reservation)
        self._add(c, item, dst, lot, quantity, presentation, packages)
        avg = c.valuations[item.id].avg_cost
        return self._record(
            c, kind=kind, item=item, warehouse=wh, source=source, line_ref=line_ref, quantity=quantity,
            move_date=move_date, from_loc=src, to_loc=dst, lot=lot, presentation=presentation, packages=packages,
            unit_cost=avg, value=ZERO,
        )  # fmt: skip

    def repack(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        *,
        item_id: uuid.UUID,
        location_id: uuid.UUID,
        presentation_id: uuid.UUID,
        packages: Decimal,
        pack: bool,
        lot_id: uuid.UUID | None = None,
    ) -> None:
        """Loose → packaged (``pack``) or packaged → loose. Canonical stock and valuation never change."""
        uow.ctx.require_company(company_id)
        c = _Ctx(uow, company_id)
        self._lock_valuations(c, [item_id])
        item = self._item(company_id, item_id)
        loc = Location.objects.filter(company_id=company_id, id=location_id).first()
        if loc is None:
            raise error("location_cross_warehouse", "unknown location")
        locations.assert_postable(loc)
        lot = self._lot(item, lot_id) if lot_id else None
        presentation = self._presentation(item, presentation_id)
        assert presentation is not None  # noqa: S101 - presentation_id is required
        pres.check_precision(packages, presentation.precision, "package quantity")
        if packages <= 0:
            raise ValidationFailed("packages must be positive", pointer="/packages", reason="positive")
        quant = self._quant(c, item, loc, lot)
        rows = self._packaged_rows(item, loc, lot)
        pq = self._packaged(c, item, presentation, loc, lot)
        if pack:
            free_loose = pres.loose(quant.on_hand, [pres.Packaged(r.id, r.qty_packages, r.presentation.factor) for r in rows])
            if packages * presentation.factor > free_loose:
                raise error("packaged_exceeds_canonical", "not enough loose stock to fill the packages")
            PackagedQuant.objects.filter(id=pq.id).update(qty_packages=F("qty_packages") + packages)
        else:
            if packages > pq.qty_packages - pq.reserved_packages:
                raise error("stock_insufficient", "not enough unreserved packages to open")
            PackagedQuant.objects.filter(id=pq.id).update(qty_packages=F("qty_packages") - packages)


SERVICE = InventoryStockService()
