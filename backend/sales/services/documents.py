"""Quotes and sales orders (non-posting documents, C1-3): DRAFT editing with the D-024 resolver, approvals,
credit control and stock reservations (``Inventory.reserve``).

Quote: DRAFT → SENT → ACCEPTED → ORDERED | CANCELLED. Order: DRAFT → (PENDING_APPROVAL) → CONFIRMED →
PARTIALLY_DELIVERED → DELIVERED → INVOICED | CANCELLED.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import Inventory, StockLine
from kernel.interfaces.workflow import WorkflowService
from kernel.money import q_amount
from kernel.uow import UnitOfWork
from sales import kits
from sales.errors import CreditLimitExceeded, InvalidStateTransition
from sales.models import ArOpenItem, Party, Quote, QuoteLine, SalesDocument, SalesOrder, SalesOrderLine
from sales.party.service import get_customer
from sales.pricing import engine
from sales.services import lines as L
from sales.services.common import (
    audit,
    currency_in,
    date_in,
    decimal_in,
    lock,
    next_number,
    require,
    settings_for,
    text_in,
    unknown_keys,
    uuid_in,
)

DOC_FIELDS = ("document_date", "party", "currency", "fx_rate", "notes", "salesperson_id", "channel", "lines")
QUOTE_FIELDS = (*DOC_FIELDS, "valid_until")
ORDER_FIELDS = DOC_FIELDS
CREDIT_POLICY = "sales.credit_limit"
Q, OS = Quote.Status, SalesOrder.Status


def transition(doc: Any, allowed: tuple[str, ...], action: str) -> None:
    if doc.status not in allowed:
        raise InvalidStateTransition(f"cannot {action} a document in status {doc.status}", status_now=doc.status)


def header(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> dict[str, Any]:
    ctx = uow.ctx
    party = get_customer(uow, company_id, data.get("party"))
    currency = currency_in(data.get("currency"))
    day = date_in(data.get("document_date"), "document_date", clock.business_date())
    given = decimal_in(data["fx_rate"], "fx_rate", positive=True) if data.get("fx_rate") not in (None, "") else None
    fx = engine.document_fx(uow, company_id, currency, day, given)
    return {
        "tenant_id": ctx.tenant_id,
        "company_id": company_id,
        "party": party,
        "currency": currency,
        "fx_rate": fx,
        "document_date": day,
        "notes": text_in(data.get("notes"), "notes"),
        "channel": text_in(data.get("channel") or "internal", "channel", 20),
        "salesperson_id": uuid_in(data.get("salesperson_id"), "salesperson_id", required=False) or party.salesperson_id,
        "created_by": ctx.principal_id,
        "updated_by": ctx.principal_id,
    }


def update_header(uow: UnitOfWork, doc: SalesDocument, data: Mapping[str, Any]) -> None:
    if "party" in data:
        doc.party = get_customer(uow, doc.company_id, data["party"])
    if "currency" in data:
        doc.currency = currency_in(data["currency"])
    if "document_date" in data:
        doc.document_date = date_in(data["document_date"], "document_date")
    if "currency" in data or "fx_rate" in data or "document_date" in data:
        given = decimal_in(data["fx_rate"], "fx_rate", positive=True) if data.get("fx_rate") not in (None, "") else None
        doc.fx_rate = engine.document_fx(uow, doc.company_id, doc.currency, doc.document_date, given)
    if "notes" in data:
        doc.notes = text_in(data["notes"], "notes")
    if "salesperson_id" in data:
        doc.salesperson_id = uuid_in(data["salesperson_id"], "salesperson_id", required=False)
    if "channel" in data:
        doc.channel = text_in(data["channel"], "channel", 20)
    doc.version += 1
    doc.updated_by = uow.ctx.principal_id
    doc.save()


# --------------------------------------------------------------------------- quotes


def create_quote(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Quote:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.quote.create", {"company_id": company_id})
    unknown_keys(data, QUOTE_FIELDS)
    values = header(uow, company_id, data)
    quote = Quote.objects.create(
        number=next_number(uow, company_id, "quote", values["document_date"]),
        valid_until=date_in(data["valid_until"], "valid_until") if data.get("valid_until") else None,
        **values,
    )
    L.write_lines(uow, quote, QuoteLine, data.get("lines"))
    audit(uow, "sales.quote.create", "sales.quote", quote.id, company_id, {"number": quote.number})
    return quote


def update_quote(uow: UnitOfWork, quote: Quote, data: Mapping[str, Any]) -> Quote:
    require(uow.ctx, "sales.quote.update", quote)
    unknown_keys(data, QUOTE_FIELDS)
    quote = lock(Quote.objects.filter(tenant_id=uow.ctx.tenant_id), quote.id)
    transition(quote, (Q.DRAFT,), "update")
    if "valid_until" in data:
        quote.valid_until = date_in(data["valid_until"], "valid_until") if data["valid_until"] else None
    update_header(uow, quote, data)
    if "lines" in data or any(k in data for k in ("party", "currency", "fx_rate", "document_date")):
        raw = data.get("lines") or [line_json(x) for x in quote.lines.order_by("line_no")]
        L.write_lines(uow, quote, QuoteLine, raw)
    audit(uow, "sales.quote.update", "sales.quote", quote.id, quote.company_id, {"fields": sorted(data)})
    return quote


def line_json(line: Any) -> dict[str, Any]:
    """Re-submittable line (keeps a manual override as ``unit_price``)."""
    out: dict[str, Any] = {
        "line_no": line.line_no,
        "item_id": str(line.item_id) if line.item_id else None,
        "description": line.description,
        "quantity": f"{line.quantity:f}",
        "uom": line.uom,
        "discount_pct": f"{line.discount_pct:f}",
        "tax_code": line.tax_code,
        "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
        "dimensions": line.dimensions,
    }
    if line.price_source == engine.OVERRIDE:
        out["unit_price"] = f"{line.unit_price:f}"
        out["override_reason"] = ((line.price_snapshot or {}).get("override") or {}).get("reason", "")
    return out


def delete_draft(uow: UnitOfWork, doc: Any, permission: str) -> None:
    require(uow.ctx, permission, doc)
    doc = lock(type(doc).objects.filter(tenant_id=uow.ctx.tenant_id), doc.id)
    transition(doc, ("DRAFT",), "delete")
    doc.lines.all().delete()
    doc.delete()


def quote_action(uow: UnitOfWork, quote: Quote, action: str, reason: str, params: Mapping[str, Any]) -> Any:
    ctx = uow.ctx
    quote = lock(Quote.objects.filter(tenant_id=ctx.tenant_id), quote.id)
    if action == "send":
        require(ctx, "sales.quote.update", quote)
        transition(quote, (Q.DRAFT,), "send")
        L.require_overrides_approved(uow, quote)
        quote.status = Q.SENT
    elif action == "accept":
        require(ctx, "sales.quote.update", quote)
        transition(quote, (Q.SENT,), "accept")
        L.require_overrides_approved(uow, quote)
        quote.status = Q.ACCEPTED
    elif action == "to-order":
        require(ctx, "sales.order.create", quote)
        transition(quote, (Q.SENT, Q.ACCEPTED), "convert")
        L.require_overrides_approved(uow, quote)
        order = order_from_quote(uow, quote)
        quote.status, quote.order_id = Q.ORDERED, order.id
        quote.version += 1
        quote.save()
        audit(uow, "sales.quote.to_order", "sales.quote", quote.id, quote.company_id, {"order": order.number})
        return order
    elif action == "cancel":
        require(ctx, "sales.quote.void", quote)
        transition(quote, (Q.DRAFT, Q.SENT, Q.ACCEPTED), "cancel")
        quote.status = Q.CANCELLED
    elif action == "reprice":
        require(ctx, "sales.quote.update", quote)
        transition(quote, (Q.DRAFT,), "reprice")
        return {"document": quote, "diff": L.reprice(uow, quote, apply=bool(params.get("apply")))}
    else:
        raise ValidationFailed(f"unknown action {action}")
    quote.version += 1
    quote.updated_by = ctx.principal_id
    quote.save()
    audit(uow, f"sales.quote.{action}", "sales.quote", quote.id, quote.company_id, {"status": quote.status}, reason)
    return quote


def order_from_quote(uow: UnitOfWork, quote: Quote) -> SalesOrder:
    ctx = uow.ctx
    order = SalesOrder.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=quote.company_id,
        number=next_number(uow, quote.company_id, "order", clock.business_date()),
        document_date=clock.business_date(),
        party=quote.party,
        currency=quote.currency,
        fx_rate=quote.fx_rate,
        channel=quote.channel,
        salesperson_id=quote.salesperson_id,
        notes=quote.notes,
        source={"type": "sales.quote", "id": str(quote.id), "number": quote.number},
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    L.copy_lines(quote, order, SalesOrderLine)
    return order


# --------------------------------------------------------------------------- orders


def create_order(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> SalesOrder:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.order.create", {"company_id": company_id})
    unknown_keys(data, ORDER_FIELDS)
    values = header(uow, company_id, data)
    order = SalesOrder.objects.create(number=next_number(uow, company_id, "order", values["document_date"]), **values)
    L.write_lines(uow, order, SalesOrderLine, data.get("lines"))
    audit(uow, "sales.order.create", "sales.order", order.id, company_id, {"number": order.number})
    return order


def update_order(uow: UnitOfWork, order: SalesOrder, data: Mapping[str, Any]) -> SalesOrder:
    require(uow.ctx, "sales.order.update", order)
    unknown_keys(data, ORDER_FIELDS)
    order = lock(SalesOrder.objects.filter(tenant_id=uow.ctx.tenant_id), order.id)
    transition(order, (OS.DRAFT,), "update")
    update_header(uow, order, data)
    if "lines" in data or any(k in data for k in ("party", "currency", "fx_rate", "document_date")):
        raw = data.get("lines") or [line_json(x) for x in order.lines.order_by("line_no")]
        L.write_lines(uow, order, SalesOrderLine, raw)
    audit(uow, "sales.order.update", "sales.order", order.id, order.company_id, {"fields": sorted(data)})
    return order


def exposure_cop(party: Party, exclude_order: uuid.UUID | None = None) -> Decimal:
    """Open AR (COP) + confirmed orders not yet invoiced (COP) of the customer."""
    ar = ArOpenItem.objects.filter(tenant_id=party.tenant_id, company_id=party.company_id, party=party).aggregate(
        s=Sum("balance_cop")
    )["s"]
    open_orders = SalesOrder.objects.filter(
        tenant_id=party.tenant_id,
        company_id=party.company_id,
        party=party,
        status__in=(OS.CONFIRMED, OS.PARTIALLY_DELIVERED, OS.DELIVERED),
    )
    if exclude_order is not None:
        open_orders = open_orders.exclude(id=exclude_order)
    pending = Decimal(0)
    for o in open_orders:
        for line in o.lines.all():
            rest = line.quantity - line.invoiced_qty
            if rest > 0:
                pending += q_amount(L.line_amount(rest, line.unit_price, line.discount_pct, o.currency) * o.fx_rate)
    return q_amount((ar or Decimal(0)) + pending)


def overdue(party: Party, on: dt.date) -> bool:
    return ArOpenItem.objects.filter(
        tenant_id=party.tenant_id, company_id=party.company_id, party=party, due_date__lt=on, balance__gt=0
    ).exists()


def credit_check(uow: UnitOfWork, order: SalesOrder) -> str | None:
    """None when within the limit; else the reason. BLOCK mode raises ``credit_limit_exceeded``."""
    party = order.party
    reasons = []
    total_cop = q_amount(order.total * order.fx_rate)
    if party.credit_limit is not None and exposure_cop(party, order.id) + total_cop > party.credit_limit:
        reasons.append("cupo de crédito excedido")
    if overdue(party, order.document_date):
        reasons.append("cartera vencida")
    if not reasons:
        return None
    reason = "; ".join(reasons)
    if settings_for(uow.ctx, order.company_id).credit_mode == "BLOCK":
        raise CreditLimitExceeded(f"order blocked: {reason}", party=str(party.id))
    return reason


def _reserve(uow: UnitOfWork, order: SalesOrder) -> None:
    inventory = services.optional(Inventory)
    if inventory is None:
        return
    for line in order.lines.order_by("line_no"):
        if line.item_id is None or line.warehouse_id is None or not kits.is_stocked(order.company_id, line.item_id):
            continue
        if kits.item_is_kit(order.company_id, line.item_id):
            continue  # kit components are reserved/issued at delivery from the BOM explosion
        rest = line.quantity - line.delivered_qty
        if rest <= 0:
            continue
        ids = inventory.reserve(
            uow,
            order.company_id,
            DocumentRef("sales.order", order.id, order.number),
            [StockLine(str(line.line_no), line.item_id, line.warehouse_id, rest)],
        )
        line.reservation_ids = [str(i) for i in ids]
        line.save(update_fields=["reservation_ids"])


def release(uow: UnitOfWork, line: SalesOrderLine) -> None:
    inventory = services.optional(Inventory)
    if inventory is not None and line.reservation_ids:
        inventory.release(uow, [uuid.UUID(i) for i in line.reservation_ids])
    if line.reservation_ids:
        line.reservation_ids = []
        line.save(update_fields=["reservation_ids"])


def confirm_order(uow: UnitOfWork, order: SalesOrder, reason: str = "") -> SalesOrder:
    ctx = uow.ctx
    require(ctx, "sales.order.update", order)
    order = lock(SalesOrder.objects.filter(tenant_id=ctx.tenant_id), order.id)
    transition(order, (OS.DRAFT, OS.PENDING_APPROVAL), "confirm")
    L.require_overrides_approved(uow, order)
    problem = credit_check(uow, order)
    if problem is not None:
        wf = services.get(WorkflowService)
        state = wf.state(order.credit_approval_request_id) if order.credit_approval_request_id else None
        if state is None or state.state == "REJECTED":
            req = wf.request_approval(
                uow, order.company_id, DocumentRef("sales.order", order.id, order.number), CREDIT_POLICY, problem
            )
            order.credit_approval_request_id, order.status = req.id, OS.PENDING_APPROVAL
            order.version += 1
            order.save()
            audit(uow, "sales.order.credit_hold", "sales.order", order.id, order.company_id, {"reason": problem})
            return order
        if state.state != "APPROVED":
            raise CreditLimitExceeded(f"credit approval pending: {problem}", approval_request_id=str(state.id))
    _reserve(uow, order)
    order.status, order.confirmed_at = OS.CONFIRMED, clock.now()
    order.version += 1
    order.updated_by = ctx.principal_id
    order.save()
    uow.emit(
        "sales.order.confirmed.v1",
        {
            "order_id": str(order.id),
            "number": order.number,
            "party": {"id": str(order.party_id), "kind": "customer"},
            "total": {"amount": f"{q_amount(order.total):f}", "currency": order.currency},
        },
        company_id=order.company_id,
    )
    audit(uow, "sales.order.confirm", "sales.order", order.id, order.company_id, {"status": order.status}, reason)
    return order


def cancel_order(uow: UnitOfWork, order: SalesOrder, reason: str) -> SalesOrder:
    ctx = uow.ctx
    require(ctx, "sales.order.void", order)
    order = lock(SalesOrder.objects.filter(tenant_id=ctx.tenant_id), order.id)
    transition(order, (OS.DRAFT, OS.PENDING_APPROVAL, OS.CONFIRMED), "cancel")
    if order.lines.filter(delivered_qty__gt=0).exists() or order.lines.filter(invoiced_qty__gt=0).exists():
        raise InvalidStateTransition("a delivered or invoiced order cannot be cancelled", status_now=order.status)
    for line in order.lines.all():
        release(uow, line)
    order.status = OS.CANCELLED
    order.version += 1
    order.save()
    audit(uow, "sales.order.cancel", "sales.order", order.id, order.company_id, {}, reason)
    return order


def refresh_order(order: SalesOrder) -> None:
    lines = list(order.lines.all())
    if all(x.invoiced_qty >= x.quantity for x in lines):
        order.status = OS.INVOICED
    elif all(x.delivered_qty >= x.quantity or not x.item_id for x in lines):
        order.status = OS.DELIVERED
    elif any(x.delivered_qty > 0 for x in lines):
        order.status = OS.PARTIALLY_DELIVERED
    order.version += 1
    order.save(update_fields=["status", "version"])
