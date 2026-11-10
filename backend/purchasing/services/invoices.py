"""Vendor invoices: three-way match (PO ↔ receipts ↔ invoice) with tolerances, in-line posting, AP item.

* ``:match`` compares every invoice line with its PO line: cumulative invoiced quantity ≤ received
  quantity (+ qty tolerance %) and unit price within the price tolerance % of the PO price, same
  currency. Lines without a PO (services / expenses) are allowed only with an approval. A mismatch
  starts an approval (``WorkflowService``, policy ``purchasing.vendor_invoice.mismatch``).
* ``:post`` re-runs the match and refuses with ``three_way_mismatch`` unless it matches or the mismatch
  approval is APPROVED. In ONE unit of work: Dr GRNI (receipt value) / Dr-Cr PURCHASE_PRICE_VARIANCE (price
  difference) / Dr EXPENSE (non-PO lines) / Dr VAT_RECOVERABLE / Cr WHT_PAYABLE / Cr AP, the AP open
  item (GL AP = Σ open items), PO invoiced quantities, audit and ``purchasing.vendor_invoice.posted.v1``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from django.db.models import F, Sum

from kernel import clock, services
from kernel.errors import Conflict, ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.tax import TaxComputation, TaxLineInput, TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.money import UNIT_COST, q_amount, round_half_up
from kernel.uow import UnitOfWork
from purchasing.errors import InvalidStateTransition, ThreeWayMismatch
from purchasing.models import (
    ApOpenItem,
    PurchaseOrder,
    PurchaseOrderLine,
    ReceiptLine,
    VendorInvoice,
    VendorInvoiceLine,
)
from purchasing.services import orders as order_svc
from purchasing.services.common import (
    ROLE_AP,
    ROLE_EXPENSE,
    ROLE_GRNI,
    ROLE_PRICE_VARIANCE,
    ROLE_VAT_RECOVERABLE,
    ROLE_WHT_PAYABLE,
    audit,
    currency_in,
    date_in,
    decimal_in,
    dimension_values,
    dims_in,
    lock,
    money_in,
    next_number,
    qty_in,
    rate_on,
    require,
    settings_for,
    text_in,
    uuid_in,
    uuid_req,
)
from purchasing.services.suppliers import get_supplier

DOC_TYPE = "purchasing.vendor_invoice"
MISMATCH_POLICY = "purchasing.vendor_invoice.mismatch"
INVOICE_FIELDS = (
    "document_date",
    "posting_date",
    "party",
    "currency",
    "fx_rate",
    "notes",
    "vendor_invoice_number",
    "due_date",
    "received_einvoice",
    "order",
    "lines",
)
LINE_FIELDS = (
    "line_no",
    "order_line_id",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "amount",
    "tax_code",
    "account_code",
    "account_role",
    "dimensions",
    "withholding_codes",
    "withholding_concept",
)
V = VendorInvoice


def _lines(invoice: VendorInvoice, raw: Any, order: PurchaseOrder | None) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a vendor invoice needs at least one line", field="lines")
    po_lines = {line.id: line for line in order.lines.all()} if order else {}
    by_no = {line.line_no: line for line in po_lines.values()}
    withholding: dict[str, Any] = {}
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown = sorted(set(line) - set(LINE_FIELDS))
        if unknown:
            raise ValidationFailed(f"unknown properties in lines[{i}]: {', '.join(unknown)}", fields=unknown)
        target: PurchaseOrderLine | None = None
        if line.get("order_line_id"):
            target = po_lines.get(uuid_in(line["order_line_id"], f"lines[{i}].order_line_id"))  # type: ignore[arg-type]
            if target is None:
                raise ValidationFailed(f"lines[{i}] references a line of another order", field="lines")
        elif order is not None and isinstance(line.get("line_no"), int) and line.get("line_no") in by_no:
            target = by_no[line["line_no"]]
        qty, uom = qty_in(line.get("quantity"), f"lines[{i}].quantity")
        price = round_half_up(money_in(line.get("unit_price"), f"lines[{i}].unit_price", invoice.currency), UNIT_COST)
        codes = line.get("withholding_codes") or []
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise ValidationFailed(f"lines[{i}].withholding_codes must be a list of codes", field="lines")
        created = VendorInvoiceLine.objects.create(
            tenant_id=invoice.tenant_id,
            company_id=invoice.company_id,
            invoice=invoice,
            line_no=i + 1,
            order_line=target,
            item_id=target.item_id if target else uuid_in(line.get("item_id"), "item_id", required=False),
            description=text_in(line.get("description") or (target.description if target else ""), "description", 500),
            quantity=qty,
            uom=(uom or (target.uom if target else "UND"))[:20],
            unit_price=price,
            amount=q_amount(qty * price),
            tax_code=text_in(line.get("tax_code", target.tax_code if target else ""), "tax_code", 40),
            account_role=text_in(line.get("account_role") or ("" if target else ROLE_EXPENSE), "account_role", 60),
            dimensions=dims_in(line.get("dimensions")) or (target.dimensions if target else {}),
        )
        if codes or line.get("withholding_concept"):
            withholding[str(created.id)] = {"codes": codes, "concept": line.get("withholding_concept")}
    invoice.tax_snapshot = {"withholding_requests": withholding}
    invoice.save(update_fields=["tax_snapshot"])


def create_invoice(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> VendorInvoice:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.vendor_invoice.create", {"company_id": company_id})
    unknown = sorted(set(data) - set(INVOICE_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    supplier = get_supplier(uow, company_id, data.get("party"))
    number = text_in(data.get("vendor_invoice_number"), "vendor_invoice_number", 60).strip()
    if not number:
        raise ValidationFailed("vendor_invoice_number is required", field="vendor_invoice_number")
    if V.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, supplier=supplier, vendor_invoice_number__iexact=number
    ).exists():
        raise Conflict(f"invoice {number} of this vendor is already registered", code="vendor_invoice_duplicate")
    order = None
    if data.get("order"):
        order = PurchaseOrder.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, id=uuid_req(data["order"], "order")
        ).first()
        if order is None or order.supplier_id != supplier.id:
            raise ValidationFailed("order is not a purchase order of this vendor", field="order")
    doc_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    currency = currency_in(data.get("currency"), order.currency if order else supplier.currency)
    if currency == "COP":
        fx = Decimal(1)
    elif data.get("fx_rate"):
        fx = decimal_in(data["fx_rate"], "fx_rate", positive=True)
    else:
        found = rate_on(uow, company_id, currency, doc_date)
        if found is None:
            raise ValidationFailed(f"no {currency} rate on {doc_date}", code="fx_rate_missing", field="fx_rate")
        fx = found
    invoice = V.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "vendor_invoice", doc_date),
        vendor_invoice_number=number,
        document_date=doc_date,
        due_date=date_in(data.get("due_date"), "due_date", doc_date + dt.timedelta(days=supplier.payment_terms_days)),
        supplier=supplier,
        order=order,
        currency=currency,
        fx_rate=fx,
        notes=text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _lines(invoice, data.get("lines"), order)
    invoice.subtotal = sum((line.amount for line in invoice.lines.all()), Decimal(0))
    invoice.save(update_fields=["subtotal"])
    audit(uow, "purchasing.vendor_invoice.create", DOC_TYPE, invoice.id, company_id, {"number": invoice.number})
    return invoice


def delete_invoice(uow: UnitOfWork, invoice: VendorInvoice) -> None:
    require(uow.ctx, "purchasing.vendor_invoice.delete", invoice)
    if invoice.status != V.Status.DRAFT:
        raise InvalidStateTransition("a posted vendor invoice is corrected with a credit note, never deleted")
    audit(uow, "purchasing.vendor_invoice.delete", DOC_TYPE, invoice.id, invoice.company_id, {"number": invoice.number})
    invoice.lines.all().delete()
    invoice.delete()


# --------------------------------------------------------------------------- three-way match


def _invoiced_before(line: VendorInvoiceLine) -> Decimal:
    if line.order_line_id is None:
        return Decimal(0)
    total = (
        VendorInvoiceLine.objects.filter(order_line_id=line.order_line_id, invoice__status=V.Status.POSTED)
        .exclude(invoice_id=line.invoice_id)
        .aggregate(s=Sum("quantity"))["s"]
    )
    return total or Decimal(0)


def three_way(uow: UnitOfWork, invoice: VendorInvoice) -> dict[str, Any]:
    """Pure check (no writes): per-line quantity / price / currency result and the overall status."""
    s = settings_for(uow.ctx, invoice.company_id)
    qty_tol, price_tol = s.invoice_qty_tolerance_pct, s.invoice_price_tolerance_pct
    pending: dict[uuid.UUID, Decimal] = {}
    lines_out = []
    ok_all = True
    for line in invoice.lines.select_related("order_line__order").order_by("line_no"):
        issues: list[str] = []
        detail: dict[str, Any] = {
            "line_no": line.line_no,
            "quantity": str(line.quantity),
            "unit_price": str(line.unit_price),
        }
        po_line = line.order_line
        if po_line is None:
            issues.append("no_purchase_order")
        else:
            order = po_line.order
            pending[po_line.id] = pending.get(po_line.id, Decimal(0)) + line.quantity
            invoiced = _invoiced_before(line) + pending[po_line.id]
            allowed_qty = po_line.received_qty * (Decimal(100) + qty_tol) / Decimal(100)
            price_gap = abs(line.unit_price - po_line.unit_price)
            allowed_gap = po_line.unit_price * price_tol / Decimal(100)
            detail.update(
                order=order.number,
                order_line=po_line.line_no,
                ordered=str(po_line.quantity),
                received=str(po_line.received_qty),
                invoiced_cumulative=str(invoiced),
                po_unit_price=str(po_line.unit_price),
            )
            if order.currency != invoice.currency:
                issues.append("currency")
            if invoiced > allowed_qty:
                issues.append("quantity_not_received" if po_line.received_qty == 0 else "quantity")
            if price_gap > allowed_gap:
                issues.append("price")
        detail["issues"] = issues
        ok_all = ok_all and not issues
        lines_out.append(detail)
    return {
        "status": V.MatchStatus.MATCHED if ok_all else V.MatchStatus.MISMATCH,
        "tolerances": {"quantity_pct": str(qty_tol), "price_pct": str(price_tol)},
        "lines": lines_out,
    }


def _mismatch_approved(invoice: VendorInvoice) -> bool:
    if invoice.mismatch_approval_id is None:
        return False
    return services.get(WorkflowService).state(invoice.mismatch_approval_id).state == "APPROVED"


def match_invoice(uow: UnitOfWork, invoice: VendorInvoice) -> VendorInvoice:
    """``:match``: store the result; a mismatch requests the breach approval (never blocks the match itself)."""
    require(uow.ctx, "purchasing.vendor_invoice.update", invoice)
    if invoice.status != V.Status.DRAFT:
        raise InvalidStateTransition(f"a {invoice.status} invoice cannot be matched")
    result = three_way(uow, invoice)
    invoice.match_status, invoice.match_result = result["status"], result
    fields = ["match_status", "match_result"]
    if result["status"] == V.MatchStatus.MISMATCH and invoice.mismatch_approval_id is None:
        issues = sorted({i for line in result["lines"] for i in line["issues"]})
        approval = services.get(WorkflowService).request_approval(
            uow,
            invoice.company_id,
            DocumentRef(DOC_TYPE, invoice.id, invoice.number),
            MISMATCH_POLICY,
            "three-way match: " + ", ".join(issues),
        )
        invoice.mismatch_approval_id = approval.id
        fields.append("mismatch_approval_id")
    invoice.save_versioned(uow.ctx.principal_id, fields)
    audit(
        uow, "purchasing.vendor_invoice.match", DOC_TYPE, invoice.id, invoice.company_id, {"status": result["status"]}
    )
    return invoice


# --------------------------------------------------------------------------- posting


@dataclass(frozen=True)
class InvoicePlan:
    request: PostingRequest
    taxes: TaxComputation
    withholdings: TaxComputation
    grni: dict[uuid.UUID, Decimal]
    variance: dict[uuid.UUID, Decimal]
    subtotal: Decimal
    tax_total: Decimal
    withholding_total: Decimal
    payable: Decimal
    payable_cop: Decimal


def _received_unit_cost(po_line: PurchaseOrderLine) -> Decimal:
    agg = ReceiptLine.objects.filter(order_line=po_line, receipt__status="VALIDATED").aggregate(
        q=Sum("quantity"), a=Sum("amount_cop")
    )
    if not agg["q"]:
        return round_half_up(po_line.unit_price * po_line.order.fx_rate, UNIT_COST)
    return round_half_up(agg["a"] / agg["q"], UNIT_COST)


def plan(uow: UnitOfWork, invoice: VendorInvoice) -> InvoicePlan:
    tax = services.get(TaxService)
    lines = list(invoice.lines.select_related("order_line__order").order_by("line_no"))
    party_id = invoice.supplier.party_id
    tax_inputs = [TaxLineInput(str(x.id), x.amount, (x.tax_code,)) for x in lines if x.tax_code]
    taxes = (
        tax.compute(invoice.company_id, DOC_TYPE, invoice.document_date, party_id, tax_inputs)
        if tax_inputs
        else TaxComputation(())
    )
    requests = (invoice.tax_snapshot or {}).get("withholding_requests", {})
    wht_inputs = [
        TaxLineInput(str(x.id), x.amount, tuple(requests[str(x.id)]["codes"]), requests[str(x.id)].get("concept"))
        for x in lines
        if str(x.id) in requests
    ]
    if wht_inputs and party_id is None:
        raise ValidationFailed("withholdings need the vendor's party (tax profile)", field="party")
    withholdings = (
        tax.withholdings(invoice.company_id, DOC_TYPE, invoice.document_date, party_id, wht_inputs)  # type: ignore[arg-type]
        if wht_inputs
        else TaxComputation(())
    )
    fx = invoice.fx_rate
    party = PartyRef(party_id or invoice.supplier_id, "vendor")
    posting: list[PostingLine] = []
    grni: dict[uuid.UUID, Decimal] = {}
    variance: dict[uuid.UUID, Decimal] = {}
    debit_cop = Decimal(0)
    for x in lines:
        dims = dimension_values(x.dimensions)
        net_cop = q_amount(x.amount * fx)
        memo = f"{invoice.number} L{x.line_no}"
        if x.order_line is not None:
            cleared = q_amount(x.quantity * _received_unit_cost(x.order_line))
            grni[x.id], variance[x.id] = cleared, net_cop - cleared
            posting.append(PostingLine(ROLE_GRNI, "DEBIT", cleared, party=party, dimensions=dims, memo=memo))
            if net_cop != cleared:
                side: Literal["DEBIT", "CREDIT"] = "DEBIT" if net_cop > cleared else "CREDIT"
                posting.append(
                    PostingLine(
                        ROLE_PRICE_VARIANCE, side, abs(net_cop - cleared), item_id=x.item_id, dimensions=dims, memo=memo
                    )
                )
        else:
            posting.append(PostingLine(x.account_role or ROLE_EXPENSE, "DEBIT", net_cop, dimensions=dims, memo=memo))
        debit_cop += net_cop
    tax_total = sum((t.amount for t in taxes.lines), Decimal(0))
    for t in taxes.lines:
        amount = q_amount(t.amount * fx)
        posting.append(PostingLine(ROLE_VAT_RECOVERABLE, "DEBIT", amount, tax_code=t.tax_code, memo=invoice.number))
        debit_cop += amount
    wht_total = sum((t.amount for t in withholdings.lines), Decimal(0))
    wht_cop = Decimal(0)
    for t in withholdings.lines:
        amount = q_amount(t.amount * fx)
        posting.append(
            PostingLine(ROLE_WHT_PAYABLE, "CREDIT", amount, tax_code=t.tax_code, party=party, memo=invoice.number)
        )
        wht_cop += amount
    subtotal = sum((x.amount for x in lines), Decimal(0))
    payable = subtotal + tax_total - wht_total
    payable_cop = debit_cop - wht_cop
    if payable_cop <= 0:
        raise ValidationFailed("the invoice payable must be positive")
    posting.append(PostingLine(ROLE_AP, "CREDIT", payable_cop, party=party, memo=invoice.number))
    request = PostingRequest(
        company_id=invoice.company_id,
        posting_date=invoice.document_date,
        source=DocumentRef(DOC_TYPE, invoice.id, invoice.number),
        purpose="vendor_invoice",
        lines=tuple(posting),
        journal_code="PURCHASES",
    )
    return InvoicePlan(
        request, taxes, withholdings, grni, variance, subtotal, tax_total, wht_total, payable, payable_cop
    )


def simulate_invoice(uow: UnitOfWork, invoice: VendorInvoice) -> dict[str, Any]:
    require(uow.ctx, "purchasing.vendor_invoice.read", invoice)
    p = plan(uow, invoice)
    result = services.get(PostingService).simulate(uow, p.request)
    return {
        "match": three_way(uow, invoice),
        "journal_entries": [
            {
                "lines": [
                    {
                        "account_role": x.account_role,
                        "side": x.side,
                        "amount": str(x.amount),
                        "tax_code": x.tax_code,
                        "memo": x.memo,
                    }
                    for x in p.request.lines
                ],
                "total_debit_cop": str(result.total_debit_cop),
                "total_credit_cop": str(result.total_credit_cop),
            }
        ],
        "totals": {
            "subtotal": str(p.subtotal),
            "taxes": str(p.tax_total),
            "withholdings": str(p.withholding_total),
            "payable": str(p.payable),
            "payable_cop": str(p.payable_cop),
            "currency": invoice.currency,
        },
        "warnings": list(p.taxes.warnings) + list(p.withholdings.warnings),
    }


def post_invoice(uow: UnitOfWork, invoice: VendorInvoice) -> VendorInvoice:
    ctx = uow.ctx
    require(ctx, "purchasing.vendor_invoice.post", invoice)
    invoice = lock(V.objects.filter(tenant_id=ctx.tenant_id), invoice.id)
    if invoice.status != V.Status.DRAFT:
        raise InvalidStateTransition(f"a {invoice.status} invoice cannot be posted")
    po_line_ids = sorted({x.order_line_id for x in invoice.lines.all() if x.order_line_id})
    list(PurchaseOrderLine.objects.select_for_update().filter(id__in=po_line_ids).order_by("id"))
    result = three_way(uow, invoice)
    if result["status"] != V.MatchStatus.MATCHED and not _mismatch_approved(invoice):
        raise ThreeWayMismatch(
            "the invoice does not match the order and receipts; an approved breach is required",
            lines=[x for x in result["lines"] if x["issues"]],
        )
    p = plan(uow, invoice)
    posted = services.get(PostingService).post(uow, p.request)
    for x in invoice.lines.all():
        x.grni_amount_cop = p.grni.get(x.id, Decimal(0))
        x.variance_cop = p.variance.get(x.id, Decimal(0))
        x.posted = True
        x.save(update_fields=["grni_amount_cop", "variance_cop", "posted"])
        if x.order_line_id:
            PurchaseOrderLine.objects.filter(id=x.order_line_id).update(invoiced_qty=F("invoiced_qty") + x.quantity)
    invoice.status, invoice.match_status, invoice.match_result = V.Status.POSTED, result["status"], result
    invoice.tax_total, invoice.withholding_total, invoice.total = p.tax_total, p.withholding_total, p.payable
    invoice.journal_entry_id, invoice.posted_at, invoice.posted_by, invoice.posted = (
        posted.entry_id,
        clock.now(),
        ctx.principal_id,
        True,
    )
    invoice.tax_snapshot = {
        **(invoice.tax_snapshot or {}),
        "parameters": [*p.taxes.parameter_snapshots, *p.withholdings.parameter_snapshots],
        "warnings": [*p.taxes.warnings, *p.withholdings.warnings],
    }
    invoice.save(
        update_fields=[
            "status",
            "match_status",
            "match_result",
            "tax_total",
            "withholding_total",
            "total",
            "journal_entry_id",
            "posted_at",
            "posted_by",
            "posted",
            "tax_snapshot",
        ]
    )
    ApOpenItem.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=invoice.company_id,
        supplier=invoice.supplier,
        document_type=DOC_TYPE,
        document_id=invoice.id,
        document_number=invoice.number,
        document_date=invoice.document_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        fx_rate=invoice.fx_rate,
        amount=p.payable,
        balance=p.payable,
        amount_cop=p.payable_cop,
        balance_cop=p.payable_cop,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    for order in PurchaseOrder.objects.filter(lines__id__in=po_line_ids).distinct().order_by("id"):
        order_svc.refresh_progress(uow, order)
    uow.emit(
        "purchasing.vendor_invoice.posted.v1",
        {
            "vendor_invoice_id": str(invoice.id),
            "party": {"id": str(invoice.supplier.party_id or invoice.supplier_id), "kind": "vendor"},
            "total": {"amount": f"{q_amount(p.payable):f}", "currency": invoice.currency},
            "journal_entry_id": str(posted.entry_id),
        },
        company_id=invoice.company_id,
    )
    audit(
        uow,
        "purchasing.vendor_invoice.post",
        DOC_TYPE,
        invoice.id,
        invoice.company_id,
        {"journal_entry": str(posted.entry_id), "match": result["status"]},
    )
    return invoice
