"""JSON views of the POS resources (contract ``contracts/openapi/modules/pos.yaml`` + CCR P12-1 additive fields).

Decimals travel as strings (Money = ``{amount, currency}``). Field security (P04 field policy, fail closed):
fields classified ``confidential`` / PII in ``pos.registry.FIELDS`` are emitted only when the principal's
``PolicyService.field_mask`` lists them as readable — the cashier never receives costs, drawer totals or buyer data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import DomainError
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.fiscal import FiscalDocuments
from kernel.interfaces.policy import PolicyService
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from pos.models import CashMovement, CashSession, PosConfig, PosReturn, Terminal, Ticket
from pos.registry import FIELDS

HIDDEN = ("confidential", "restricted-pii", "sensitive-pii", "secret")


def money(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    return {"amount": f"{q_amount(Decimal(value)):f}", "currency": "COP"}


def _readable(ctx: TenantContext, resource: str) -> frozenset[str]:
    return frozenset(services.get(PolicyService).field_mask(ctx, resource).readable)


def masked(ctx: TenantContext, resource: str, data: dict[str, Any]) -> dict[str, Any]:
    hidden = {k for k, v in FIELDS.get(resource, {}).items() if v in HIDDEN}
    for name in hidden - _readable(ctx, resource):
        data.pop(name, None)
    return data


def _audit(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat(),
        "created_by": str(obj.created_by) if obj.created_by else None,
        "updated_at": obj.updated_at.isoformat(),
        "updated_by": str(obj.updated_by) if obj.updated_by else None,
    }


def _ids(obj: Any) -> dict[str, Any]:
    return {"id": str(obj.id), "tenant_id": str(obj.tenant_id), "company_id": str(obj.company_id)}


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


def config_json(ctx: TenantContext, c: PosConfig) -> dict[str, Any]:
    data = {
        **_ids(c),
        "name": c.name,
        "active": c.active,
        "branch_id": str(c.branch_id),
        "warehouse_id": str(c.warehouse_id),
        "location_id": _opt(c.location_id),
        "price_list_id": _opt(c.price_list_id),
        "numbering_range_id": _opt(c.numbering_range_id),
        "allowed_tenders": list(c.allowed_tenders),
        "receipt_profile": dict(c.receipt_profile),
        "cash_limit": money(c.cash_limit),
        "allow_returns": c.allow_returns,
        "blind_count": c.blind_count,
        "default_party_id": _opt(c.default_party_id),
        "version": c.version,
        "audit": _audit(c),
    }
    return masked(ctx, "pos.config", data)


def terminal_json(ctx: TenantContext, t: Terminal) -> dict[str, Any]:
    data = {
        **_ids(t),
        "code": t.code,
        "name": t.name,
        "status": t.status,
        "config_id": str(t.config_id),
        "device_fingerprint": t.device_fingerprint,
        "registered_at": t.registered_at.isoformat() if t.registered_at else None,
        "version": t.version,
        "audit": _audit(t),
    }
    return masked(ctx, "pos.terminal", data)


def session_json(ctx: TenantContext, s: CashSession) -> dict[str, Any]:
    closed = s.status == CashSession.Status.CLOSED
    data = {
        **_ids(s),
        "number": s.number,
        "status": s.status,
        "terminal_id": str(s.terminal_id),
        "config_id": str(s.config_id),
        "cashier_id": str(s.cashier_id),
        "opened_at": s.opened_at.isoformat(),
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
        "business_date": s.business_date.isoformat(),
        "opening_cash": money(s.opening_cash),
        "blind": s.blind,
        "counted_cash": money(s.counted_cash),
        "difference": money(s.difference),
        "journal_entry_ids": list(s.journal_entry_ids),
        "version": s.version,
        "audit": _audit(s),
    }
    if closed or not s.blind:  # blind count: the expected amounts are not shown before the close commits
        data["expected_cash"] = money(s.expected_cash)
        data["expected"] = dict(s.expected)
        data["counted"] = dict(s.counted)
        data["deposit_amount"] = money(s.deposit_amount)
    return masked(ctx, "pos.session", data)


def _cude(t: Ticket) -> tuple[str | None, str]:
    svc = services.optional(FiscalDocuments)
    if svc is None or t.status != Ticket.Status.POSTED:
        return None, t.fiscal_status
    try:
        info = svc.status(t.company_id, DocumentRef("pos.ticket", t.id, t.number or ""))
    except (LookupError, DomainError):
        return None, t.fiscal_status
    if info.unique_code is None and info.status == "DRAFT":  # no fiscal document yet: P15 has not picked it up
        return None, t.fiscal_status
    return info.unique_code, str(info.status)


def ticket_json(ctx: TenantContext, t: Ticket) -> dict[str, Any]:
    line_mask = {k for k, v in FIELDS["pos.ticket_line"].items() if v in HIDDEN} - _readable(ctx, "pos.ticket_line")
    lines: list[dict[str, Any]] = []
    for x in t.lines.order_by("line_no") if t.status != Ticket.Status.DRAFT else []:
        row = {
            "line_no": x.line_no,
            "item_id": str(x.item_id),
            "presentation_id": _opt(x.presentation_id),
            "barcode": x.barcode,
            "description": x.description,
            "quantity": {"value": f"{x.quantity:f}", "uom": "PKG" if x.presentation_id else "UND"},
            "factor": f"{x.factor:f}",
            "base_qty": f"{x.base_qty:f}",
            "unit_price": money(x.unit_price),
            "discount_pct": f"{x.discount_pct:f}",
            "amount": money(x.amount),
            "tax_code": x.tax_code or None,
            "tax_amount": money(x.tax_amount),
            "price_source": x.price_source,
            "cost_value": money(x.cost_value),
            "unit_cost": f"{x.unit_cost:f}",
        }
        for name in line_mask:
            row.pop(name, None)
        lines.append(row)
    cude, fiscal_status = _cude(t)
    data: dict[str, Any] = {
        **_ids(t),
        "number": t.number or "",
        "document_date": t.document_date.isoformat(),
        "posting_date": t.document_date.isoformat() if t.status == Ticket.Status.POSTED else None,
        "status": t.status,
        "party": {"id": str(t.party_id), "kind": "customer"} if t.party_id else None,
        "currency": t.currency,
        "subtotal": money(t.subtotal),
        "tax_total": money(t.tax_total),
        "total": money(t.total),
        "change_due": money(t.change_due),
        "notes": t.notes,
        "source": {"type": "pos.session", "id": str(t.session_id), "number": None},
        "session_id": str(t.session_id),
        "client_uuid": str(t.client_uuid),
        "cashier_id": str(t.cashier_id),
        "branch_id": str(t.branch_id),
        "warehouse_id": str(t.warehouse_id),
        "payments": {
            "items": [
                {
                    "type": p.tender_type,
                    "amount": money(p.amount),
                    "tendered": money(p.tendered),
                    "reference": p.reference,
                }
                for p in t.tenders.order_by("line_no")
            ]
        },
        "cost_total": money(t.cost_total),
        "fiscal_doc_type": t.fiscal_doc_type,
        "fiscal_status": fiscal_status,
        "cude": cude,
        "journal_entry_id": _opt(t.journal_entry_id),
        "lines": lines,
        "draft": dict(t.draft_payload) if t.status == Ticket.Status.DRAFT else None,
        "watermark": "BORRADOR – sin valor fiscal" if t.status == Ticket.Status.DRAFT else None,
        "version": t.version,
        "audit": _audit(t),
    }
    req = getattr(t, "invoice_request", None) if t.status == Ticket.Status.POSTED else None
    if req is not None:
        data["invoice_request"] = {"status": req.status, "buyer": dict(req.buyer)}
    return masked(ctx, "pos.ticket", data)


def return_json(ctx: TenantContext, r: PosReturn) -> dict[str, Any]:
    line_mask = {"cost_value"} - _readable(ctx, "pos.ticket_line")
    lines = []
    for x in r.lines.select_related("ticket_line").order_by("line_no"):
        row = {
            "line_no": x.line_no,
            "original_line_no": x.ticket_line.line_no,
            "item_id": str(x.ticket_line.item_id),
            "description": x.ticket_line.description,
            "quantity": {"value": f"{x.quantity:f}", "uom": "PKG" if x.ticket_line.presentation_id else "UND"},
            "base_qty": f"{x.base_qty:f}",
            "unit_price": money(x.ticket_line.unit_price),
            "amount": money(x.amount),
            "tax_code": x.ticket_line.tax_code or None,
            "tax_amount": money(x.tax_amount),
            "cost_value": money(x.cost_value),
        }
        for name in line_mask:
            row.pop(name, None)
        lines.append(row)
    data = {
        **_ids(r),
        "number": r.number,
        "document_date": r.document_date.isoformat(),
        "posting_date": r.document_date.isoformat() if r.status == PosReturn.Status.POSTED else None,
        "status": r.status,
        "party": None,
        "currency": "COP",
        "total": money(r.total),
        "subtotal": money(r.subtotal),
        "tax_total": money(r.tax_total),
        "notes": r.notes,
        "reason": r.reason,
        "source": {"type": "pos.session", "id": str(r.session_id), "number": None},
        "session_id": str(r.session_id),
        "original": {"type": "pos.ticket", "id": str(r.original_id), "number": r.original.number},
        "refund_tender": r.refund_tender,
        "cost_total": money(r.cost_total),
        "journal_entry_id": _opt(r.journal_entry_id),
        "lines": lines,
        "version": r.version,
        "audit": _audit(r),
    }
    return masked(ctx, "pos.return", data)


def movement_json(ctx: TenantContext, m: CashMovement) -> dict[str, Any]:
    data = {
        **_ids(m),
        "number": m.number,
        "status": m.status,
        "session_id": str(m.session_id),
        "movement_type": m.movement_type,
        "amount": money(m.amount),
        "reason": m.reason,
        "journal_entry_id": _opt(m.journal_entry_id),
        "version": m.version,
        "audit": _audit(m),
    }
    return masked(ctx, "pos.cash_movement", data)
