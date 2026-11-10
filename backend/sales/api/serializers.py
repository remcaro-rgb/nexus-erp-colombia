"""JSON views of the sales documents (contract schemas in contracts/openapi/modules/sales.yaml).

Decimals travel as strings. Field security (P04 field policy, fail closed): ``confidential`` and
``restricted-pii`` fields are emitted only when ``PolicyService.field_mask`` lists them as readable for the
principal — party data, credit limits, AR balances, line costs/margins and the confidential parts of the price
snapshot (``cost`` input, formula trace). The same projection is used by UI, API, exports, AI and MCP.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from iam.policy import fields as field_registry
from kernel import services
from kernel.errors import DomainError
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.fiscal import FiscalDocuments
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from sales.models import (
    ArOpenItem,
    Delivery,
    Invoice,
    ItemSalesPrice,
    Party,
    PriceFormula,
    PriceList,
    Quote,
    Receipt,
    SalesDocument,
    SalesNote,
    SalesOrder,
    SalesReturn,
    WriteOff,
)
from sales.pricing import engine

_COMMON = {
    "id": "public",
    "tenant_id": "internal",
    "company_id": "internal",
    "number": "internal",
    "document_date": "internal",
    "posting_date": "internal",
    "status": "internal",
    "document_status": "internal",
    "currency": "public",
    "total": "internal",
    "subtotal": "internal",
    "tax_total": "internal",
    "notes": "internal",
    "source": "internal",
    "lines": "internal",
    "version": "internal",
    "audit": "internal",
    "fx_rate": "internal",
    "salesperson_id": "internal",
    "channel": "internal",
    "approval_request_id": "internal",
}
DOC_EXTRA = {
    "party": "confidential",
    "valid_until": "internal",
    "due_date": "internal",
    "fiscal_status": "internal",
    "cufe": "internal",
    "branch_id": "internal",
    "journal_entry_id": "internal",
    "deliver_on_invoice": "internal",
    "warehouse_id": "internal",
    "order_id": "internal",
    "cost_total": "confidential",
    "margin": "confidential",
}
FIELDS: dict[str, dict[str, str]] = {
    "sales.party": {
        **_COMMON,
        "code": "internal",
        "legal_name": "confidential",
        "trade_name": "confidential",
        "nit": "confidential",
        "dv": "confidential",
        "person_type": "internal",
        "roles": "internal",
        "email": "restricted-pii",
        "einvoice_email": "restricted-pii",
        "phone": "restricted-pii",
        "address": "restricted-pii",
        "municipality_code": "internal",
        "tax_profile_id": "internal",
        "credit_limit": "confidential",
        "payment_terms_days": "internal",
        "price_list_id": "internal",
        "customer_group": "internal",
        "territory": "internal",
        "consent_ref": "internal",
        "merged_into": "internal",
    },
    "sales.price_list": {
        **_COMMON,
        "code": "internal",
        "name": "internal",
        "customer_group": "internal",
        "valid_from": "internal",
        "valid_to": "internal",
        "prices": "confidential",
    },
    "sales.price_formula": {
        **_COMMON,
        "code": "internal",
        "name": "internal",
        "formula_id": "internal",
        "formula_code": "internal",
        "scope": "internal",
        "priority": "internal",
        "valid_from": "internal",
        "valid_to": "internal",
    },
    "sales.item_price": {
        **_COMMON,
        "item_id": "internal",
        "base_price": "internal",
        "valid_from": "internal",
        "valid_to": "internal",
        "category": "internal",
        "tax_code": "internal",
    },
    "sales.quote": {**_COMMON, **DOC_EXTRA},
    "sales.order": {**_COMMON, **DOC_EXTRA},
    "sales.invoice": {**_COMMON, **DOC_EXTRA},
    "sales.delivery": {
        **_COMMON,
        "party": "confidential",
        "order_id": "internal",
        "warehouse_id": "internal",
        "cost_total": "confidential",
        "journal_entry_id": "internal",
    },
    "sales.credit_note": {
        **_COMMON,
        "party": "confidential",
        "original": "internal",
        "reason": "internal",
        "reason_text": "internal",
        "with_return": "internal",
        "warehouse_id": "internal",
        "journal_entry_id": "internal",
        "cost_total": "confidential",
    },
    "sales.debit_note": {
        **_COMMON,
        "party": "confidential",
        "original": "internal",
        "reason": "internal",
        "reason_text": "internal",
        "with_return": "internal",
        "warehouse_id": "internal",
        "journal_entry_id": "internal",
        "cost_total": "confidential",
    },
    "sales.receipt": {
        **_COMMON,
        "party": "confidential",
        "amount": "confidential",
        "applications": "internal",
        "withholdings": "internal",
        "journal_entry_id": "internal",
        "bank_role": "internal",
    },
    "sales.ar_open_item": {
        **_COMMON,
        "party": "confidential",
        "document": "internal",
        "due_date": "internal",
        "balance": "confidential",
        "amount": "confidential",
        "balance_cop": "confidential",
        "amount_cop": "confidential",
    },
    "sales.write_off": {
        **_COMMON,
        "party": "confidential",
        "open_item_id": "internal",
        "amount": "confidential",
        "reason": "internal",
        "journal_entry_id": "internal",
    },
    "sales.return": {
        **_COMMON,
        "original": "internal",
        "warehouse_id": "internal",
        "reason": "internal",
        "credit_note_id": "internal",
    },
    #: Line-level / snapshot fields (``cost``/``trace``/``margin`` confidential: cost roles only).
    engine.SNAPSHOT_RESOURCE: {
        "source": "internal",
        "unit_price": "internal",
        "detail": "internal",
        "cost": "confidential",
        "trace": "confidential",
    },
    "sales.line": {"cost_value": "confidential", "margin": "confidential", "components": "confidential"},
}


def register_fields() -> None:
    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)


def _readable(ctx: TenantContext, resource: str) -> frozenset[str]:
    return frozenset(services.get(PolicyService).field_mask(ctx, resource).readable)


def masked(ctx: TenantContext, resource: str, data: dict[str, Any]) -> dict[str, Any]:
    """Drop ``confidential`` / PII fields the principal's field policy cannot read (fail closed)."""
    hidden = {
        k
        for k, v in FIELDS.get(resource, {}).items()
        if v in ("confidential", "restricted-pii", "sensitive-pii", "secret")
    }
    if not hidden:
        return data
    readable = _readable(ctx, resource)
    for name in hidden - readable:
        data.pop(name, None)
    return data


