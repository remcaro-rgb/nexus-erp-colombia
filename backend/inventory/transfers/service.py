"""Warehouse transfers through an in-transit bin (F-INV-REQ T-INV-REQ-04, D-012). Same company only.

DRAFT → SUBMITTED (stock reserved in the source bins) → [APPROVED] → SHIPPED → RECEIVED; DRAFT/SUBMITTED/APPROVED
→ CANCELLED (reservations released).

* ship: source bin → TRANSITO bin of the source warehouse (``TRANSFER_OUT`` moves). In-transit stock sits in a
  TRANSIT-type bin: it is on hand exactly once (left the source bin, not yet in the destination) and never
  available. GL (same transaction): Dr ``INVENTORY_IN_TRANSIT`` / Cr ``INVENTORY`` (source warehouse) at the
  current cost, branch = source warehouse branch.
* receive: TRANSITO → destination bin (line ``to_location_id`` or putaway) (``TRANSFER_IN`` moves). GL:
  Dr ``INVENTORY`` (destination warehouse) / Cr ``INVENTORY_IN_TRANSIT`` with the shipped value, branch =
  destination warehouse branch. Company-wide valuation (ADR-008 default) is unchanged by a transfer.
* A destination warehouse of another company → ``transfer_cross_company`` (inter-company transfers are
  intercompany sale/purchase documents, P16).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from inventory.common import audit
from inventory.errors import error
from inventory.locations import service as locations
from inventory.models import Item, Location, Reservation, StockMove, Transfer, TransferLine, Warehouse
from inventory.stock.service import SERVICE as STOCK
from inventory.valuation import core as val
from kernel import clock, sequences, services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine
from kernel.models import Sequence
from kernel.money import q_amount
from kernel.uow import UnitOfWork

SOURCE_TYPE = "inventory.transfer"
SEQUENCE_CODE = "TRA"
TRANSIT_ROLE = "INVENTORY_IN_TRANSIT"
INVENTORY_ROLE = "INVENTORY"
ZERO = Decimal(0)
S = Transfer.TransferStatus


@dataclass(frozen=True, slots=True)
class TransferLineInput:
    item_id: uuid.UUID
    quantity: Decimal
    presentation_id: uuid.UUID | None = None
    qty_packages: Decimal | None = None
    lot_id: uuid.UUID | None = None
    from_location_id: uuid.UUID | None = None
    to_location_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class TransferInput:
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    document_date: dt.date
    lines: tuple[TransferLineInput, ...]
    notes: str = ""
    source: DocumentRef | None = None
    requisition: DocumentRef | None = None


def _warehouse_any_company(uow: UnitOfWork, company_id: uuid.UUID, warehouse_id: uuid.UUID, field: str) -> Warehouse:
    wh = Warehouse.objects.filter(tenant_id=uow.ctx.tenant_id, id=warehouse_id).first()
    if wh is not None and wh.company_id != company_id:
        raise error("transfer_cross_company", "transfers stay inside one company (use an intercompany document)")
    if wh is None:
        # a warehouse of a company outside the caller's scope is invisible (RLS): still cross-company or unknown
        raise error("transfer_cross_company", f"{field}: unknown warehouse or warehouse of another company")
    return wh


def _event(uow: UnitOfWork, t: Transfer, status: str) -> None:
    uow.emit("inventory.transfer.status_changed.v1", {"transfer_id": str(t.id), "to_status": status}, company_id=t.company_id)


def _number(uow: UnitOfWork, company_id: uuid.UUID, day: dt.date) -> str:
    Sequence.objects.get_or_create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        sequence_code=SEQUENCE_CODE,
        prefix="TR-",
        defaults={"padding": 6},
    )
    return sequences.allocate(company_id, SEQUENCE_CODE, "TR-", on_date=day).formatted


def create(uow: UnitOfWork, company_id: uuid.UUID, data: TransferInput) -> Transfer:
    uow.ctx.require_company(company_id)
    src = _warehouse_any_company(uow, company_id, data.from_warehouse_id, "from_warehouse_id")
    dst = _warehouse_any_company(uow, company_id, data.to_warehouse_id, "to_warehouse_id")
    if src.id == dst.id:
        raise ValidationFailed("a transfer needs two different warehouses", pointer="/to_warehouse_id", reason="same")
    if not data.lines:
        raise ValidationFailed("lines required", pointer="/lines", reason="required")
    if src.transit_location_id is None:
        raise error("location_not_leaf", f"warehouse {src.code} has no TRANSITO bin")
    t = Transfer.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        number=_number(uow, company_id, data.document_date),
        document_date=data.document_date,
        from_warehouse=src,
        to_warehouse=dst,
        transit_location_id=src.transit_location_id,
        notes=data.notes[:500],
        source_type=data.source.type if data.source else "",
        source_id=data.source.id if data.source else None,
        requisition_type=data.requisition.type if data.requisition else "",
        requisition_id=data.requisition.id if data.requisition else None,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    for n, ln in enumerate(data.lines, start=1):
        item = Item.objects.select_related("base_uom").filter(company_id=company_id, id=ln.item_id).first()
        if item is None:
            raise ValidationFailed("unknown item", pointer=f"/lines/{n - 1}/item_id", reason="not_found")
        presentation = STOCK._presentation(item, ln.presentation_id)
        from inventory.masters import quantity_in_base

        qty = quantity_in_base(item, presentation, ln.quantity, ln.qty_packages)
        if qty <= 0:
            raise ValidationFailed("quantity must be positive", pointer=f"/lines/{n - 1}/quantity", reason="positive")
        packages = STOCK._packages(item, presentation, qty)
        if ln.from_location_id:
            STOCK._location(company_id, ln.from_location_id, src.id)
        if ln.to_location_id:
            STOCK._location(company_id, ln.to_location_id, dst.id)
        TransferLine.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company_id,
            transfer=t,
            line_no=n,
            item=item,
            presentation=presentation,
            qty_packages=packages,
            quantity=qty,
            lot_id=ln.lot_id,
            from_location_id=ln.from_location_id,
            to_location_id=ln.to_location_id,
        )
    audit(uow, "create", "inventory.transfer", t, {"number": t.number})
    return t


def _lock(t: Transfer) -> Transfer:
    locked = Transfer.objects.select_for_update().filter(id=t.id).first()
    if locked is None:
        raise NotFound("transfer not found")
    return locked


def _require(t: Transfer, *allowed: str) -> None:
    if t.status not in allowed:
        raise Conflict(f"transfer is {t.status}; expected {' or '.join(allowed)}", code="invalid_state_transition")


def _doc(t: Transfer) -> DocumentRef:
    return DocumentRef(SOURCE_TYPE, t.id, t.number or None)


def _lines(t: Transfer) -> list[TransferLine]:
    return list(TransferLine.objects.filter(transfer_id=t.id).select_related("item", "presentation").order_by("line_no"))


def submit(uow: UnitOfWork, t: Transfer) -> Transfer:
    """Reserve the stock of every line in the source warehouse (D-012)."""
    t = _lock(t)
    _require(t, S.DRAFT)
    lines = _lines(t)
    for ln in lines:
        ids = STOCK.reserve(
            uow,
            t.company_id,
            _doc(t),
            [
                StockLine(
                    line_ref=f"L{ln.line_no}", item_id=ln.item_id, warehouse_id=t.from_warehouse_id,
                    quantity=ln.quantity, location_id=ln.from_location_id, lot_id=ln.lot_id,
                    presentation_id=ln.presentation_id,
                )  # fmt: skip
            ],
        )
        res = Reservation.objects.filter(id__in=ids).order_by("id")
        first = res.first()
        if first is not None and res.count() == 1:
            ln.reservation = first
            ln.from_location_id = first.location_id
            ln.lot_id = first.lot_id
            ln.save(update_fields=["reservation", "from_location", "lot"])
    t.status = S.SUBMITTED
    t.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "submit", "inventory.transfer", t)
    return t


def approve(uow: UnitOfWork, t: Transfer) -> Transfer:
    t = _lock(t)
    _require(t, S.SUBMITTED)
    if t.created_by is not None and t.created_by == uow.ctx.principal_id:
        raise Conflict("the requester cannot approve the transfer (segregation of duties)", code="sod_violation")
    t.status = S.APPROVED
    t.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "approve", "inventory.transfer", t)
    return t


def _post(uow: UnitOfWork, t: Transfer, purpose: str, day: dt.date, value_lines: list[tuple[Item, Decimal]]) -> uuid.UUID | None:
    total = q_amount(sum((v for _, v in value_lines), ZERO))
    if total == 0:
        return None
    ship = purpose == "ship"
    wh = t.from_warehouse if ship else t.to_warehouse
    lines: list[PostingLine] = []
    for item, value in value_lines:
        if value == 0:
            continue
        debit_role, credit_role = (TRANSIT_ROLE, INVENTORY_ROLE) if ship else (INVENTORY_ROLE, TRANSIT_ROLE)
        memo = f"Traslado {t.number} {item.code}"
        lines.append(PostingLine(debit_role, "DEBIT", value, item_id=item.id, warehouse_id=wh.id, memo=memo))
        lines.append(PostingLine(credit_role, "CREDIT", value, item_id=item.id, warehouse_id=wh.id, memo=memo))
    result = services.get(PostingService).post(
        uow,
        PostingRequest(
            company_id=t.company_id,
            posting_date=day,
            source=_doc(t),
            purpose=purpose,
            lines=tuple(lines),
            journal_code="INVENTORY",
            branch_id=wh.branch_id,
        ),
    )
    return result.entry_id


def ship(uow: UnitOfWork, t: Transfer, day: dt.date | None = None) -> Transfer:
    t = _lock(t)
    if t.status == S.DRAFT:
        submit(uow, t)
        t = _lock(t)
    _require(t, S.SUBMITTED, S.APPROVED)
    day = day or clock.business_date()
    transit = Location.objects.get(id=t.transit_location_id)
    values: list[tuple[Item, Decimal]] = []
    src = Warehouse.objects.get(id=t.from_warehouse_id)
    for ln in _lines(t):
        reservations = list(
            Reservation.objects.filter(
                company_id=t.company_id, source_type=SOURCE_TYPE, source_id=t.id,
                line_ref__startswith=f"L{ln.line_no}", state=Reservation.State.ACTIVE,
            ).order_by("line_ref")  # fmt: skip
        )
        if not reservations:
            raise error("reservation_conflict", f"line {ln.line_no} has no active reservation")
        line_value = ZERO
        for n, res in enumerate(reservations):
            if not (res.line_ref == f"L{ln.line_no}" or res.line_ref.startswith(f"L{ln.line_no}#")):
                continue
            move = STOCK.internal_move(
                uow, t.company_id, _doc(t), line_ref=f"{res.line_ref}", item_id=ln.item_id,
                from_location_id=res.location_id, to_location_id=transit.id, quantity=res.quantity,
                move_date=day, lot_id=res.lot_id, presentation_id=res.presentation_id,
                kind=StockMove.Kind.TRANSFER_OUT, reservation=res,
            )  # fmt: skip
            value = q_amount(res.quantity * move.unit_cost)
            line_value += value
            if n == 0 and len(reservations) == 1:
                ln.lot_id = res.lot_id
        ln.unit_cost = val.average_of(ln.quantity, line_value)
        ln.value = q_amount(line_value)
        ln.save(update_fields=["unit_cost", "value", "lot"])
        values.append((ln.item, ln.value))
    t.from_warehouse = src
    t.ship_entry_id = _post(uow, t, "ship", day, values)
    t.status = S.SHIPPED
    t.shipped_at = clock.now()
    t.save_versioned(uow.ctx.principal_id, ["status", "shipped_at", "ship_entry_id"])
    audit(uow, "ship", "inventory.transfer", t, {"value": format(sum((v for _, v in values), ZERO), "f")})
    _event(uow, t, "SHIPPED")
    return t


def receive(uow: UnitOfWork, t: Transfer, day: dt.date | None = None) -> Transfer:
    t = _lock(t)
    _require(t, S.SHIPPED)
    day = day or clock.business_date()
    dst = Warehouse.objects.get(id=t.to_warehouse_id)
    values: list[tuple[Item, Decimal]] = []
    for ln in _lines(t):
        out_moves = list(
            StockMove.objects.filter(
                company_id=t.company_id, source_type=SOURCE_TYPE, source_id=t.id,
                kind=StockMove.Kind.TRANSFER_OUT, line_ref__startswith=f"L{ln.line_no}",
            ).order_by("line_ref")  # fmt: skip
        )
        for m in out_moves:
            if not (m.line_ref == f"L{ln.line_no}" or m.line_ref.startswith(f"L{ln.line_no}#")):
                continue
            target = (
                STOCK._location(t.company_id, ln.to_location_id, dst.id) if ln.to_location_id else locations.putaway(dst, ln.item)
            )
            STOCK.internal_move(
                uow, t.company_id, _doc(t), line_ref=m.line_ref, item_id=ln.item_id,
                from_location_id=m.to_location_id or t.transit_location_id, to_location_id=target.id,
                quantity=m.quantity, move_date=day, lot_id=m.lot_id, presentation_id=m.presentation_id,
                kind=StockMove.Kind.TRANSFER_IN, cross_warehouse=True,
            )  # fmt: skip
        values.append((ln.item, ln.value or ZERO))
    t.to_warehouse = dst
    t.receive_entry_id = _post(uow, t, "receive", day, values)
    t.status = S.RECEIVED
    t.received_at = clock.now()
    t.save_versioned(uow.ctx.principal_id, ["status", "received_at", "receive_entry_id"])
    audit(uow, "receive", "inventory.transfer", t)
    _event(uow, t, "RECEIVED")
    return t


def cancel(uow: UnitOfWork, t: Transfer) -> Transfer:
    t = _lock(t)
    _require(t, S.DRAFT, S.SUBMITTED, S.APPROVED)
    active = list(
        Reservation.objects.filter(
            company_id=t.company_id, source_type=SOURCE_TYPE, source_id=t.id, state=Reservation.State.ACTIVE
        ).values_list("id", flat=True)
    )
    STOCK.release(uow, active)
    t.status = S.CANCELLED
    t.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "cancel", "inventory.transfer", t)
    _event(uow, t, "CANCELLED")
    return t
