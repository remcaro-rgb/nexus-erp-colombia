"""JSON views of the purchasing documents (contract schemas in contracts/openapi/modules/purchasing.yaml).

Decimals travel as strings; confidential fields (``x-classification: confidential``: ``party``,
``landed_total``, AP ``balance``) are emitted only when the principal's field policy can read them
(``PolicyService.field_mask``; P04 field registry, fail closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from iam.policy import fields as field_registry
from kernel import services
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from purchasing.models import (
    ApOpenItem,
    Award,
    PurchaseOrder,
    QuoteComparison,
    Receipt,
    Rfq,
    Supplier,
    VendorInvoice,
    VendorQuote,
)

CONFIDENTIAL = {
    "purchasing.rfq": frozenset(),
    "purchasing.quote": frozenset({"party", "landed_total"}),
    "purchasing.order": frozenset({"party"}),
    "purchasing.receipt": frozenset({"party"}),
    "purchasing.vendor_invoice": frozenset({"party"}),
    "purchasing.ap_open_item": frozenset({"party", "balance"}),
}
_COMMON = {
    "id": "public",
    "tenant_id": "internal",
    "company_id": "internal",
    "number": "internal",
    "document_date": "internal",
    "posting_date": "internal",
    "status": "internal",
    "currency": "public",
    "total": "internal",
    "notes": "internal",
    "source": "internal",
    "lines": "internal",
    "version": "internal",
    "audit": "internal",
}


def register_fields() -> None:
    """Field classifications for P04's field policy (mirrors the contract ``x-classification``)."""
    extra = {
        "purchasing.rfq": {
            "deadline": "internal",
            "invited_vendors": "internal",
            "scoring_weights": "internal",
            "awarded_quote_id": "internal",
            "award_reason": "internal",
            "warnings": "internal",
            "reopen_count": "internal",
            "reopen_reason": "internal",
            "award": "internal",
            "terms": "internal",
        },
        "purchasing.quote": {
            "party": "confidential",
            "received_at": "internal",
            "lead_time_days": "internal",
            "payment_terms_days": "internal",
            "quote_status": "internal",
            "landed_total": "confidential",
            "rfq_id": "internal",
            "fx_rate": "internal",
            "channel": "internal",
            "valid_until": "internal",
            "freight_amount": "internal",
            "warnings": "internal",
        },
        "purchasing.order": {
            "party": "confidential",
            "expected_date": "internal",
            "rfq": "internal",
            "fx_rate": "internal",
        },
        "purchasing.receipt": {
            "party": "confidential",
            "order": "internal",
            "warehouse_id": "internal",
            "partial": "internal",
            "journal_entry_id": "internal",
        },
        "purchasing.vendor_invoice": {
            "party": "confidential",
            "vendor_invoice_number": "internal",
            "due_date": "internal",
            "received_einvoice": "internal",
            "order": "internal",
            "match_status": "internal",
            "match_result": "internal",
            "subtotal": "internal",
            "tax_total": "internal",
            "withholding_total": "internal",
            "fx_rate": "internal",
            "journal_entry_id": "internal",
        },
        "purchasing.ap_open_item": {
            "party": "confidential",
            "document": "internal",
            "due_date": "internal",
            "balance": "confidential",
            "amount": "internal",
            "balance_cop": "confidential",
        },
    }
    for resource, fields in extra.items():
        field_registry.register(resource, {**_COMMON, **fields})


def masked(ctx: TenantContext, resource_type: str, data: dict[str, Any]) -> dict[str, Any]:
    hidden = CONFIDENTIAL.get(resource_type, frozenset())
    if not hidden:
        return data
    readable = services.get(PolicyService).field_mask(ctx, resource_type).readable
    for name in hidden:
        if name not in readable:
            data.pop(name, None)
    if "balance" in hidden and "balance" not in readable:
        data.pop("balance_cop", None)
    return data


def d(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:f}"


def money(amount: Decimal, currency: str) -> dict[str, str]:
    return {"amount": f"{amount:f}", "currency": currency}


def audit_stamp(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat() if getattr(obj, "created_at", None) else None,
        "created_by": str(obj.created_by) if getattr(obj, "created_by", None) else None,
        "updated_at": obj.updated_at.isoformat() if getattr(obj, "updated_at", None) else None,
        "updated_by": str(obj.updated_by) if getattr(obj, "updated_by", None) else None,
    }


def party(supplier: Supplier) -> dict[str, Any]:
    return {"id": str(supplier.id), "kind": "vendor", "name": supplier.name, "tax_id": supplier.nit}


def supplier_json(s: Supplier) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "code": s.code,
        "name": s.name,
        "nit": s.nit,
        "party_id": str(s.party_id) if s.party_id else None,
        "rating": d(s.rating),
        "payment_terms_days": s.payment_terms_days,
        "currency": s.currency,
        "active": s.active,
    }


