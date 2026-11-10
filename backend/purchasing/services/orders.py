"""Purchase orders: DRAFT → SUBMITTED (approval by amount) → APPROVED → SENT/ACKNOWLEDGED →
PARTIALLY_RECEIVED → RECEIVED → CLOSED | CANCELLED | REJECTED (F-PUR-CYCLE, P11)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock
from kernel.errors import ValidationFailed
from kernel.money import UNIT_COST, q_amount, round_half_up
from kernel.uow import UnitOfWork
from purchasing.errors import InvalidStateTransition, ReasonRequired, SodViolation
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from purchasing.services.common import (
    audit,
    currency_in,
    date_in,
    decimal_in,
    dims_in,
    money_in,
    next_number,
    qty_in,
    require,
    settings_for,
    text_in,
    uuid_in,
)
from purchasing.services.suppliers import get_supplier

P = PurchaseOrder.Status
ORDER_FIELDS = (
    "document_date",
    "posting_date",
    "party",
    "currency",
    "fx_rate",
    "notes",
    "expected_date",
    "lines",
)
LINE_FIELDS = (
    "line_no",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "amount",
    "tax_code",
    "account_code",
    "dimensions",
    "warehouse_id",
)
RECEIVABLE = (P.APPROVED, P.SENT, P.ACKNOWLEDGED, P.PARTIALLY_RECEIVED)


def _transition(order: PurchaseOrder, allowed: tuple[str, ...], action: str) -> None:
    if order.status not in allowed:
        raise InvalidStateTransition(f"cannot {action} an order in status {order.status}", status_now=order.status)


def recompute_total(order: PurchaseOrder) -> None:
    order.total = sum((q_amount(line.quantity * line.unit_price) for line in order.lines.all()), Decimal(0))
    order.save(update_fields=["total"])


def total_cop(order: PurchaseOrder) -> Decimal:
    return q_amount(order.total * order.fx_rate)


def _lines(order: PurchaseOrder, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a purchase order needs at least one line", field="lines")
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown = sorted(set(line) - set(LINE_FIELDS))
        if unknown:
            raise ValidationFailed(f"unknown properties in lines[{i}]: {', '.join(unknown)}", fields=unknown)
        line_no = line.get("line_no", i + 1)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        qty, uom = qty_in(line.get("quantity"), f"lines[{i}].quantity")
        PurchaseOrderLine.objects.create(
            tenant_id=order.tenant_id,
            company_id=order.company_id,
            order=order,
            line_no=line_no,
            item_id=uuid_in(line.get("item_id"), f"lines[{i}].item_id", required=False),
            description=text_in(line.get("description"), "description", 500),
            quantity=qty,
            uom=(uom or "UND")[:20],
            unit_price=round_half_up(
                money_in(line.get("unit_price"), f"lines[{i}].unit_price", order.currency), UNIT_COST
            ),
            tax_code=text_in(line.get("tax_code"), "tax_code", 40),
            warehouse_id=uuid_in(line.get("warehouse_id"), f"lines[{i}].warehouse_id", required=False),
            dimensions=dims_in(line.get("dimensions")),
        )


def create_order(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, source: Mapping[str, Any] | None = None
) -> PurchaseOrder:
    """Manual DRAFT order (orders from an award are created by ``rfq.create_orders_from_award``)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.order.create", {"company_id": company_id})
    unknown = sorted(set(data) - set(ORDER_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    supplier = get_supplier(uow, company_id, data.get("party"))
    currency = currency_in(data.get("currency"), supplier.currency)
    fx = Decimal(1) if currency == "COP" else decimal_in(data.get("fx_rate"), "fx_rate", positive=True)
    doc_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    order = PurchaseOrder.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "order", doc_date),
        document_date=doc_date,
        expected_date=date_in(data["expected_date"], "expected_date") if data.get("expected_date") else None,
        supplier=supplier,
        currency=currency,
        fx_rate=fx,
        notes=text_in(data.get("notes"), "notes"),
        source=dict(source) if source else None,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _lines(order, data.get("lines"))
    recompute_total(order)
    audit(uow, "purchasing.order.create", "purchasing.order", order.id, company_id, {"number": order.number})
    return order


def update_order(uow: UnitOfWork, order: PurchaseOrder, data: Mapping[str, Any]) -> PurchaseOrder:
    require(uow.ctx, "purchasing.order.update", order)
    _transition(order, (P.DRAFT,), "edit")
    unknown = sorted(set(data) - set(ORDER_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    if order.rfq_id and ("party" in data or "lines" in data or "currency" in data):
        raise ValidationFailed("vendor, currency and lines of an awarded order come from the award", field="lines")
    fields: list[str] = []
    if "expected_date" in data:
        order.expected_date = date_in(data["expected_date"], "expected_date") if data["expected_date"] else None
        fields.append("expected_date")
    if "notes" in data:
        order.notes = text_in(data["notes"], "notes")
        fields.append("notes")
    if "party" in data:
        order.supplier = get_supplier(uow, order.company_id, data["party"])
        fields.append("supplier")
    order.save_versioned(uow.ctx.principal_id, fields)
    if "lines" in data:
        order.lines.all().delete()
        _lines(order, data["lines"])
        recompute_total(order)
    audit(uow, "purchasing.order.update", "purchasing.order", order.id, order.company_id, {"fields": sorted(data)})
    return order


def delete_order(uow: UnitOfWork, order: PurchaseOrder) -> None:
    require(uow.ctx, "purchasing.order.delete", order)
    _transition(order, (P.DRAFT,), "delete")
    if order.award_id:
        raise InvalidStateTransition("an order created from an award is cancelled, not deleted")
    audit(uow, "purchasing.order.delete", "purchasing.order", order.id, order.company_id, {"number": order.number})
    order.lines.all().delete()
    order.delete()


def submit_order(uow: UnitOfWork, order: PurchaseOrder) -> PurchaseOrder:
    """DRAFT → SUBMITTED; orders up to the company's auto-approve limit (COP, default 0) are APPROVED directly."""
    ctx = uow.ctx
    require(ctx, "purchasing.order.update", order)
    _transition(order, (P.DRAFT,), "submit")
    order.submitted_by = ctx.principal_id
    limit = settings_for(ctx, order.company_id).po_auto_approve_limit
    if limit > 0 and total_cop(order) <= limit:
        order.status, order.approved_at = P.APPROVED, clock.now()
    else:
        order.status = P.SUBMITTED
    order.save_versioned(ctx.principal_id, ["status", "submitted_by", "approved_at"])
    audit(uow, "purchasing.order.submit", "purchasing.order", order.id, order.company_id, {"status": order.status})
    return order


def approve_order(uow: UnitOfWork, order: PurchaseOrder, approve: bool = True, reason: str = "") -> PurchaseOrder:
    """SUBMITTED → APPROVED | REJECTED. The submitter / creator never approves (SoD, D-023)."""
    ctx = uow.ctx
    require(ctx, "purchasing.order.approve", order)
    _transition(order, (P.SUBMITTED,), "approve")
    if ctx.principal_id in (order.submitted_by, order.created_by):
        raise SodViolation("the requester of an order cannot approve it")
    if not approve and not (reason or "").strip():
        raise ReasonRequired("rejecting an order requires a reason", field="reason")
    order.status = P.APPROVED if approve else P.REJECTED
    order.approved_by, order.approved_at = ctx.principal_id, clock.now()
    order.save_versioned(ctx.principal_id, ["status", "approved_by", "approved_at"])
    audit(
        uow,
        "purchasing.order.approve",
        "purchasing.order",
        order.id,
        order.company_id,
        {"status": order.status},
        reason=reason,
    )
    return order


def send_order(uow: UnitOfWork, order: PurchaseOrder) -> PurchaseOrder:
    """APPROVED → SENT (the supplier portal of P11b acknowledges: SENT → ACKNOWLEDGED)."""
    require(uow.ctx, "purchasing.order.update", order)
    _transition(order, (P.APPROVED,), "send")
    order.status, order.sent_at = P.SENT, clock.now()
    order.save_versioned(uow.ctx.principal_id, ["status", "sent_at"])
    audit(uow, "purchasing.order.send", "purchasing.order", order.id, order.company_id, {"status": P.SENT})
    return order


def acknowledge_order(uow: UnitOfWork, order: PurchaseOrder) -> PurchaseOrder:
    """SENT → ACKNOWLEDGED (called by the supplier portal, P11b, with the portal principal's party scope)."""
    _transition(order, (P.SENT,), "acknowledge")
    order.status = P.ACKNOWLEDGED
    order.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "purchasing.order.acknowledge", "purchasing.order", order.id, order.company_id, {})
    return order


def close_order(uow: UnitOfWork, order: PurchaseOrder, reason: str = "") -> PurchaseOrder:
    """Close an order that was (partially) received; open quantities are not expected any more."""
    require(uow.ctx, "purchasing.order.update", order)
    _transition(order, (P.APPROVED, P.SENT, P.ACKNOWLEDGED, P.PARTIALLY_RECEIVED, P.RECEIVED), "close")
    if order.status != P.RECEIVED and not (reason or "").strip():
        raise ReasonRequired("closing an order with open quantities requires a reason", field="reason")
    order.status = P.CLOSED
    order.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "purchasing.order.close", "purchasing.order", order.id, order.company_id, {}, reason=reason)
    return order


def cancel_order(uow: UnitOfWork, order: PurchaseOrder, reason: str) -> PurchaseOrder:
    require(uow.ctx, "purchasing.order.void", order)
    _transition(order, (P.DRAFT, P.SUBMITTED, P.APPROVED, P.SENT, P.ACKNOWLEDGED, P.REJECTED), "cancel")
    if not (reason or "").strip():
        raise ReasonRequired("cancelling an order requires a reason", field="reason")
    if order.lines.filter(received_qty__gt=0).exists():
        raise InvalidStateTransition("an order with receipts is closed, not cancelled")
    order.status = P.CANCELLED
    order.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "purchasing.order.cancel", "purchasing.order", order.id, order.company_id, {}, reason=reason)
    return order


def refresh_progress(uow: UnitOfWork, order: PurchaseOrder) -> None:
    """Receiving status from the line quantities; fully received and invoiced orders close (3-way match)."""
    lines = list(order.lines.all())
    received_all = all(line.received_qty >= line.quantity for line in lines)
    received_any = any(line.received_qty > 0 for line in lines)
    invoiced_all = all(line.invoiced_qty >= line.received_qty for line in lines)
    status = order.status
    if order.status in RECEIVABLE or order.status == P.RECEIVED:
        if received_all:
            status = P.CLOSED if invoiced_all and all(line.invoiced_qty > 0 for line in lines) else P.RECEIVED
        elif received_any:
            status = P.PARTIALLY_RECEIVED
    if status != order.status:
        order.status = status
        order.save_versioned(uow.ctx.principal_id, ["status"])