def d(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:f}"


def money(amount: Decimal | None, currency: str) -> dict[str, str] | None:
    return None if amount is None else {"amount": f"{amount:f}", "currency": currency}


def audit_stamp(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat() if getattr(obj, "created_at", None) else None,
        "created_by": str(obj.created_by) if getattr(obj, "created_by", None) else None,
        "updated_at": obj.updated_at.isoformat() if getattr(obj, "updated_at", None) else None,
        "updated_by": str(obj.updated_by) if getattr(obj, "updated_by", None) else None,
    }


def party_ref(p: Party) -> dict[str, Any]:
    return {"id": str(p.id), "kind": "customer", "name": p.legal_name, "tax_id": f"{p.nit}-{p.dv}" if p.dv else p.nit}


# --------------------------------------------------------------------------- masters


def party_json(ctx: TenantContext, p: Party) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.party",
        {
            "id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "company_id": str(p.company_id),
            "code": p.code,
            "status": "ACTIVE" if p.status == Party.Status.ACTIVE else "ARCHIVED",
            "document_status": p.status,
            "legal_name": p.legal_name,
            "trade_name": p.trade_name,
            "nit": {"number": p.nit, "dv": p.dv} if p.dv else {"number": p.nit},
            "person_type": p.person_type,
            "roles": list(p.roles or []),
            "email": p.email,
            "einvoice_email": p.einvoice_email,
            "phone": p.phone,
            "address": p.address,
            "municipality_code": p.municipality_code,
            "tax_profile_id": str(p.tax_profile_id) if p.tax_profile_id else None,
            "credit_limit": money(p.credit_limit, "COP"),
            "payment_terms_days": p.payment_terms_days,
            "price_list_id": str(p.price_list_id) if p.price_list_id else None,
            "customer_group": p.customer_group,
            "salesperson_id": str(p.salesperson_id) if p.salesperson_id else None,
            "territory": p.territory,
            "consent_ref": str(p.consent_ref) if p.consent_ref else None,
            "merged_into": str(p.merged_into) if p.merged_into else None,
            "version": p.version,
            "audit": audit_stamp(p),
        },
    )


