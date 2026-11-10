"""Goods receipts against purchase orders (partial / full, over-receipt tolerance; shared/07 §2).

``:validate`` in ONE unit of work: stock moves through ``StockService.receive`` (P09; valuation layer at
the PO price in COP at the receipt-date rate), the balanced entry Dr INVENTORY (stock lines) / Dr EXPENSE
(service lines) / Cr GRNI through ``PostingService``, the PO progress, audit and the
``purchasing.receipt.validated.v1`` event (outbox). Any failure rolls everything back.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingResult, PostingService
from kernel.interfaces.stock import StockLine, StockService
from kernel.money import UNIT_COST, q_amount, round_half_up
from kernel.uow import UnitOfWork
from purchasing.errors import InvalidStateTransition, ReceiptOverTolerance
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt, ReceiptLine
from purchasing.services import orders as order_svc
from purchasing.services.common import (
    ROLE_EXPENSE,
    ROLE_GRNI,
    ROLE_INVENTORY,
    audit,
    date_in,
    dimension_values,
    lock,
    next_number,
    qty_in,
    rate_on,
    require,
    settings_for,
    text_in,
    uuid_in,
    uuid_req,
)

DOC_TYPE = "purchasing.receipt"
RECEIPT_FIELDS = (
    "document_date",
    "posting_date",
    "order",
    "warehouse_id",
    "partial",
    "notes",
    "lines",
    "party",
    "currency",
)
LINE_FIELDS = (
    "line_no",
    "order_line_id",
    "item_id",
    "description",
    "quantity",
    "location_id",
    "lot_id",
    "presentation_id",
    "unit_price",
    "amount",
    "tax_code",
    "account_code",
    "dimensions",
)


def _lines(receipt: Receipt, order: PurchaseOrder, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a receipt needs at least one line", field="lines")
    po_lines = {line.id: line for line in order.lines.all()}
    by_no = {line.line_no: line for line in po_lines.values()}
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown = sorted(set(line) - set(LINE_FIELDS))
        if unknown:
            raise ValidationFailed(f"unknown properties in lines[{i}]: {', '.join(unknown)}", fields=unknown)
        target = None
        if line.get("order_line_id"):
            target = po_lines.get(uuid_in(line["order_line_id"], f"lines[{i}].order_line_id"))  # type: ignore[arg-type]
        elif isinstance(line.get("line_no"), int):
            target = by_no.get(line["line_no"])
        if target is None:
            raise ValidationFailed(f"lines[{i}] does not reference a line of the order", field="lines")
        qty, uom = qty_in(line.get("quantity"), f"lines[{i}].quantity")
        if uom and uom != target.uom:
            raise ValidationFailed(f"lines[{i}]: receive in the base UoM {target.uom}", field="lines")
        line_no = len(seen) + 1
        seen.add(line_no)
        ReceiptLine.objects.create(
            tenant_id=receipt.tenant_id,
            company_id=receipt.company_id,
            receipt=receipt,
            order_line=target,
            line_no=line_no,
            item_id=target.item_id,
            quantity=qty,
            location_id=uuid_in(line.get("location_id"), "location_id", required=False),
            lot_id=uuid_in(line.get("lot_id"), "lot_id", required=False),
            presentation_id=uuid_in(line.get("presentation_id"), "presentation_id", required=False),
            dimensions=target.dimensions,
        )


def create_receipt(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Receipt:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.receipt.create", {"company_id": company_id})
    unknown = sorted(set(data) - set(RECEIPT_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    order_id = uuid_req(data.get("order"), "order")
    order = PurchaseOrder.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=order_id).first()
    if order is None:
        raise ValidationFailed("order is not a purchase order of the company", field="order")
    if order.status not in order_svc.RECEIVABLE:
        raise InvalidStateTransition(f"an order in status {order.status} cannot be received")
    doc_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    receipt = Receipt.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "receipt", doc_date),
        document_date=doc_date,
        order=order,
        warehouse_id=uuid_req(data.get("warehouse_id"), "warehouse_id"),
        notes=text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _lines(receipt, order, data.get("lines"))
    audit(uow, "purchasing.receipt.create", "purchasing.receipt", receipt.id, company_id, {"order": str(order.id)})
    return receipt


def delete_receipt(uow: UnitOfWork, receipt: Receipt) -> None:
    require(uow.ctx, "purchasing.receipt.delete", receipt)
    if receipt.status != Receipt.Status.DRAFT:
        raise InvalidStateTransition("only DRAFT receipts can be deleted (validated receipts are reversed)")
    audit(uow, "purchasing.receipt.delete", DOC_TYPE, receipt.id, receipt.company_id, {"number": receipt.number})
    receipt.lines.all().delete()
    receipt.delete()


@dataclass(frozen=True)
class ReceiptPlan:
    lines: list[tuple[ReceiptLine, PurchaseOrderLine, Decimal, Decimal]]  # line, po line, unit cost COP, amount COP
    stock_lines: list[StockLine]
    posting: PostingRequest
    total_cop: Decimal


def _rate(uow: UnitOfWork, order: PurchaseOrder, day: dt.date) -> Decimal:
    """Receipt-date rate (shared/07 §2): ledger rate on/before the date, else the order's rate."""
    if order.currency == "COP":
        return Decimal(1)
    return rate_on(uow, order.company_id, order.currency, day) or order.fx_rate


