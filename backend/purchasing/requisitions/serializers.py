"""JSON shapes of the F-INV-REQ resources (contract schemas ``InternalRequisition``, ``Consolidation``,
``PurchaseRequisition``). Extra read-only fields are listed in CCR P11b-2 (``requisition_status``,
per-line sourcing state and lineage)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from kernel.money import q_amount
from purchasing.requisitions.common import qty_json
from purchasing.requisitions.models import (
    BuyerTask,
    ConsolidationBatch,
    ConsolidationLine,
    InternalRequisition,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequisitionLine,
)

ZERO = Decimal(0)
#: contract ``status`` enum (DRAFT, SUBMITTED, APPROVED, REJECTED, POSTED, REVERSED, CANCELLED)
CONTRACT_STATUS = {
    "IN_PROGRESS": "APPROVED",
    "FULFILLED": "POSTED",
    "PARTIALLY_FULFILLED_CLOSED": "POSTED",
}


def audit_stamp(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat() if getattr(obj, "created_at", None) else None,
        "created_by": str(obj.created_by) if getattr(obj, "created_by", None) else None,
        "updated_at": obj.updated_at.isoformat() if getattr(obj, "updated_at", None) else None,
        "updated_by": str(obj.updated_by) if getattr(obj, "updated_by", None) else None,
    }


def money(value: Decimal | None, currency: str = "COP") -> dict[str, str] | None:
    return None if value is None else {"amount": str(q_amount(value)), "currency": currency}


def line_json(line: RequisitionLine) -> dict[str, Any]:
    amount = None if line.unit_price is None else line.unit_price * line.quantity
    sourced = line.qty_transfer + line.qty_procure
    return {
        "line_no": line.line_no,
        "item_id": str(line.item_id),
        "presentation_id": str(line.presentation_id) if line.presentation_id else None,
        "description": line.description,
        "quantity": qty_json(line.quantity, line.uom),
        "unit_price": money(line.unit_price),
        "amount": money(amount),
        "tax_code": None,
        "account_code": None,
        "dimensions": dict(line.dimensions),
        "sourced_qty": qty_json(sourced, line.uom if line.presentation_id is None else "base"),
        "remaining_qty": qty_json(line.remaining, line.uom if line.presentation_id is None else "base"),
        # CCR P11b-2 (read-only additions)
        "id": str(line.id),
        "base_qty": str(line.base_qty),
        "line_status": line.status,
        "qty_transfer": str(line.qty_transfer),
        "qty_procure": str(line.qty_procure),
        "qty_cancelled": str(line.qty_cancelled),
        "fulfilled_qty": str(line.fulfilled_qty),
        "delivery_warehouse_id": str(line.delivery_warehouse_id),
        "needed_by": line.needed_by.isoformat(),
        "lineage": list(line.lineage),
    }


def requisition_json(r: InternalRequisition, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    lines = list(r.lines.order_by("line_no"))
    total = sum(((ln.unit_price or ZERO) * ln.quantity for ln in lines), ZERO)
    out = {
        "id": str(r.id),
        "tenant_id": str(r.tenant_id),
        "company_id": str(r.company_id),
        "number": r.number,
        "document_date": r.document_date.isoformat(),
        "posting_date": None,
        "status": CONTRACT_STATUS.get(r.status, r.status),
        "currency": r.currency,
        "total": money(total),
        "notes": r.notes,
        "source": r.source,
        "requesting_warehouse_id": str(r.requesting_warehouse_id),
        "needed_by": r.needed_by.isoformat(),
        "priority": r.priority,
        "dimensions": dict(r.dimensions),
        "lines": [line_json(ln) for ln in lines],
        "version": r.version,
        "audit": audit_stamp(r),
        # CCR P11b-2
        "requisition_status": r.status,
        "requester_id": str(r.requester_id),
        "decided_by": str(r.decided_by) if r.decided_by else None,
    }
    if extra:
        out.update(extra)
    return out


def task_json(t: BuyerTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "reason_code": t.reason_code,
        "message": t.message,
        "estimated_total": money(t.estimated_total),
        "status": t.status,
    }


def consolidation_line_json(cl: ConsolidationLine) -> dict[str, Any]:
    return {
        "line_no": cl.line_no,
        "item_id": str(cl.item_id),
        "presentation_id": str(cl.presentation_id) if cl.presentation_id else None,
        "needed_week": cl.needed_week.isoformat(),
        "delivery_warehouse_id": str(cl.delivery_warehouse_id),
        "quantity": str(cl.quantity),
        "status": cl.status,
        "purchase_order_id": str(cl.purchase_order_id) if cl.purchase_order_id else None,
        "purchase_order_line_no": cl.purchase_order_line_no,
        "unit_price": money(cl.unit_price),
        "fulfilled_qty": str(cl.fulfilled_qty),
        "buyer_tasks": [task_json(t) for t in cl.tasks.order_by("created_at")],
    }


def consolidation_json(b: ConsolidationBatch) -> dict[str, Any]:
    lines = list(b.lines.order_by("line_no"))
    return {
        "id": str(b.id),
        "tenant_id": str(b.tenant_id),
        "company_id": str(b.company_id),
        "status": b.status,
        "number": b.number,
        "rule": b.rule,
        "lineage": {str(cl.line_no): list(cl.sources) for cl in lines},
        "lines": {str(cl.line_no): consolidation_line_json(cl) for cl in lines},
        "run_at": b.run_at.isoformat(),
        "version": b.version,
        "audit": audit_stamp(b),
    }


def preq_line_json(line: PurchaseRequisitionLine, currency: str) -> dict[str, Any]:
    return {
        "line_no": line.line_no,
        "item_id": str(line.item_id) if line.item_id else None,
        "description": line.description,
        "quantity": qty_json(line.quantity, line.uom),
        "unit_price": money(line.unit_price, currency),
        "amount": money(line.unit_price * line.quantity, currency),
        "tax_code": line.tax_code or None,
        "account_code": line.account_code or None,
        "dimensions": dict(line.dimensions),
    }


def purchase_requisition_json(p: PurchaseRequisition) -> dict[str, Any]:
    lines = list(p.lines.order_by("line_no"))
    return {
        "id": str(p.id),
        "tenant_id": str(p.tenant_id),
        "company_id": str(p.company_id),
        "number": p.number,
        "document_date": p.document_date.isoformat(),
        "posting_date": None,
        "status": p.status,
        "currency": p.currency,
        "total": money(sum((ln.unit_price * ln.quantity for ln in lines), ZERO), p.currency),
        "notes": p.notes,
        "source": p.source,
        "needed_by": p.needed_by.isoformat() if p.needed_by else None,
        "lines": [preq_line_json(ln, p.currency) for ln in lines],
        "version": p.version,
        "audit": audit_stamp(p),
        "target": p.target,  # CCR P11b-2
    }