def price_list_json(ctx: TenantContext, p: PriceList) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.price_list",
        {
            "id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "company_id": str(p.company_id),
            "code": p.code,
            "name": p.name,
            "status": "ACTIVE" if p.status == PriceList.Status.ACTIVE else "ARCHIVED",
            "currency": p.currency,
            "customer_group": p.customer_group,
            "valid_from": p.valid_from.isoformat(),
            "valid_to": p.valid_to.isoformat() if p.valid_to else None,
            "prices": [
                {"item_id": str(x.item_id), "min_qty": d(x.min_qty), "unit_price": money(x.unit_price, p.currency)}
                for x in p.lines.order_by("item_id", "min_qty")
            ],
            "version": p.version,
            "audit": audit_stamp(p),
        },
    )


def price_formula_json(ctx: TenantContext, p: PriceFormula) -> dict[str, Any]:
    """Binding only: the formula text lives in workflow (``workflow.formula.read``), never copied here."""
    return masked(
        ctx,
        "sales.price_formula",
        {
            "id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "company_id": str(p.company_id),
            "code": p.code,
            "name": p.name or p.code,
            "status": "ACTIVE" if p.status == PriceFormula.Status.ACTIVE else "ARCHIVED",
            "formula_id": str(p.formula_id),
            "formula_code": p.formula_code,
            "scope": dict(p.scope or {}),
            "priority": p.priority,
            "valid_from": p.valid_from.isoformat(),
            "valid_to": p.valid_to.isoformat() if p.valid_to else None,
            "version": p.version,
            "audit": audit_stamp(p),
        },
    )


def item_price_json(p: ItemSalesPrice) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "item_id": str(p.item_id),
        "currency": p.currency,
        "base_price": money(p.base_price, p.currency),
        "valid_from": p.valid_from.isoformat(),
        "valid_to": p.valid_to.isoformat() if p.valid_to else None,
        "category": p.category,
        "tax_code": p.tax_code,
    }


# --------------------------------------------------------------------------- documents

CONTRACT_STATUS = {
    "DRAFT": "DRAFT",
    "SENT": "SUBMITTED",
    "PENDING_APPROVAL": "SUBMITTED",
    "SUBMITTED": "SUBMITTED",
    "ACCEPTED": "APPROVED",
    "APPROVED": "APPROVED",
    "CONFIRMED": "APPROVED",
    "PARTIALLY_DELIVERED": "APPROVED",
    "DELIVERED": "APPROVED",
    "ORDERED": "POSTED",
    "INVOICED": "POSTED",
    "DONE": "POSTED",
    "POSTED": "POSTED",
    "CANCELLED": "CANCELLED",
}


def line_json(ctx: TenantContext, doc: Any, x: Any, cost_ok: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(x.id),
        "line_no": x.line_no,
        "item_id": str(x.item_id) if x.item_id else None,
        "description": x.description,
        "quantity": {"value": d(x.quantity), "uom": x.uom},
        "unit_price": money(x.unit_price, doc.currency),
        "discount_pct": d(x.discount_pct),
        "amount": money(x.amount, doc.currency),
        "tax_code": x.tax_code or None,
        "account_code": None,
        "warehouse_id": str(x.warehouse_id) if x.warehouse_id else None,
        "dimensions": dict(x.dimensions or {}),
        "price_source": x.price_source,
        "override_status": x.override_status,
        "price_snapshot": engine.visible_snapshot(ctx, x.price_snapshot),
    }
    for attr in ("delivered_qty", "invoiced_qty"):
        if hasattr(x, attr):
            out[attr] = d(getattr(x, attr))
    if hasattr(x, "cost_value") and cost_ok:
        out["cost_value"] = money(x.cost_value, "COP")
        out["components"] = list(x.components or [])
        out["margin"] = money(x.amount * doc.fx_rate - x.cost_value, "COP")
    return out


