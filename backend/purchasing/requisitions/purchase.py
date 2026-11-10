"""Purchase requisitions (``/api/v1/purchasing/requisitions``, P11 ``PurchaseRequisition``).

A manual purchase request of a buyer/requester: ``DRAFT → SUBMITTED → APPROVED / REJECTED`` (P07 approval, the
requester never approves) → ``:to-rfq`` (DRAFT RFQ through ``purchasing.interfaces.rfq_from_requisition``, lines keep
``source_line_id`` lineage) or ``:to-po`` (DRAFT purchase order through ``draft_order``; it enters the normal PO
approval). Both conversions set the requisition to ``POSTED`` with ``target`` = the created document.
Goods requests coming from warehouses start as internal requisitions (F-INV-REQ supersedes this entity for them).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.workflow import WorkflowService
from kernel.money import AMOUNT, round_half_up
from kernel.uow import UnitOfWork
from purchasing import interfaces as purchasing_contract
from purchasing.requisitions import common as c
from purchasing.requisitions.errors import (
    InvalidStateTransition,
    ReasonRequired,
    RequisitionNotApproved,
    SodViolation,
)
from purchasing.requisitions.models import PurchaseRequisition, PurchaseRequisitionLine

P = PurchaseRequisition.Status
DOC_TYPE = "purchasing.requisition"
FIELDS = ("document_date", "posting_date", "currency", "notes", "needed_by", "lines")
LINE_FIELDS = ("line_no", "item_id", "description", "quantity", "unit_price", "tax_code", "account_code", "dimensions")


def ref(p: PurchaseRequisition) -> DocumentRef:
    return DocumentRef(DOC_TYPE, p.id, p.number)


def _transition(p: PurchaseRequisition, allowed: tuple[str, ...], action: str) -> None:
    if p.status not in allowed:
        raise InvalidStateTransition(f"cannot {action} a purchase requisition in status {p.status}")


def _lines(p: PurchaseRequisition, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a purchase requisition needs at least one line", field="lines")
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        c.reject_unknown(line, LINE_FIELDS, f" in lines[{i}]")
        line_no = line.get("line_no", i + 1)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        item_id = c.uuid_in(line.get("item_id"), f"lines[{i}].item_id", required=False)
        description = c.text_in(line.get("description"), "description", 255)
        if item_id is None and not description:
            raise ValidationFailed(f"lines[{i}] needs an item_id or a description", field="lines")
        qty, uom = c.qty_in(line.get("quantity"), f"lines[{i}].quantity")
        price = c.money_in(line.get("unit_price"), f"lines[{i}].unit_price")
        PurchaseRequisitionLine.objects.create(
            tenant_id=p.tenant_id,
            company_id=p.company_id,
            requisition=p,
            line_no=line_no,
            item_id=item_id,
            description=description,
            quantity=qty,
            uom=(uom or "UND")[:20],
            unit_price=round_half_up(price or 0, AMOUNT),
            tax_code=c.text_in(line.get("tax_code"), "tax_code", 40),
            account_code=c.text_in(line.get("account_code"), "account_code", 40),
            dimensions=c.dims_in(line.get("dimensions"), f"lines[{i}].dimensions"),
        )


def create(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PurchaseRequisition:
    ctx = uow.ctx
    ctx.require_company(company_id)
    c.require(ctx, "purchasing.requisition.create", {"company_id": company_id})
    c.reject_unknown(data, FIELDS)
    doc_date = c.date_in(data.get("document_date"), "document_date")
    currency = str(data.get("currency") or "COP")
    if len(currency) != 3 or not currency.isupper():
        raise ValidationFailed("currency must be an ISO 4217 code", field="currency")
    p = PurchaseRequisition.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=c.next_number(uow, company_id, "purchase_requisition", doc_date),
        document_date=doc_date,
        needed_by=c.date_in(data["needed_by"], "needed_by") if data.get("needed_by") else None,
        requester_id=ctx.principal_id,
        currency=currency,
        notes=c.text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _lines(p, data.get("lines"))
    c.audit(uow, "purchasing.requisition.create", DOC_TYPE, p.id, company_id, {"number": p.number})
    return p


def update(uow: UnitOfWork, p: PurchaseRequisition, data: Mapping[str, Any]) -> PurchaseRequisition:
    c.require(uow.ctx, "purchasing.requisition.update", p)
    _transition(p, (P.DRAFT,), "edit")
    c.reject_unknown(data, FIELDS)
    if not data:
        raise ValidationFailed("nothing to update")
    fields: list[str] = []
    if "document_date" in data:
        p.document_date = c.date_in(data["document_date"], "document_date")
        fields.append("document_date")
    if "needed_by" in data:
        p.needed_by = c.date_in(data["needed_by"], "needed_by") if data["needed_by"] else None
        fields.append("needed_by")
    if "notes" in data:
        p.notes = c.text_in(data["notes"], "notes")
        fields.append("notes")
    if "lines" in data:
        p.lines.all().delete()
        _lines(p, data["lines"])
    p.save_versioned(uow.ctx.principal_id, fields)
    return p


def delete(uow: UnitOfWork, p: PurchaseRequisition) -> None:
    c.require(uow.ctx, "purchasing.requisition.delete", p)
    _transition(p, (P.DRAFT,), "delete")
    c.audit(uow, "purchasing.requisition.delete", DOC_TYPE, p.id, p.company_id, {"number": p.number})
    p.delete()


def submit(uow: UnitOfWork, p: PurchaseRequisition) -> PurchaseRequisition:
    c.require(uow.ctx, "purchasing.requisition.update", p)
    _transition(p, (P.DRAFT,), "submit")
    approval = services.get(WorkflowService).request_approval(uow, p.company_id, ref(p), DOC_TYPE)
    p.status = P.SUBMITTED
    p.approval_request_id = approval.id
    p.save_versioned(uow.ctx.principal_id, ["status", "approval_request_id"])
    return p


def decide(uow: UnitOfWork, p: PurchaseRequisition, approve: bool, reason: str) -> PurchaseRequisition:
    ctx = uow.ctx
    c.require(ctx, "purchasing.requisition.approve", p)
    _transition(p, (P.SUBMITTED,), "approve" if approve else "reject")
    if not approve and not reason.strip():
        raise ReasonRequired("a reason is required to reject", field="reason")
    if ctx.principal_id == p.requester_id:
        raise SodViolation("the requester cannot decide the own purchase requisition (D-023)")
    if p.approval_request_id is None:
        raise InvalidStateTransition("the purchase requisition has no approval request")
    services.get(WorkflowService).decide(uow, p.approval_request_id, approve, reason)
    p.status = P.APPROVED if approve else P.REJECTED
    p.decided_by = ctx.principal_id
    p.decided_at = clock.now()
    p.save_versioned(ctx.principal_id, ["status", "decided_by", "decided_at"])
    c.audit(uow, f"purchasing.requisition.{'approve' if approve else 'reject'}", DOC_TYPE, p.id, p.company_id, {})
    return p


def _require_approved(p: PurchaseRequisition) -> None:
    if p.status != P.APPROVED:
        raise RequisitionNotApproved(f"purchase requisition {p.number} is {p.status}")


def to_rfq(uow: UnitOfWork, p: PurchaseRequisition, params: Mapping[str, Any]) -> PurchaseRequisition:
    """``parameters``: ``invited_vendors`` (supplier ids, ≥ 1), optional ``deadline``."""
    c.require(uow.ctx, "purchasing.requisition.update", p)
    _require_approved(p)
    c.reject_unknown(params, ("invited_vendors", "deadline"), " in parameters")
    lines = []
    for ln in p.lines.order_by("line_no"):
        if ln.item_id is None:
            raise ValidationFailed("only item lines can go to an RFQ", field="lines")
        lines.append(
            {
                "line_no": ln.line_no,
                "item_id": str(ln.item_id),
                "description": ln.description,
                "quantity": {"value": str(ln.quantity), "uom": ln.uom},
                "source_line_id": str(ln.id),
            }
        )
    data: dict[str, Any] = {
        "document_date": clock.business_date().isoformat(),
        "currency": p.currency,
        "invited_vendors": list(params.get("invited_vendors") or []),
        "lines": lines,
    }
    if params.get("deadline"):
        data["deadline"] = params["deadline"]
    rfq, _warnings = purchasing_contract.rfq_from_requisition(uow, p.company_id, ref(p), data)
    return _posted(uow, p, {"type": "purchasing.rfq", "id": str(rfq.id), "number": rfq.number})


def to_po(uow: UnitOfWork, p: PurchaseRequisition, params: Mapping[str, Any]) -> PurchaseRequisition:
    """``parameters``: ``party`` (supplier id); prices are the requisition line prices."""
    c.require(uow.ctx, "purchasing.requisition.update", p)
    _require_approved(p)
    c.reject_unknown(params, ("party", "expected_date"), " in parameters")
    data: dict[str, Any] = {
        "party": {"id": str(c.uuid_req(params.get("party"), "parameters.party"))},
        "currency": p.currency,
        "notes": f"Desde la solicitud de compra {p.number}",
        "lines": [
            {
                "line_no": ln.line_no,
                "item_id": str(ln.item_id) if ln.item_id else None,
                "description": ln.description,
                "quantity": {"value": str(ln.quantity), "uom": ln.uom},
                "unit_price": {"amount": str(ln.unit_price), "currency": p.currency},
                "tax_code": ln.tax_code or None,
                "dimensions": dict(ln.dimensions),
            }
            for ln in p.lines.order_by("line_no")
        ],
    }
    if params.get("expected_date"):
        data["expected_date"] = params["expected_date"]
    order = purchasing_contract.draft_order(uow, p.company_id, ref(p), data)
    return _posted(uow, p, {"type": "purchasing.order", "id": str(order.id), "number": order.number})


def _posted(uow: UnitOfWork, p: PurchaseRequisition, target: dict[str, str]) -> PurchaseRequisition:
    p.status = P.POSTED
    p.target = target
    p.save_versioned(uow.ctx.principal_id, ["status", "target"])
    c.audit(uow, "purchasing.requisition.convert", DOC_TYPE, p.id, p.company_id, {"target": target})
    return p