def rfq_json(r: Rfq, warnings: list[str] | None = None) -> dict[str, Any]:
    award = r.awards.order_by("-created_at").first()
    out = {
        "id": str(r.id),
        "tenant_id": str(r.tenant_id),
        "company_id": str(r.company_id),
        "number": r.number,
        "document_date": r.document_date.isoformat(),
        "posting_date": None,
        "status": r.status,
        "currency": r.currency,
        "notes": r.notes,
        "terms": r.terms,
        "source": r.source,
        "deadline": r.deadline.isoformat(),
        "invited_vendors": list(r.invited_vendors),
        "scoring_weights": dict(r.scoring_weights),
        "awarded_quote_id": str(r.awarded_quote_id) if r.awarded_quote_id else None,
        "award_reason": r.award_reason or None,
        "reopen_count": r.reopen_count,
        "reopen_reason": r.reopen_reason or None,
        "award": award_json(award) if award else None,
        "lines": [
            {
                "id": str(x.id),
                "line_no": x.line_no,
                "item_id": str(x.item_id) if x.item_id else None,
                "description": x.description,
                "quantity": {"value": d(x.quantity), "uom": x.uom},
                "tax_code": x.tax_code or None,
                "dimensions": x.dimensions,
            }
            for x in r.lines.order_by("line_no")
        ],
        "version": r.version,
        "audit": audit_stamp(r),
    }
    if warnings:
        out["warnings"] = warnings
    return out


def award_json(a: Award) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "status": a.status,
        "comparison_id": str(a.comparison_id),
        "recommended_quote_id": str(a.recommended_quote_id) if a.recommended_quote_id else None,
        "differs_from_recommendation": a.differs_from_recommendation,
        "reason": a.reason or None,
        "proposed_by": str(a.proposed_by),
        "approval_request_id": str(a.approval_request_id) if a.approval_request_id else None,
        "decided_by": str(a.decided_by) if a.decided_by else None,
        "lines": [{"rfq_line_id": str(x.rfq_line_id), "quote_id": str(x.quote_id)} for x in a.lines.order_by("id")],
    }


def quote_json(ctx: TenantContext, q: VendorQuote, warnings: list[str] | None = None) -> dict[str, Any]:
    out = {
        "id": str(q.id),
        "tenant_id": str(q.tenant_id),
        "company_id": str(q.company_id),
        "number": q.number,
        "rfq_id": str(q.rfq_id),
        "document_date": q.document_date.isoformat(),
        "posting_date": None,
        "status": "SUBMITTED",
        "party": party(q.supplier),
        "currency": q.currency,
        "fx_rate": d(q.fx_rate),
        "total": money(q.total, q.currency),
        "notes": q.notes,
        "channel": q.channel,
        "received_at": q.received_at.isoformat(),
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "lead_time_days": q.lead_time_days,
        "payment_terms_days": q.payment_terms_days,
        "freight_amount": d(q.freight_amount),
        "quote_status": q.quote_status,
        "landed_total": money(q.landed_total_cop, "COP"),
        "lines": [
            {
                "id": str(x.id),
                "line_no": x.line_no,
                "rfq_line_id": str(x.rfq_line_id),
                "item_id": str(x.item_id) if x.item_id else None,
                "description": x.description,
                "quantity": {"value": d(x.quantity), "uom": x.uom},
                "factor_to_base": d(x.factor_to_base),
                "unit_price": money(x.unit_price, q.currency),
                "discount_pct": d(x.discount_pct),
                "tax_code": x.tax_code or None,
                "tax_rate_pct": d(x.tax_rate_pct),
                "tax_recoverable": x.tax_recoverable,
                "freight_amount": d(x.freight_amount),
                "lead_time_days": x.lead_time_days,
            }
            for x in q.lines.order_by("line_no")
        ],
        "version": q.version,
        "audit": audit_stamp(q),
    }
    if warnings:
        out["warnings"] = warnings
    return masked(ctx, "purchasing.quote", out)


def comparison_json(c: QuoteComparison) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "rfq_id": str(c.rfq_id),
        "sequence": c.sequence,
        "created_at": c.created_at.isoformat(),
        "created_by": str(c.created_by),
        "weights": c.weights,
        "recommended_quote_id": str(c.recommended_quote_id) if c.recommended_quote_id else None,
        "explanation": c.explanation,
        **{k: v for k, v in c.snapshot.items() if k not in ("weights", "explanation")},
    }


def order_json(ctx: TenantContext, o: PurchaseOrder) -> dict[str, Any]:
    out = {
        "id": str(o.id),
        "tenant_id": str(o.tenant_id),
        "company_id": str(o.company_id),
        "number": o.number,
        "document_date": o.document_date.isoformat(),
        "posting_date": None,
        "status": o.status,
        "party": party(o.supplier),
        "currency": o.currency,
        "fx_rate": d(o.fx_rate),
        "total": money(o.total, o.currency),
        "notes": o.notes,
        "source": o.source,
        "expected_date": o.expected_date.isoformat() if o.expected_date else None,
        "rfq": {"type": "purchasing.rfq", "id": str(o.rfq_id), "number": o.rfq.number} if o.rfq_id and o.rfq else None,
        "lines": [
            {
                "id": str(x.id),
                "line_no": x.line_no,
                "item_id": str(x.item_id) if x.item_id else None,
                "description": x.description,
                "quantity": {"value": d(x.quantity), "uom": x.uom},
                "unit_price": money(x.unit_price, o.currency),
                "amount": money(x.amount, o.currency),
                "tax_code": x.tax_code or None,
                "dimensions": x.dimensions,
                "received_qty": d(x.received_qty),
                "invoiced_qty": d(x.invoiced_qty),
            }
            for x in o.lines.order_by("line_no")
        ],
        "version": o.version,
        "audit": audit_stamp(o),
    }
    return masked(ctx, "purchasing.order", out)


