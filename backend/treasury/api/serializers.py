"""JSON views of the treasury resources (contract schemas in contracts/openapi/modules/treasury.yaml).

Decimals travel as strings; confidential fields (``x-classification: confidential``: voucher ``party``, bank
``account_number``) are emitted only when the principal's field policy can read them (``PolicyService.field_mask``;
P04 field registry, fail closed). Bank account numbers are never returned in clear: at most ``****1234``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from iam.policy import fields as field_registry
from kernel import services
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from treasury import crypto
from treasury.models import (
    DIM_SLOTS,
    BankAccount,
    BankStatement,
    CashBox,
    Fund,
    PaymentBatch,
    StatementLine,
    TreasuryConcept,
    TreasuryVoucher,
    TreasuryVoucherLine,
)

RECEIPT = "treasury.receipt"
DISBURSEMENT = "treasury.disbursement"
CONFIDENTIAL = {
    RECEIPT: frozenset({"party"}),
    DISBURSEMENT: frozenset({"party"}),
    "treasury.bank_account": frozenset({"account_number"}),
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
    voucher = {
        "party": "confidential",
        "fund_id": "internal",
        "payment_method": "internal",
        "reference": "internal",
        "withholdings": "internal",
        "branch_id": "internal",
    }
    master = {"code": "internal", "name": "internal", "fund_id": "internal", "account_id": "internal"}
    extra = {
        RECEIPT: voucher,
        DISBURSEMENT: voucher,
        "treasury.fund": {**master, "fund_type": "internal", "allow_overdraft": "internal"},
        "treasury.bank_account": {**master, "bank_code": "internal", "account_number": "confidential"},
        "treasury.cash_box": master,
        "treasury.concept": {
            **master,
            "direction": "internal",
            "account_role": "internal",
            "withholding_concept_ids": "internal",
            "requires_dimension": "internal",
        },
        "treasury.payment_batch": {"fund_id": "internal", "payment_date": "internal"},
        "treasury.statement": {"bank_account_id": "internal", "period": "internal"},
        "treasury.statement_line": {
            "statement_id": "internal",
            "amount": "internal",
            "value_date": "internal",
            "description": "internal",
            "matched": "internal",
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
    return data


def d(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:f}"


def money(value: Decimal | None, currency: str) -> dict[str, str] | None:
    return None if value is None else {"amount": f"{value:f}", "currency": currency}


def audit_json(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(obj, "created_at", None):
        out["created_at"] = obj.created_at.isoformat()
    if getattr(obj, "created_by", None):
        out["created_by"] = str(obj.created_by)
    if getattr(obj, "updated_at", None):
        out["updated_at"] = obj.updated_at.isoformat()
    if getattr(obj, "updated_by", None):
        out["updated_by"] = str(obj.updated_by)
    return out


def _base(obj: Any) -> dict[str, Any]:
    return {"id": str(obj.id), "tenant_id": str(obj.tenant_id), "company_id": str(obj.company_id)}


def _master(obj: Any) -> dict[str, Any]:
    return {**_base(obj), "code": obj.code, "name": obj.name, "status": obj.status}


# --------------------------------------------------------------------------- masters


def fund_json(f: Fund) -> dict[str, Any]:
    return {
        **_master(f),
        "fund_type": f.fund_type,
        "allow_overdraft": f.allow_overdraft,
        "version": f.version,
        "audit": audit_json(f),
    }


def bank_account_json(ctx: TenantContext, b: BankAccount) -> dict[str, Any]:
    data = {
        **_master(b),
        "bank_code": b.bank_code,
        "account_number": crypto.masked(b.account_number_last4),  # never in clear (T-P16-09)
        "currency": b.currency,
        "account_id": str(b.account_id),
        "fund_id": str(b.fund_id) if b.fund_id else None,
        "version": b.version,
        "audit": audit_json(b),
    }
    return masked(ctx, "treasury.bank_account", data)


def cash_box_json(c: CashBox) -> dict[str, Any]:
    return {
        **_master(c),
        "currency": c.currency,
        "account_id": str(c.account_id),
        "fund_id": str(c.fund_id) if c.fund_id else None,
        "version": c.version,
        "audit": audit_json(c),
    }


def concept_json(c: TreasuryConcept) -> dict[str, Any]:
    return {
        **_master(c),
        "direction": c.direction,
        "account_role": c.account_role,
        "withholding_concept_ids": list(c.withholding_concept_ids or []),
        "requires_dimension": c.requires_dimension,
        "version": c.version,
        "audit": audit_json(c),
    }


# --------------------------------------------------------------------------- vouchers


def _line_json(ln: TreasuryVoucherLine, currency: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "line_no": ln.line_no,
        "item_id": None,
        "description": ln.description,
        "amount": money(ln.amount, currency),
        "tax_code": ln.tax_code or None,
        "account_code": None,
        "dimensions": {s: (str(getattr(ln, s)) if getattr(ln, s) else None) for s in DIM_SLOTS},
        "concept_id": str(ln.concept_id) if ln.concept_id else None,
        "open_item_id": str(ln.open_item_id) if ln.open_item_id else None,
    }
    if ln.kind in (TreasuryVoucherLine.Kind.AR_APPLICATION, TreasuryVoucherLine.Kind.AP_APPLICATION):
        out["applied_amount"] = money(ln.amount, currency)
    elif ln.kind == TreasuryVoucherLine.Kind.WITHHOLDING:
        out["unit_price"] = money(ln.tax_base, currency)  # the tax base
    else:
        out["applied_amount"] = money(ln.amount, currency)
    return out


def voucher_json(ctx: TenantContext, v: TreasuryVoucher) -> dict[str, Any]:
    resource = RECEIPT if v.voucher_type == TreasuryVoucher.Type.RC else DISBURSEMENT
    lines = list(v.lines.order_by("line_no"))
    withholdings = {
        "total": money(v.withholding_total, v.currency),
        "lines": [
            {
                "line_no": ln.line_no,
                "tax_code": ln.tax_code,
                "base": d(ln.tax_base),
                "rate": d(ln.tax_rate),
                "amount": d(ln.amount),
            }
            for ln in lines
            if ln.kind == TreasuryVoucherLine.Kind.WITHHOLDING
        ],
        "parameters": (v.tax_snapshot or {}).get("parameters", []),
        "warnings": (v.tax_snapshot or {}).get("warnings", []),
    }
    source = None
    if v.reversal_of_id:
        source = {"type": resource, "id": str(v.reversal_of_id), "number": v.source_number or None}
    elif v.source_id:
        source = {"type": v.source_type, "id": str(v.source_id), "number": v.source_number or None}
    data: dict[str, Any] = {
        **_base(v),
        "number": v.number,
        "document_date": v.document_date.isoformat(),
        "posting_date": v.posting_date.isoformat() if v.posting_date else None,
        "status": v.status,
        "party": {"id": str(v.party_id), "kind": v.party_kind or None} if v.party_id else None,
        "currency": v.currency,
        "total": money(v.total, v.currency),
        "notes": v.notes,
        "source": source,
        "fund_id": str(v.fund_id),
        "payment_method": v.payment_method,
        "reference": v.reference or None,
        "withholdings": withholdings,
        "branch_id": str(v.branch_id) if v.branch_id else None,
        "lines": [_line_json(ln, v.currency) for ln in lines],
        "version": v.version,
        "audit": audit_json(v),
    }
    if data["party"] is None:
        data.pop("party")
    else:
        data["party"] = {k: val for k, val in data["party"].items() if val is not None}
    if data["source"] is None:
        data.pop("source")
    return masked(ctx, resource, data)


# --------------------------------------------------------------------------- batches / statements


def batch_json(b: PaymentBatch) -> dict[str, Any]:
    return {
        **_base(b),
        "number": b.number,
        "document_date": b.document_date.isoformat(),
        "posting_date": b.executed_at.date().isoformat() if b.executed_at else None,
        "status": b.status,
        "currency": b.currency,
        "total": money(b.total, b.currency),
        "notes": b.notes,
        "fund_id": str(b.fund_id),
        "payment_date": b.payment_date.isoformat(),
        "lines": [
            {
                "line_no": ln.line_no,
                "item_id": None,
                "description": ln.description,
                "amount": money(ln.amount, b.currency),
                "tax_code": ln.tax_code or None,
                "account_code": None,
                "dimensions": {s: (str(getattr(ln, s)) if getattr(ln, s) else None) for s in DIM_SLOTS},
            }
            for ln in b.lines.order_by("line_no")
        ],
        "version": b.version,
        "audit": audit_json(b),
    }


def statement_json(s: BankStatement) -> dict[str, Any]:
    return {
        **_base(s),
        "status": s.status,
        "bank_account_id": str(s.bank_account_id),
        "period": {"from": s.date_from.isoformat(), "to": s.date_to.isoformat() if s.date_to else None},
        "version": s.version,
        "audit": audit_json(s),
    }


def statement_line_json(ln: StatementLine) -> dict[str, Any]:
    return {
        **_base(ln),
        "status": ln.state,
        "statement_id": str(ln.statement_id),
        "amount": money(ln.amount, "COP"),
        "value_date": ln.date.isoformat(),
        "description": ln.description,
        "matched": ln.state == StatementLine.State.MATCHED,
        "version": ln.version,
        "audit": audit_json(ln),
    }