def plan(uow: UnitOfWork, receipt: Receipt, *, lock_lines: bool) -> ReceiptPlan:
    """Tolerance check + the stock lines and journal entry the receipt produces (shared by validate/simulate)."""
    ctx = uow.ctx
    order = receipt.order
    tolerance = settings_for(ctx, receipt.company_id).receipt_tolerance_pct
    rate = _rate(uow, order, receipt.document_date)
    po_qs = PurchaseOrderLine.objects.filter(order=order).order_by("id")
    po_lines = {line.id: line for line in (po_qs.select_for_update() if lock_lines else po_qs)}
    incoming: dict[uuid.UUID, Decimal] = {}
    planned: list[tuple[ReceiptLine, PurchaseOrderLine, Decimal, Decimal]] = []
    for line in receipt.lines.order_by("line_no"):
        po_line = po_lines[line.order_line_id]
        incoming[po_line.id] = incoming.get(po_line.id, Decimal(0)) + line.quantity
        limit = po_line.quantity * (Decimal(100) + tolerance) / Decimal(100)
        if po_line.received_qty + incoming[po_line.id] > limit:
            raise ReceiptOverTolerance(
                f"line {po_line.line_no}: {po_line.received_qty + incoming[po_line.id]} exceeds the ordered "
                f"{po_line.quantity} + {tolerance}% tolerance",
                order_line=po_line.line_no,
                ordered=str(po_line.quantity),
                tolerance_pct=str(tolerance),
            )
        unit_cop = round_half_up(po_line.unit_price * rate, UNIT_COST)
        planned.append((line, po_line, unit_cop, q_amount(line.quantity * unit_cop)))
    party = PartyRef(order.supplier.party_id or order.supplier_id, "vendor")
    stock_lines: list[StockLine] = []
    posting_lines: list[PostingLine] = []
    for line, po_line, unit_cop, amount in planned:
        dims = dimension_values(po_line.dimensions)
        if po_line.item_id is not None:
            stock_lines.append(
                StockLine(
                    line_ref=str(line.id),
                    item_id=po_line.item_id,
                    warehouse_id=receipt.warehouse_id,
                    quantity=line.quantity,
                    location_id=line.location_id,
                    lot_id=line.lot_id,
                    presentation_id=line.presentation_id,
                    unit_cost=unit_cop,
                )
            )
            posting_lines.append(
                PostingLine(
                    ROLE_INVENTORY,
                    "DEBIT",
                    amount,
                    item_id=po_line.item_id,
                    warehouse_id=receipt.warehouse_id,
                    dimensions=dims,
                    memo=f"{receipt.number} L{line.line_no}",
                )
            )
        else:
            posting_lines.append(
                PostingLine(ROLE_EXPENSE, "DEBIT", amount, dimensions=dims, memo=f"{receipt.number} L{line.line_no}")
            )
        posting_lines.append(
            PostingLine(
                ROLE_GRNI, "CREDIT", amount, party=party, dimensions=dims, memo=f"{receipt.number} L{line.line_no}"
            )
        )
    total = sum((amount for *_x, amount in planned), Decimal(0))
    request = PostingRequest(
        company_id=receipt.company_id,
        posting_date=receipt.document_date,
        source=DocumentRef(DOC_TYPE, receipt.id, receipt.number),
        purpose="receipt",
        lines=tuple(posting_lines),
        journal_code="PURCHASES",
    )
    return ReceiptPlan(planned, stock_lines, request, total)


