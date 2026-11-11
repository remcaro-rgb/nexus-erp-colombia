"""Posting documents of sales (shared/07 §1, C1-1/C1-2): deliveries, invoices, credit/debit notes.

Each ``post``/``validate`` runs in ONE unit of work: document state + stock issue/receipt + valuation + balanced
GL entry (P05 ``PostingService``) + AR open item + audit + outbox event. Any failure rolls everything back.
No external call happens inside the transaction: the fiscal hand-off to P15 is the outbox event
``sales.invoice.posted.v1`` (C1-10). Posted rows are immutable (DB trigger); corrections are notes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from django.db.models import F

from kernel import clock, ids, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine, StockService
from kernel.interfaces.tax import TaxComputation, TaxLineInput, TaxService
from kernel.money import minor_units, q_amount, q_qty, q_unit_cost, round_half_up
from kernel.uow import UnitOfWork
from sales import kits
from sales.errors import InvalidStateTransition, PricingPostedDocumentImmutable, TreasuryOverapplication
from sales.models import (
    ArOpenItem,
    Delivery,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    SalesNote,
    SalesNoteLine,
    SalesOrder,
    SalesOrderLine,
)
from sales.pricing import engine
from sales.services import ar
from sales.services import documents as D
from sales.services import lines as L
from sales.services.common import (
    ROLE_AR,
    ROLE_COGS,
    ROLE_INVENTORY,
    ROLE_REVENUE,
    ROLE_SALES_DISCOUNTS,
    ROLE_VAT_PAYABLE,
    audit,
    date_in,
    decimal_in,
    dimension_values,
    fiscal_number,
    lock,
    next_number,
    qty_in,
    require,
    settings_for,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)

INV = "sales.invoice"
CN, DN = "sales.credit_note", "sales.debit_note"
NOTE_REASONS = ("devolucion", "anulacion", "rebaja", "descuento", "ajuste_precio", "otro")
DISCOUNT_REASONS = ("rebaja", "descuento", "ajuste_precio")


def _draft_number() -> str:
    """Placeholder of a DRAFT fiscal document; the DIAN-range number is allocated at posting (gap-free)."""
    return f"BOR-{ids.new_id().hex[-12:].upper()}"


# --------------------------------------------------------------------------- deliveries


def create_delivery(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Delivery:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.delivery.create", {"company_id": company_id})
    unknown_keys(data, ("order", "order_id", "warehouse_id", "document_date", "notes", "lines"))
    order = lock(
        SalesOrder.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id),
        uuid_req(data.get("order", data.get("order_id")), "order"),
    )
    D.transition(order, (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_DELIVERED), "deliver")
    delivery = Delivery.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "delivery", clock.business_date()),
        document_date=date_in(data.get("document_date"), "document_date", clock.business_date()),
        order=order,
        party=order.party,
        warehouse_id=uuid_req(data.get("warehouse_id"), "warehouse_id"),
        notes=text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    order_lines = {x.line_no: x for x in order.lines.all()}
    raw = data.get("lines")
    wanted: list[tuple[SalesOrderLine, Decimal, uuid.UUID | None]] = []
    if raw in (None, []):
        for x in order_lines.values():
            if x.item_id is not None and kits.is_stocked(company_id, x.item_id) and x.quantity > x.delivered_qty:
                wanted.append((x, x.quantity - x.delivered_qty, None))
    else:
        if not isinstance(raw, list):
            raise ValidationFailed("lines must be a list", field="lines")
        for i, row in enumerate(raw):
            if not isinstance(row, Mapping):
                raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
            unknown_keys(row, ("line_no", "quantity", "lot_id"), f" in lines[{i}]")
            ol = order_lines.get(row.get("line_no"))
            if ol is None or ol.item_id is None:
                raise ValidationFailed(f"lines[{i}].line_no is not an item line of the order", field="lines")
            qty, _ = qty_in(row.get("quantity"), f"lines[{i}].quantity")
            if qty > ol.quantity - ol.delivered_qty:
                raise ValidationFailed(f"lines[{i}] delivers more than the open quantity", field="lines")
            wanted.append((ol, qty, uuid_in(row.get("lot_id"), "lot_id", required=False)))
    if not wanted:
        raise ValidationFailed("nothing to deliver", field="lines")
    for n, (ol, qty, lot) in enumerate(wanted, start=1):
        if ol.item_id is None:
            continue
        DeliveryLine.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            delivery=delivery,
            order_line=ol,
            line_no=n,
            item_id=ol.item_id,
            quantity=qty,
            lot_id=lot,
        )
    audit(uow, "sales.delivery.create", "sales.delivery", delivery.id, company_id, {"number": delivery.number})
    return delivery


def validate_delivery(uow: UnitOfWork, delivery: Delivery) -> Delivery:
    """Issue at cost (kits: components) + Dr COGS / Cr INVENTORY, in the same UoW (shared/07 §1, §3)."""
    ctx = uow.ctx
    require(ctx, "sales.delivery.update", delivery)
    delivery = lock(Delivery.objects.filter(tenant_id=ctx.tenant_id), delivery.id)
    D.transition(delivery, (Delivery.Status.DRAFT,), "validate")
    order = lock(SalesOrder.objects.filter(tenant_id=ctx.tenant_id), delivery.order_id) if delivery.order_id else None
    dlines = list(delivery.lines.select_related("order_line").order_by("line_no"))
    order_line_ids = sorted({x.order_line_id for x in dlines if x.order_line_id})
    locked = {x.id: x for x in SalesOrderLine.objects.select_for_update().filter(id__in=order_line_ids).order_by("id")}
    for ol in locked.values():
        D.release(uow, ol)
    source = DocumentRef("sales.delivery", delivery.id, delivery.number)
    issued = kits.issue_lines(
        uow,
        delivery.company_id,
        source,
        [(str(x.line_no), x.item_id, x.quantity, delivery.warehouse_id, x.lot_id) for x in dlines],
        delivery.document_date,
    )
    posting: list[PostingLine] = []
    total = Decimal(0)
    for x in dlines:
        res = issued[str(x.line_no)]
        x.cost_value, x.components, x.posted = res.value, res.components, True
        x.save(update_fields=["cost_value", "components", "posted"])
        total += res.value
        if res.value > 0:
            dims = dimension_values(x.order_line.dimensions if x.order_line else {})
            memo = f"{delivery.number} L{x.line_no}"
            posting.append(PostingLine(ROLE_COGS, "DEBIT", res.value, item_id=x.item_id, dimensions=dims, memo=memo))
            posting.append(
                PostingLine(
                    ROLE_INVENTORY,
                    "CREDIT",
                    res.value,
                    item_id=x.item_id,
                    warehouse_id=delivery.warehouse_id,
                    memo=memo,
                )
            )
        if x.order_line_id:
            locked[x.order_line_id].delivered_qty += x.quantity
            locked[x.order_line_id].save(update_fields=["delivered_qty"])
    entry = None
    if posting:
        entry = services.get(PostingService).post(
            uow,
            PostingRequest(
                delivery.company_id, delivery.document_date, source, "delivery_cogs", tuple(posting), "SALES"
            ),
        )
    delivery.status, delivery.cost_total, delivery.validated_at = Delivery.Status.DONE, total, clock.now()
    delivery.journal_entry_id = entry.entry_id if entry else None
    delivery.posted = True
    delivery.version += 1
    delivery.save()
    if order is not None:
        D._reserve(uow, order)  # re-reserve what is still open
        D.refresh_order(order)
    uow.emit(
        "sales.delivery.done.v1",
        {"delivery_id": str(delivery.id), **({"order_id": str(delivery.order_id)} if delivery.order_id else {})},
        company_id=delivery.company_id,
    )
    audit(
        uow,
        "sales.delivery.validate",
        "sales.delivery",
        delivery.id,
        delivery.company_id,
        {"journal_entry": str(delivery.journal_entry_id)},
    )
    return delivery


# --------------------------------------------------------------------------- invoices

INVOICE_FIELDS = (*D.DOC_FIELDS, "due_date", "deliver_on_invoice", "warehouse_id", "branch_id")


def _due(inv: Invoice, ctx: Any, raw: Any = None) -> dt.date:
    if raw not in (None, ""):
        return date_in(raw, "due_date")
    terms = inv.party.payment_terms_days
    if terms is None:
        terms = settings_for(ctx, inv.company_id).default_payment_terms_days
    return inv.document_date + dt.timedelta(days=int(terms))


def create_invoice(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Invoice:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.invoice.create", {"company_id": company_id})
    unknown_keys(data, INVOICE_FIELDS)
    values = D.header(uow, company_id, data)
    deliver = bool(data.get("deliver_on_invoice", False))
    inv = Invoice.objects.create(
        number=_draft_number(),
        deliver_on_invoice=deliver,
        warehouse_id=uuid_in(data.get("warehouse_id"), "warehouse_id", required=deliver),
        branch_id=uuid_in(data.get("branch_id"), "branch_id", required=False),
        **values,
    )
    inv.due_date = _due(inv, ctx, data.get("due_date"))
    inv.save(update_fields=["due_date"])
    L.write_lines(uow, inv, InvoiceLine, data.get("lines"))
    audit(uow, "sales.invoice.create", INV, inv.id, company_id, {"draft": inv.number})
    return inv


def update_invoice(uow: UnitOfWork, inv: Invoice, data: Mapping[str, Any]) -> Invoice:
    require(uow.ctx, "sales.invoice.update", inv)
    unknown_keys(data, INVOICE_FIELDS)
    inv = lock(Invoice.objects.filter(tenant_id=uow.ctx.tenant_id), inv.id)
    if inv.status != Invoice.Status.DRAFT:
        raise PricingPostedDocumentImmutable("a posted invoice cannot be changed or repriced (use a note)")
    if "deliver_on_invoice" in data:
        inv.deliver_on_invoice = bool(data["deliver_on_invoice"])
    if "warehouse_id" in data:
        inv.warehouse_id = uuid_in(data["warehouse_id"], "warehouse_id", required=False)
    if "branch_id" in data:
        inv.branch_id = uuid_in(data["branch_id"], "branch_id", required=False)
    if "due_date" in data:
        inv.due_date = _due(inv, uow.ctx, data["due_date"])
    D.update_header(uow, inv, data)
    if inv.order_id is not None and "lines" in data:
        raise ValidationFailed("lines of an invoice from an order follow the order", field="lines")
    if "lines" in data or any(k in data for k in ("party", "currency", "fx_rate", "document_date")):
        raw = data.get("lines") or [D.line_json(x) for x in inv.lines.order_by("line_no")]
        L.write_lines(uow, inv, InvoiceLine, raw)
    audit(uow, "sales.invoice.update", INV, inv.id, inv.company_id, {"fields": sorted(data)})
    return inv


def invoice_from_order(uow: UnitOfWork, order: SalesOrder, params: Mapping[str, Any]) -> Invoice:
    """DRAFT invoice for the order's delivered-not-invoiced quantities (services: ordered-not-invoiced);
    ``deliver_on_invoice`` invoices the open quantities and issues them when the invoice posts."""
    ctx = uow.ctx
    require(ctx, "sales.invoice.create", order)
    order = lock(SalesOrder.objects.filter(tenant_id=ctx.tenant_id), order.id)
    D.transition(
        order,
        (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_DELIVERED, SalesOrder.Status.DELIVERED),
        "invoice",
    )
    deliver = bool(params.get("deliver_on_invoice", False))
    warehouse = uuid_in(params.get("warehouse_id"), "warehouse_id", required=False)

    def qty(line: SalesOrderLine) -> Decimal:
        stocked = line.item_id is not None and kits.is_stocked(order.company_id, line.item_id)
        if stocked and not deliver:
            return Decimal(line.delivered_qty - line.invoiced_qty)
        return Decimal(line.quantity - max(line.invoiced_qty, line.delivered_qty if deliver else line.invoiced_qty))

    inv = Invoice.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=order.company_id,
        number=_draft_number(),
        document_date=date_in(params.get("document_date"), "document_date", clock.business_date()),
        party=order.party,
        currency=order.currency,
        fx_rate=order.fx_rate,
        channel=order.channel,
        salesperson_id=order.salesperson_id,
        order=order,
        deliver_on_invoice=deliver,
        warehouse_id=warehouse,
        source={"type": "sales.order", "id": str(order.id), "number": order.number},
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    by_no = {x.line_no: x for x in order.lines.all()}
    n = L.copy_lines(order, inv, InvoiceLine, qty_of=qty, extra=lambda line: {"order_line": line})
    if n == 0:
        raise ValidationFailed("nothing to invoice on the order (deliver first or use deliver_on_invoice)")
    if deliver:
        for line in inv.lines.all():
            if line.warehouse_id is None:
                line.warehouse_id = warehouse or by_no[line.line_no].warehouse_id
                line.save(update_fields=["warehouse_id"])
    inv.due_date = _due(inv, ctx, params.get("due_date"))
    inv.save(update_fields=["due_date"])
    audit(uow, "sales.order.to_invoice", "sales.order", order.id, order.company_id, {"invoice": str(inv.id)})
    return inv


@dataclass
class InvoicePlan:
    request: PostingRequest
    taxes: TaxComputation
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    total_cop: Decimal
    cogs: dict[uuid.UUID, Decimal] = field(default_factory=dict)


def _taxes(
    company_id: uuid.UUID,
    document_type: str,
    day: dt.date,
    party_id: uuid.UUID,
    rows: list[tuple[str, Decimal, str]],
    currency: str,
) -> TaxComputation:
    inputs = [TaxLineInput(ref, amount, (code,)) for ref, amount, code in rows if code]
    if not inputs:
        return TaxComputation(())
    comp = services.get(TaxService).compute(company_id, document_type, day, party_id, inputs)
    places = minor_units(currency)
    lines = tuple(
        type(t)(t.line_ref, t.tax_code, t.base_amount, t.rate, round_half_up(t.amount, places), t.withholding)
        for t in comp.lines
    )
    return TaxComputation(lines, comp.parameter_snapshots, comp.warnings)


def plan_invoice(
    inv: Invoice, cogs: Mapping[uuid.UUID, Decimal] | None = None, number: str | None = None
) -> InvoicePlan:
    lines = list(inv.lines.order_by("line_no"))
    taxes = _taxes(
        inv.company_id,
        INV,
        inv.document_date,
        inv.party_id,
        [(str(x.id), x.amount, x.tax_code) for x in lines],
        inv.currency,
    )
    fx = inv.fx_rate
    party = PartyRef(inv.party_id, "customer")
    memo_no = number or inv.number
    posting: list[PostingLine] = []
    credit_cop = Decimal(0)
    for x in lines:
        cop = q_amount(x.amount * fx)
        dims = dimension_values(x.dimensions)
        posting.append(
            PostingLine(
                ROLE_REVENUE,
                "CREDIT",
                cop,
                party=party,
                item_id=x.item_id,
                dimensions=dims,
                memo=f"{memo_no} L{x.line_no}",
            )
        )
        credit_cop += cop
    for t in taxes.lines:
        cop = q_amount(t.amount * fx)
        posting.append(PostingLine(ROLE_VAT_PAYABLE, "CREDIT", cop, tax_code=t.tax_code, memo=memo_no))
        credit_cop += cop
    subtotal = sum((x.amount for x in lines), Decimal(0))
    tax_total = sum((t.amount for t in taxes.lines), Decimal(0))
    total = subtotal + tax_total
    if total <= 0:
        raise ValidationFailed("the invoice total must be positive")
    posting.insert(0, PostingLine(ROLE_AR, "DEBIT", credit_cop, party=party, memo=memo_no))
    for x in lines:
        value = (cogs or {}).get(x.id, Decimal(0))
        if value > 0:
            dims = dimension_values(x.dimensions)
            posting.append(
                PostingLine(
                    ROLE_COGS, "DEBIT", value, item_id=x.item_id, dimensions=dims, memo=f"{memo_no} L{x.line_no}"
                )
            )
            posting.append(
                PostingLine(
                    ROLE_INVENTORY,
                    "CREDIT",
                    value,
                    item_id=x.item_id,
                    warehouse_id=x.warehouse_id or inv.warehouse_id,
                    memo=f"{memo_no} L{x.line_no}",
                )
            )
    request = PostingRequest(
        company_id=inv.company_id,
        posting_date=inv.document_date,
        source=DocumentRef(INV, inv.id, memo_no),
        purpose="sales_invoice",
        lines=tuple(posting),
        journal_code="SALES",
        branch_id=inv.branch_id,
    )
    return InvoicePlan(request, taxes, subtotal, tax_total, total, credit_cop, dict(cogs or {}))


def _estimated_cogs(inv: Invoice) -> dict[uuid.UUID, Decimal]:
    """Cost preview for simulations (no stock is touched): current valuation × quantity (kits: components)."""
    out: dict[uuid.UUID, Decimal] = {}
    if not inv.deliver_on_invoice:
        return out
    for x in inv.lines.all():
        if x.item_id is None or not kits.is_stocked(inv.company_id, x.item_id):
            continue
        if kits.item_is_kit(inv.company_id, x.item_id):
            parts = kits.components(inv.company_id, x.item_id, x.quantity, inv.document_date)
            out[x.id] = q_amount(
                sum(
                    ((engine.cost_cop(inv.company_id, p.item_id) or Decimal(0)) * p.quantity for p in parts), Decimal(0)
                )
            )
        else:
            out[x.id] = q_amount((engine.cost_cop(inv.company_id, x.item_id) or Decimal(0)) * x.quantity)
    return out


def _plan_json(p: InvoicePlan, currency: str, total_debit: Decimal, total_credit: Decimal) -> dict[str, Any]:
    return {
        "journal_entries": [
            {
                "lines": [
                    {
                        "account_role": x.account_role,
                        "side": x.side,
                        "amount": f"{x.amount:f}",
                        "currency": x.currency,
                        "tax_code": x.tax_code,
                        "item_id": str(x.item_id) if x.item_id else None,
                        "memo": x.memo,
                    }
                    for x in p.request.lines
                ],
                "total_debit_cop": f"{total_debit:f}",
                "total_credit_cop": f"{total_credit:f}",
            }
        ],
        "stock_moves": [{"line_id": str(k), "cost_cop": f"{v:f}"} for k, v in p.cogs.items()],
        "totals": {
            "subtotal": f"{p.subtotal:f}",
            "taxes": f"{p.tax_total:f}",
            "total": f"{p.total:f}",
            "total_cop": f"{p.total_cop:f}",
            "currency": currency,
        },
        "warnings": list(p.taxes.warnings),
    }


def simulate_invoice(uow: UnitOfWork, inv: Invoice) -> dict[str, Any]:
    """``:simulate``: journal + stock preview, nothing persisted (T-P10-15)."""
    require(uow.ctx, "sales.invoice.read", inv)
    p = plan_invoice(inv, _estimated_cogs(inv))
    res = services.get(PostingService).simulate(uow, p.request)
    return {
        "documents": [{"type": INV, "id": str(inv.id), "status": inv.status}],
        **_plan_json(p, inv.currency, res.total_debit_cop, res.total_credit_cop),
    }


def post_invoice(uow: UnitOfWork, inv: Invoice) -> Invoice:
    ctx = uow.ctx
    require(ctx, "sales.invoice.post", inv)
    inv = lock(Invoice.objects.filter(tenant_id=ctx.tenant_id), inv.id)
    if inv.status != Invoice.Status.DRAFT:
        raise InvalidStateTransition(f"a {inv.status} invoice cannot be posted", status_now=inv.status)
    L.require_overrides_approved(uow, inv)
    lines = list(inv.lines.order_by("line_no"))
    order_line_ids = sorted({x.order_line_id for x in lines if x.order_line_id})
    locked = {x.id: x for x in SalesOrderLine.objects.select_for_update().filter(id__in=order_line_ids).order_by("id")}
    for x in lines:
        if x.order_line_id:
            ol = locked[x.order_line_id]
            limit = ol.quantity - ol.invoiced_qty
            if x.quantity > limit:
                raise ValidationFailed(f"line {x.line_no} invoices more than the open order quantity", field="lines")
    number = fiscal_number(uow, inv.company_id, "invoice", inv.document_date)
    source = DocumentRef(INV, inv.id, number)
    cogs: dict[uuid.UUID, Decimal] = {}
    issued: dict[str, kits.IssuedLine] = {}
    if inv.deliver_on_invoice:
        stock_lines: list[tuple[str, uuid.UUID, Decimal, uuid.UUID, uuid.UUID | None]] = []
        for x in lines:
            if x.item_id is None or not kits.is_stocked(inv.company_id, x.item_id):
                continue
            wh = x.warehouse_id or inv.warehouse_id
            if wh is None:
                raise ValidationFailed(f"line {x.line_no} needs a warehouse (deliver on invoice)", field="warehouse_id")
            if x.order_line_id:
                D.release(uow, locked[x.order_line_id])
            stock_lines.append((str(x.line_no), x.item_id, Decimal(x.quantity), wh, None))
        issued = kits.issue_lines(uow, inv.company_id, source, stock_lines, inv.document_date)
        cogs = {x.id: issued[str(x.line_no)].value for x in lines if str(x.line_no) in issued}
    p = plan_invoice(inv, cogs, number)
    entry = services.get(PostingService).post(uow, p.request)
    for x in lines:
        if str(x.line_no) in issued:
            x.cost_value, x.components = issued[str(x.line_no)].value, issued[str(x.line_no)].components
        x.posted = True
        x.save(update_fields=["cost_value", "components", "posted"])
        if x.order_line_id:
            upd: dict[str, Any] = {"invoiced_qty": F("invoiced_qty") + x.quantity}
            if inv.deliver_on_invoice and str(x.line_no) in issued:
                upd["delivered_qty"] = F("delivered_qty") + x.quantity
            SalesOrderLine.objects.filter(id=x.order_line_id).update(**upd)
    inv.number = number
    inv.tax_total, inv.subtotal, inv.total = p.tax_total, p.subtotal, p.total
    inv.cost_total = sum(cogs.values(), Decimal(0))
    inv.status, inv.posted, inv.posted_at, inv.posted_by = Invoice.Status.POSTED, True, clock.now(), ctx.principal_id
    inv.journal_entry_id = entry.entry_id
    inv.tax_snapshot = {
        "parameters": list(p.taxes.parameter_snapshots),
        "warnings": list(p.taxes.warnings),
        "lines": [
            {"line_ref": t.line_ref, "tax_code": t.tax_code, "rate": f"{t.rate:f}", "amount": f"{t.amount:f}"}
            for t in p.taxes.lines
        ],
    }
    inv.version += 1
    inv.save()
    ar.open_item(
        uow,
        inv.party,
        source,
        inv.document_date,
        inv.due_date or inv.document_date,
        inv.currency,
        inv.fx_rate,
        p.total,
        p.total_cop,
    )
    if inv.order_id:
        D.refresh_order(SalesOrder.objects.get(id=inv.order_id))
    uow.emit(
        "sales.invoice.posted.v1",
        {
            "invoice_id": str(inv.id),
            "number": number,
            "party": {"id": str(inv.party_id), "kind": "customer"},
            "total": {"amount": f"{q_amount(p.total):f}", "currency": inv.currency},
            "journal_entry_id": str(entry.entry_id),
        },
        company_id=inv.company_id,
    )
    audit(
        uow, "sales.invoice.post", INV, inv.id, inv.company_id, {"number": number, "journal_entry": str(entry.entry_id)}
    )
    return inv


def invoice_action(uow: UnitOfWork, inv: Invoice, action: str, reason: str, params: Mapping[str, Any]) -> Any:
    if action == "post":
        return post_invoice(uow, inv)
    if action == "simulate":
        return simulate_invoice(uow, inv)
    if action == "send":
        require(uow.ctx, "sales.invoice.read", inv)
        if inv.status != Invoice.Status.POSTED:
            raise InvalidStateTransition("only a posted invoice can be sent", status_now=inv.status)
        # Delivery to the buyer (AttachedDocument, local mail capture) is done by P15 after validation; this
        # records the user's request in the audit trail only.
        audit(uow, "sales.invoice.send", INV, inv.id, inv.company_id, {"number": inv.number}, reason)
        return inv
    if action == "reprice":
        require(uow.ctx, "sales.invoice.update", inv)
        locked = lock(Invoice.objects.filter(tenant_id=uow.ctx.tenant_id), inv.id)
        if locked.status != Invoice.Status.DRAFT:
            raise PricingPostedDocumentImmutable("a posted invoice is never repriced (D-024); use a credit/debit note")
        return {"document": locked, "diff": L.reprice(uow, locked, apply=bool(params.get("apply")))}
    raise ValidationFailed(f"unknown action {action}")


# --------------------------------------------------------------------------- credit / debit notes

NOTE_FIELDS = (
    "original",
    "invoice_id",
    "reason",
    "reason_text",
    "document_date",
    "with_return",
    "warehouse_id",
    "lines",
)


def create_note(uow: UnitOfWork, company_id: uuid.UUID, kind: str, data: Mapping[str, Any]) -> SalesNote:
    ctx = uow.ctx
    ctx.require_company(company_id)
    perm = "sales.credit_note.create" if kind == SalesNote.Kind.CREDIT else "sales.debit_note.create"
    require(ctx, perm, {"company_id": company_id})
    unknown_keys(data, NOTE_FIELDS)
    inv = Invoice.objects.filter(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        id=uuid_req(data.get("original", data.get("invoice_id")), "original"),
    ).first()
    if inv is None or inv.status != Invoice.Status.POSTED:
        raise ValidationFailed("original must be a posted invoice of the company", field="original")
    reason = str(data.get("reason") or "")
    if reason not in NOTE_REASONS:
        raise ValidationFailed(f"reason must be one of {', '.join(NOTE_REASONS)}", field="reason")
    with_return = bool(data.get("with_return", False)) and kind == SalesNote.Kind.CREDIT
    note = SalesNote.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        kind=kind,
        number=_draft_number(),
        document_date=date_in(data.get("document_date"), "document_date", clock.business_date()),
        invoice=inv,
        party=inv.party,
        currency=inv.currency,
        fx_rate=inv.fx_rate,
        reason=reason,
        reason_text=text_in(data.get("reason_text"), "reason_text", 2000),
        with_return=with_return,
        warehouse_id=uuid_in(data.get("warehouse_id"), "warehouse_id", required=with_return),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    inv_lines = {str(x.id): x for x in inv.lines.all()}
    raw = data.get("lines")
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a note needs at least one line", field="lines")
    places = minor_units(inv.currency)
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, Mapping):
            raise ValidationFailed(f"lines[{i - 1}] must be an object", field="lines")
        unknown_keys(row, ("invoice_line_id", "quantity", "amount", "description", "tax_code"), f" in lines[{i - 1}]")
        il = inv_lines.get(str(row.get("invoice_line_id"))) if row.get("invoice_line_id") else None
        qty = decimal_in(row.get("quantity", "0"), "quantity", minimum=Decimal(0))
        if with_return and (il is None or il.item_id is None or qty <= 0):
            raise ValidationFailed("a return line needs invoice_line_id and quantity", field="lines")
        if il is not None and qty > il.quantity:
            raise ValidationFailed("the note quantity exceeds the invoiced quantity", field="lines")
        if row.get("amount") not in (None, ""):
            amount = round_half_up(decimal_in(row["amount"], "amount", positive=True), places)
        elif il is not None and qty > 0:
            amount = L.line_amount(qty, il.unit_price, il.discount_pct, inv.currency)
        else:
            raise ValidationFailed("each line needs an amount or an invoice line with quantity", field="lines")
        cost = Decimal(0)
        if with_return and il is not None:
            cost = q_amount(il.cost_value * qty / il.quantity) if il.cost_value else _delivered_cost(inv, il, qty)
        SalesNoteLine.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            note=note,
            invoice_line=il,
            line_no=i,
            item_id=il.item_id if il else None,
            description=text_in(row.get("description") or (il.description if il else ""), "description", 500),
            quantity=qty,
            amount=amount,
            tax_code=text_in(row.get("tax_code"), "tax_code", 40) or (il.tax_code if il else ""),
            cost_value=cost,
        )
    audit(
        uow,
        f"sales.{kind.lower()}_note.create",
        CN if kind == "CREDIT" else DN,
        note.id,
        company_id,
        {"original": inv.number},
    )
    return note


def _delivered_cost(inv: Invoice, il: InvoiceLine, qty: Decimal) -> Decimal:
    """Original issue cost of goods delivered before invoicing (delivery lines of the order line)."""
    if il.order_line_id is None:
        return Decimal(0)
    rows = DeliveryLine.objects.filter(order_line_id=il.order_line_id, posted=True)
    q = sum((r.quantity for r in rows), Decimal(0))
    v = sum((r.cost_value for r in rows), Decimal(0))
    return q_amount(v * qty / q) if q > 0 else Decimal(0)


def plan_note(note: SalesNote, number: str | None = None) -> InvoicePlan:
    lines = list(note.lines.order_by("line_no"))
    doc_type = CN if note.kind == SalesNote.Kind.CREDIT else DN
    taxes = _taxes(
        note.company_id,
        doc_type,
        note.document_date,
        note.party_id,
        [(str(x.id), x.amount, x.tax_code) for x in lines],
        note.currency,
    )
    fx, party, memo = note.fx_rate, PartyRef(note.party_id, "customer"), number or note.number
    credit = note.kind == SalesNote.Kind.CREDIT
    rev_side: Literal["DEBIT", "CREDIT"] = "DEBIT" if credit else "CREDIT"
    ar_side: Literal["DEBIT", "CREDIT"] = "CREDIT" if credit else "DEBIT"
    revenue_role = ROLE_SALES_DISCOUNTS if credit and note.reason in DISCOUNT_REASONS else ROLE_REVENUE
    posting: list[PostingLine] = []
    total_cop = Decimal(0)
    for x in lines:
        cop = q_amount(x.amount * fx)
        posting.append(
            PostingLine(revenue_role, rev_side, cop, party=party, item_id=x.item_id, memo=f"{memo} L{x.line_no}")
        )
        total_cop += cop
    for t in taxes.lines:
        cop = q_amount(t.amount * fx)
        posting.append(PostingLine(ROLE_VAT_PAYABLE, rev_side, cop, tax_code=t.tax_code, memo=memo))
        total_cop += cop
    posting.insert(0, PostingLine(ROLE_AR, ar_side, total_cop, party=party, memo=memo))
    cogs: dict[uuid.UUID, Decimal] = {}
    if credit and note.with_return:
        for x in lines:
            if x.cost_value > 0:
                cogs[x.id] = x.cost_value
                posting.append(
                    PostingLine(
                        ROLE_INVENTORY,
                        "DEBIT",
                        x.cost_value,
                        item_id=x.item_id,
                        warehouse_id=note.warehouse_id,
                        memo=f"{memo} L{x.line_no}",
                    )
                )
                posting.append(
                    PostingLine(ROLE_COGS, "CREDIT", x.cost_value, item_id=x.item_id, memo=f"{memo} L{x.line_no}")
                )
    subtotal = sum((x.amount for x in lines), Decimal(0))
    tax_total = sum((t.amount for t in taxes.lines), Decimal(0))
    req = PostingRequest(
        note.company_id,
        note.document_date,
        DocumentRef(doc_type, note.id, memo),
        "credit_note" if credit else "debit_note",
        tuple(posting),
        "SALES",
    )
    return InvoicePlan(req, taxes, subtotal, tax_total, subtotal + tax_total, total_cop, cogs)


def simulate_note(uow: UnitOfWork, note: SalesNote) -> dict[str, Any]:
    require(uow.ctx, "sales.credit_note.read" if note.kind == "CREDIT" else "sales.debit_note.read", note)
    p = plan_note(note)
    res = services.get(PostingService).simulate(uow, p.request)
    return {
        "documents": [{"type": p.request.source.type, "id": str(note.id), "status": note.status}],
        **_plan_json(p, note.currency, res.total_debit_cop, res.total_credit_cop),
    }


def post_note(uow: UnitOfWork, note: SalesNote) -> SalesNote:
    ctx = uow.ctx
    credit = note.kind == SalesNote.Kind.CREDIT
    require(ctx, "sales.credit_note.post" if credit else "sales.debit_note.post", note)
    note = lock(SalesNote.objects.filter(tenant_id=ctx.tenant_id), note.id)
    if note.status != SalesNote.Status.DRAFT:
        raise InvalidStateTransition(f"a {note.status} note cannot be posted", status_now=note.status)
    number = fiscal_number(uow, note.company_id, "credit_note" if credit else "debit_note", note.document_date)
    doc_type = CN if credit else DN
    source = DocumentRef(doc_type, note.id, number)
    lines = list(note.lines.order_by("line_no"))
    if credit and note.with_return:
        if note.warehouse_id is None:
            raise ValidationFailed("a return credit note needs warehouse_id", field="warehouse_id")
        receipt = [
            StockLine(
                str(x.line_no),
                x.item_id,
                note.warehouse_id,
                q_qty(x.quantity),
                unit_cost=q_unit_cost(x.cost_value / x.quantity),
            )
            for x in lines
            if x.item_id is not None
            and x.quantity > 0
            and x.cost_value > 0
            and not kits.item_is_kit(note.company_id, x.item_id)
        ]
        if receipt:
            services.get(StockService).receive(uow, note.company_id, source, receipt, note.document_date)
    p = plan_note(note, number)
    entry = services.get(PostingService).post(uow, p.request)
    for x in lines:
        x.posted = True
        x.save(update_fields=["posted"])
    note.number, note.subtotal, note.tax_total, note.total = number, p.subtotal, p.tax_total, p.total
    note.cost_total = sum(p.cogs.values(), Decimal(0))
    note.status, note.posted, note.posted_at, note.journal_entry_id = (
        SalesNote.Status.POSTED,
        True,
        clock.now(),
        entry.entry_id,
    )
    note.version += 1
    note.save()
    if credit:
        item = ArOpenItem.objects.filter(
            tenant_id=ctx.tenant_id, company_id=note.company_id, document_type=INV, document_id=note.invoice_id
        ).first()
        if item is None or item.balance < p.total:
            raise TreasuryOverapplication(
                "the credit note exceeds the open balance of the invoice (register an advance instead)"
            )
        ar.apply_amounts(uow, note.company_id, source, [(item.id, p.total)], note.document_date)
        uow.emit(
            "sales.credit_note.posted.v1",
            {
                "credit_note_id": str(note.id),
                "original": {"type": INV, "id": str(note.invoice_id), "number": note.invoice.number},
                "total": {"amount": f"{q_amount(p.total):f}", "currency": note.currency},
            },
            company_id=note.company_id,
        )
    else:
        due = note.document_date + dt.timedelta(days=int(note.party.payment_terms_days or 0))
        ar.open_item(
            uow, note.party, source, note.document_date, due, note.currency, note.fx_rate, p.total, p.total_cop
        )
    audit(
        uow,
        f"sales.{note.kind.lower()}_note.post",
        doc_type,
        note.id,
        note.company_id,
        {"number": number, "journal_entry": str(entry.entry_id)},
    )
    return note


__all__ = [
    "create_delivery",
    "create_invoice",
    "create_note",
    "invoice_action",
    "invoice_from_order",
    "plan_invoice",
    "post_invoice",
    "post_note",
    "simulate_invoice",
    "simulate_note",
    "update_invoice",
    "validate_delivery",
]