def document_json(
    ctx: TenantContext, resource: str, doc: SalesDocument, diff: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    cost_ok = "cost_value" in _readable(ctx, "sales.line")
    status = str(getattr(doc, "status", "DRAFT"))
    out: dict[str, Any] = {
        "id": str(doc.id),
        "tenant_id": str(doc.tenant_id),
        "company_id": str(doc.company_id),
        "number": doc.number,
        "document_date": doc.document_date.isoformat(),
        "posting_date": doc.document_date.isoformat() if status == "POSTED" else None,
        "status": CONTRACT_STATUS.get(status, status),
        "document_status": status,
        "party": party_ref(doc.party),
        "currency": doc.currency,
        "fx_rate": d(doc.fx_rate),
        "subtotal": money(doc.subtotal, doc.currency),
        "tax_total": money(doc.tax_total, doc.currency),
        "total": money(doc.total, doc.currency),
        "notes": doc.notes,
        "source": doc.source,
        "salesperson_id": str(doc.salesperson_id) if doc.salesperson_id else None,
        "channel": doc.channel,
        "approval_request_id": str(doc.approval_request_id) if doc.approval_request_id else None,
        "lines": [line_json(ctx, doc, x, cost_ok) for x in doc.lines.order_by("line_no")],  # type: ignore[attr-defined]
        "version": doc.version,
        "audit": audit_stamp(doc),
    }
    if isinstance(doc, Quote):
        out["valid_until"] = doc.valid_until.isoformat() if doc.valid_until else None
        out["order_id"] = str(doc.order_id) if doc.order_id else None
    if isinstance(doc, Invoice):
        out.update(
            {
                "due_date": doc.due_date.isoformat() if doc.due_date else None,
                "branch_id": str(doc.branch_id) if doc.branch_id else None,
                "journal_entry_id": str(doc.journal_entry_id) if doc.journal_entry_id else None,
                "deliver_on_invoice": doc.deliver_on_invoice,
                "warehouse_id": str(doc.warehouse_id) if doc.warehouse_id else None,
                "order_id": str(doc.order_id) if doc.order_id else None,
                "cost_total": money(doc.cost_total, "COP"),
                **fiscal(doc),
            }
        )
    if isinstance(doc, SalesOrder):
        out["credit_approval_request_id"] = (
            str(doc.credit_approval_request_id) if doc.credit_approval_request_id else None
        )
    if diff is not None:
        out["reprice_diff"] = diff
    return masked(ctx, resource, out)


def fiscal(inv: Invoice) -> dict[str, Any]:
    """Fiscal status lifecycle shown on the document (read from P15's ``FiscalDocuments``)."""
    if inv.status != Invoice.Status.POSTED:
        return {"fiscal_status": "DRAFT", "cufe": None}
    reader = services.optional(FiscalDocuments)
    if reader is None:
        return {"fiscal_status": "QUEUED", "cufe": None}
    try:
        info = reader.status(inv.company_id, DocumentRef("sales.invoice", inv.id, inv.number))
    except DomainError:  # not registered yet: the e-invoice registration follows asynchronously (outbox)
        return {"fiscal_status": "QUEUED", "cufe": None}
    return {"fiscal_status": info.status, "cufe": info.unique_code, "fiscal_simulated": info.simulated}


def delivery_json(ctx: TenantContext, x: Delivery) -> dict[str, Any]:
    cost_ok = "cost_value" in _readable(ctx, "sales.line")
    lines = []
    for line in x.lines.order_by("line_no"):
        row: dict[str, Any] = {
            "id": str(line.id),
            "line_no": line.line_no,
            "item_id": str(line.item_id),
            "quantity": d(line.quantity),
            "lot_id": str(line.lot_id) if line.lot_id else None,
            "order_line_id": str(line.order_line_id) if line.order_line_id else None,
        }
        if cost_ok:
            row["cost_value"] = money(line.cost_value, "COP")
            row["components"] = list(line.components or [])
        lines.append(row)
    return masked(
        ctx,
        "sales.delivery",
        {
            "id": str(x.id),
            "tenant_id": str(x.tenant_id),
            "company_id": str(x.company_id),
            "number": x.number,
            "document_date": x.document_date.isoformat(),
            "status": CONTRACT_STATUS.get(x.status, x.status),
            "document_status": x.status,
            "party": party_ref(x.party),
            "order_id": str(x.order_id) if x.order_id else None,
            "warehouse_id": str(x.warehouse_id),
            "cost_total": money(x.cost_total, "COP"),
            "journal_entry_id": str(x.journal_entry_id) if x.journal_entry_id else None,
            "notes": x.notes,
            "lines": lines,
            "version": x.version,
            "audit": audit_stamp(x),
        },
    )


def note_json(ctx: TenantContext, n: SalesNote) -> dict[str, Any]:
    resource = "sales.credit_note" if n.kind == SalesNote.Kind.CREDIT else "sales.debit_note"
    cost_ok = "cost_value" in _readable(ctx, "sales.line")
    lines = []
    for x in n.lines.order_by("line_no"):
        row: dict[str, Any] = {
            "id": str(x.id),
            "line_no": x.line_no,
            "invoice_line_id": str(x.invoice_line_id) if x.invoice_line_id else None,
            "item_id": str(x.item_id) if x.item_id else None,
            "description": x.description,
            "quantity": d(x.quantity),
            "amount": money(x.amount, n.currency),
            "tax_code": x.tax_code or None,
        }
        if cost_ok:
            row["cost_value"] = money(x.cost_value, "COP")
        lines.append(row)
    return masked(
        ctx,
        resource,
        {
            "id": str(n.id),
            "tenant_id": str(n.tenant_id),
            "company_id": str(n.company_id),
            "kind": n.kind,
            "number": n.number,
            "document_date": n.document_date.isoformat(),
            "status": CONTRACT_STATUS.get(n.status, n.status),
            "document_status": n.status,
            "party": party_ref(n.party),
            "currency": n.currency,
            "original": {"type": "sales.invoice", "id": str(n.invoice_id), "number": n.invoice.number},
            "reason": n.reason,
            "reason_text": n.reason_text,
            "with_return": n.with_return,
            "warehouse_id": str(n.warehouse_id) if n.warehouse_id else None,
            "subtotal": money(n.subtotal, n.currency),
            "tax_total": money(n.tax_total, n.currency),
            "total": money(n.total, n.currency),
            "cost_total": money(n.cost_total, "COP"),
            "journal_entry_id": str(n.journal_entry_id) if n.journal_entry_id else None,
            "lines": lines,
            "version": n.version,
            "audit": audit_stamp(n),
        },
    )


def receipt_json(ctx: TenantContext, r: Receipt) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.receipt",
        {
            "id": str(r.id),
            "tenant_id": str(r.tenant_id),
            "company_id": str(r.company_id),
            "number": r.number,
            "document_date": r.document_date.isoformat(),
            "status": CONTRACT_STATUS.get(r.status, r.status),
            "document_status": r.status,
            "party": party_ref(r.party),
            "currency": r.currency,
            "fx_rate": d(r.fx_rate),
            "amount": money(r.amount, r.currency),
            "total": money(r.amount, r.currency),
            "bank_role": r.bank_role,
            "withholdings": list(r.withholdings),
            "applications": {"items": list(r.applications)},
            "journal_entry_id": str(r.journal_entry_id) if r.journal_entry_id else None,
            "version": r.version,
            "audit": audit_stamp(r),
        },
    )