def simulate_receipt(uow: UnitOfWork, receipt: Receipt) -> dict[str, Any]:
    require(uow.ctx, "purchasing.receipt.read", receipt)
    p = plan(uow, receipt, lock_lines=False)
    result = services.get(PostingService).simulate(uow, p.posting)
    return simulation_json(p, result)


def simulation_json(p: ReceiptPlan, result: PostingResult) -> dict[str, Any]:
    return {
        "stock_moves": [
            {
                "line_ref": s.line_ref,
                "item_id": str(s.item_id),
                "warehouse_id": str(s.warehouse_id),
                "quantity": str(s.quantity),
                "unit_cost": str(s.unit_cost),
            }
            for s in p.stock_lines
        ],
        "journal_entries": [
            {
                "lines": [
                    {"account_role": x.account_role, "side": x.side, "amount": str(x.amount), "memo": x.memo}
                    for x in p.posting.lines
                ],
                "total_debit_cop": str(result.total_debit_cop),
                "total_credit_cop": str(result.total_credit_cop),
            }
        ],
        "total_cop": str(p.total_cop),
    }


def validate_receipt(uow: UnitOfWork, receipt: Receipt) -> Receipt:
    ctx = uow.ctx
    require(ctx, "purchasing.receipt.update", receipt)
    receipt = lock(Receipt.objects.filter(tenant_id=ctx.tenant_id), receipt.id)
    if receipt.status != Receipt.Status.DRAFT:
        raise InvalidStateTransition(f"a {receipt.status} receipt cannot be validated")
    order = lock(PurchaseOrder.objects.filter(tenant_id=ctx.tenant_id), receipt.order_id)
    if order.status not in order_svc.RECEIVABLE:
        raise InvalidStateTransition(f"an order in status {order.status} cannot be received")
    p = plan(uow, receipt, lock_lines=True)
    move_ids: list[str] = []
    if p.stock_lines:
        moves = services.get(StockService).receive(
            uow,
            receipt.company_id,
            DocumentRef(DOC_TYPE, receipt.id, receipt.number),
            p.stock_lines,
            receipt.document_date,
        )
        move_ids = [str(m) for m in moves.move_ids]
    posted = services.get(PostingService).post(uow, p.posting)
    for line, po_line, unit_cop, amount in p.lines:
        line.unit_cost_cop, line.amount_cop, line.posted = unit_cop, amount, True
        line.save(update_fields=["unit_cost_cop", "amount_cop", "posted"])
        po_line.received_qty += line.quantity
        po_line.save(update_fields=["received_qty"])
    receipt.status = Receipt.Status.VALIDATED
    receipt.total_cop, receipt.journal_entry_id, receipt.stock_move_ids = p.total_cop, posted.entry_id, move_ids
    receipt.validated_at, receipt.validated_by, receipt.posted = clock.now(), ctx.principal_id, True
    receipt.save(
        update_fields=[
            "status",
            "total_cop",
            "journal_entry_id",
            "stock_move_ids",
            "validated_at",
            "validated_by",
            "posted",
            "updated_at",
        ]
    )
    order.refresh_from_db()
    order_svc.refresh_progress(uow, order)
    uow.emit(
        "purchasing.receipt.validated.v1",
        {"receipt_id": str(receipt.id), "order_id": str(order.id), "warehouse_id": str(receipt.warehouse_id)},
        company_id=receipt.company_id,
    )
    audit(
        uow,
        "purchasing.receipt.validate",
        DOC_TYPE,
        receipt.id,
        receipt.company_id,
        {"journal_entry": str(posted.entry_id), "order_status": order.status},
    )
    return receipt