def receipt_json(ctx: TenantContext, r: Receipt) -> dict[str, Any]:
    o = r.order
    out = {
        "id": str(r.id),
        "tenant_id": str(r.tenant_id),
        "company_id": str(r.company_id),
        "number": r.number,
        "document_date": r.document_date.isoformat(),
        "posting_date": r.document_date.isoformat() if r.posted else None,
        "status": "POSTED" if r.status == Receipt.Status.VALIDATED else r.status,
        "receipt_status": r.status,
        "party": party(o.supplier),
        "currency": "COP",
        "total": money(r.total_cop, "COP"),
        "notes": r.notes,
        "order": {"type": "purchasing.order", "id": str(o.id), "number": o.number},
        "warehouse_id": str(r.warehouse_id),
        "journal_entry_id": str(r.journal_entry_id) if r.journal_entry_id else None,
        "lines": [
            {
                "id": str(x.id),
                "line_no": x.line_no,
                "order_line_id": str(x.order_line_id),
                "item_id": str(x.item_id) if x.item_id else None,
                "quantity": {"value": d(x.quantity), "uom": x.order_line.uom},
                "unit_price": money(x.unit_cost_cop, "COP"),
                "amount": money(x.amount_cop, "COP"),
                "location_id": str(x.location_id) if x.location_id else None,
                "lot_id": str(x.lot_id) if x.lot_id else None,
                "presentation_id": str(x.presentation_id) if x.presentation_id else None,
                "dimensions": x.dimensions,
            }
            for x in r.lines.select_related("order_line").order_by("line_no")
        ],
        "version": r.version,
        "audit": audit_stamp(r),
    }
    return masked(ctx, "purchasing.receipt", out)


def invoice_json(ctx: TenantContext, v: VendorInvoice) -> dict[str, Any]:
    out = {
        "id": str(v.id),
        "tenant_id": str(v.tenant_id),
        "company_id": str(v.company_id),
        "number": v.number,
        "document_date": v.document_date.isoformat(),
        "posting_date": v.document_date.isoformat() if v.posted else None,
        "status": v.status,
        "party": party(v.supplier),
        "currency": v.currency,
        "fx_rate": d(v.fx_rate),
        "vendor_invoice_number": v.vendor_invoice_number,
        "due_date": v.due_date.isoformat(),
        "order": {"type": "purchasing.order", "id": str(v.order_id), "number": v.order.number}
        if v.order_id and v.order
        else None,
        "subtotal": money(v.subtotal, v.currency),
        "tax_total": money(v.tax_total, v.currency),
        "withholding_total": money(v.withholding_total, v.currency),
        "total": money(v.total, v.currency),
        "match_status": v.match_status,
        "match_result": v.match_result or None,
        "notes": v.notes,
        "journal_entry_id": str(v.journal_entry_id) if v.journal_entry_id else None,
        "lines": [
            {
                "id": str(x.id),
                "line_no": x.line_no,
                "order_line_id": str(x.order_line_id) if x.order_line_id else None,
                "item_id": str(x.item_id) if x.item_id else None,
                "description": x.description,
                "quantity": {"value": d(x.quantity), "uom": x.uom},
                "unit_price": money(x.unit_price, v.currency),
                "amount": money(x.amount, v.currency),
                "tax_code": x.tax_code or None,
                "dimensions": x.dimensions,
            }
            for x in v.lines.order_by("line_no")
        ],
        "version": v.version,
        "audit": audit_stamp(v),
    }
    return masked(ctx, "purchasing.vendor_invoice", out)


def open_item_json(ctx: TenantContext, i: ApOpenItem) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(i.id),
        "tenant_id": str(i.tenant_id),
        "company_id": str(i.company_id),
        "status": i.status,
        "party": party(i.supplier),
        "document": {"type": i.document_type, "id": str(i.document_id), "number": i.document_number},
        "document_date": i.document_date.isoformat(),
        "due_date": i.due_date.isoformat(),
        "amount": money(i.amount, i.currency),
        "balance": money(i.balance, i.currency),
        "balance_cop": money(i.balance_cop, "COP"),
        "version": i.version,
        "audit": audit_stamp(i),
    }
    return masked(ctx, "purchasing.ap_open_item", out)


def warnings_of(data: Mapping[str, Any]) -> list[str]:
    value = data.get("warnings")
    return list(value) if isinstance(value, list) else []