def open_item_json(ctx: TenantContext, i: ArOpenItem) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.ar_open_item",
        {
            "id": str(i.id),
            "tenant_id": str(i.tenant_id),
            "company_id": str(i.company_id),
            "status": i.status,
            "party": party_ref(i.party),
            "document": {"type": i.document_type, "id": str(i.document_id), "number": i.document_number},
            "document_date": i.document_date.isoformat(),
            "due_date": i.due_date.isoformat(),
            "currency": i.currency,
            "amount": money(i.amount, i.currency),
            "balance": money(i.balance, i.currency),
            "amount_cop": money(i.amount_cop, "COP"),
            "balance_cop": money(i.balance_cop, "COP"),
            "version": i.version,
            "audit": audit_stamp(i),
        },
    )


def write_off_json(ctx: TenantContext, w: WriteOff) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.write_off",
        {
            "id": str(w.id),
            "tenant_id": str(w.tenant_id),
            "company_id": str(w.company_id),
            "number": w.number,
            "document_date": w.document_date.isoformat(),
            "status": CONTRACT_STATUS.get(w.status, w.status),
            "document_status": w.status,
            "party": party_ref(w.party),
            "open_item_id": str(w.open_item_id),
            "amount": money(w.amount, w.open_item.currency),
            "reason": w.reason,
            "approval_request_id": str(w.approval_request_id) if w.approval_request_id else None,
            "journal_entry_id": str(w.journal_entry_id) if w.journal_entry_id else None,
            "version": w.version,
            "audit": audit_stamp(w),
        },
    )


def return_json(ctx: TenantContext, r: SalesReturn) -> dict[str, Any]:
    return masked(
        ctx,
        "sales.return",
        {
            "id": str(r.id),
            "tenant_id": str(r.tenant_id),
            "company_id": str(r.company_id),
            "number": r.number,
            "document_date": r.document_date.isoformat(),
            "status": CONTRACT_STATUS.get(r.status, r.status),
            "document_status": r.status,
            "original": {"type": "sales.invoice", "id": str(r.invoice_id), "number": r.invoice.number},
            "warehouse_id": str(r.warehouse_id),
            "lines": list(r.lines),
            "reason": r.reason,
            "approval_request_id": str(r.approval_request_id) if r.approval_request_id else None,
            "credit_note_id": str(r.credit_note_id) if r.credit_note_id else None,
            "version": r.version,
            "audit": audit_stamp(r),
        },
    )
