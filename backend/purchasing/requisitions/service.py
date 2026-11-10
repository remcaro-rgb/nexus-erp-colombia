"""F-INV-REQ services: internal requisitions, D-012 sourcing, D-031 consolidation, D-011 procurement, fulfilment.

Flow (FEATURES-ADDENDUM §13):

* ``DRAFT → SUBMITTED → APPROVED / REJECTED`` — approval through P07 ``WorkflowService`` with segregation of duties
  (the requester never approves, D-023; checked here as well, ``sod_violation``).
* Approval runs the **sourcing policy** (system action, ``policy_ref`` D-012, logged as ``system-policy``): for each
  line, stock available in other warehouses of the same company is reserved through a DRAFT transfer created by
  P09 ``Inventory.create_transfer`` (priority list, then highest available quantity, then warehouse code); the
  remainder becomes ``TO_PROCURE``. ``:source`` (mode ``simulate`` | ``apply``) runs the same policy on demand.
* ``consolidations:run`` groups the TO_PROCURE remainders by company + item + presentation + needed-by week +
  delivery warehouse (D-031), keeps the lineage of every source line and, for each consolidated line, creates a
  DRAFT purchase order through P11a ``draft_order`` only when a valid vendor price exists
  (``valid_vendor_price``: approved award ≤ 90 days) and the line total is within the company limit (D-011,
  default COP 5,000,000); otherwise a buyer task (warning ``auto_po_limit_exceeded``). Drafts always enter the
  normal PO approval; nothing is sent to a vendor.
* :func:`sync_fulfilment` reads received transfers (P09) and received PO quantities (P11a) and closes each source
  line; a consolidated receipt is split over its sources in needed-by order.

Concurrency: reservations are made by P09 under row locks and never exceed availability; a sourcing that lost the
race for the last units (``stock_insufficient``) re-reads availability inside a savepoint and sources the rest to
procurement (T-INV-REQ-05).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.db import transaction

from kernel import clock, services
from kernel.errors import DomainError, NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import Inventory, StockLine
from kernel.interfaces.workflow import WorkflowService
from kernel.money import AMOUNT, QTY, round_half_up
from kernel.uow import UnitOfWork
from purchasing import interfaces as purchasing_contract
from purchasing.requisitions import common as c
from purchasing.requisitions.errors import (
    AUTO_PO_LIMIT_EXCEEDED,
    VENDOR_PRICE_MISSING,
    InvalidStateTransition,
    ReasonRequired,
    RequisitionNotApproved,
    SodViolation,
    SourcingOverallocation,
)
from purchasing.requisitions.models import (
    BuyerTask,
    ConsolidationBatch,
    ConsolidationLine,
    InternalRequisition,
    RequisitionLine,
    RequisitionSettings,
    SourcingDecision,
)

S = InternalRequisition.Status
L = RequisitionLine.Status
ZERO = Decimal(0)
DOC_TYPE = "purchasing.internal_requisition"
APPROVAL_POLICY = "purchasing.internal_requisition"
SOURCING_POLICY = "D-012"
CONSOLIDATION_RULE = "D-031"
PROCUREMENT_POLICY = "D-011"
MAX_SOURCING_RETRIES = 3
HEADER_FIELDS = (
    "document_date",
    "posting_date",
    "currency",
    "notes",
    "requesting_warehouse_id",
    "needed_by",
    "priority",
    "dimensions",
    "lines",
)
LINE_FIELDS = (
    "line_no",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "tax_code",
    "account_code",
    "dimensions",
    "presentation_id",
)


def _inventory() -> Inventory:
    return services.get(Inventory)


def _model(name: str) -> Any:
    return apps.get_model("inventory", name)


def doc_ref(req: InternalRequisition) -> DocumentRef:
    return DocumentRef(DOC_TYPE, req.id, req.number)


def _transition(req: InternalRequisition, allowed: tuple[str, ...], action: str) -> None:
    if req.status not in allowed:
        raise InvalidStateTransition(f"cannot {action} a requisition in status {req.status}")


# --------------------------------------------------------------------------- settings


def settings_for(tenant_id: uuid.UUID, company_id: uuid.UUID) -> RequisitionSettings:
    row = RequisitionSettings.objects.filter(tenant_id=tenant_id, company_id=company_id).first()
    return row or RequisitionSettings(tenant_id=tenant_id, company_id=company_id)


def configure(uow: UnitOfWork, company_id: uuid.UUID, **values: Any) -> RequisitionSettings:
    """D-011 limit (``auto_po_limit``, COP) and D-012 priority list (``warehouse_priority``); setup/seed only."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    c.require(ctx, "purchasing.settings.update", {"company_id": company_id})
    c.reject_unknown(values, ("auto_po_limit", "warehouse_priority"))
    row = settings_for(ctx.tenant_id, company_id)
    if "auto_po_limit" in values:
        row.auto_po_limit = round_half_up(c.decimal_in(values["auto_po_limit"], "auto_po_limit"), AMOUNT)
    if "warehouse_priority" in values:
        raw = values["warehouse_priority"]
        if not isinstance(raw, list):
            raise ValidationFailed("warehouse_priority must be a list of warehouse ids", field="warehouse_priority")
        row.warehouse_priority = [str(c.uuid_req(w, "warehouse_priority")) for w in raw]
    if row._state.adding:
        row.created_by = row.updated_by = ctx.principal_id
        row.save()
    else:
        row.save_versioned(ctx.principal_id, ["auto_po_limit", "warehouse_priority"])
    return row


# --------------------------------------------------------------------------- master-data reads (P09, RLS-scoped)


def _warehouse(company_id: uuid.UUID, warehouse_id: uuid.UUID, field_name: str) -> Any:
    wh = _model("Warehouse").objects.filter(company_id=company_id, id=warehouse_id).first()
    if wh is None:
        raise ValidationFailed(f"{field_name} is not a warehouse of the company", field=field_name)
    return wh


def _base_qty(
    company_id: uuid.UUID, item_id: uuid.UUID, presentation_id: uuid.UUID | None, qty: Decimal
) -> tuple[Decimal, str]:
    item = _model("Item").objects.filter(company_id=company_id, id=item_id).select_related("base_uom").first()
    if item is None:
        raise ValidationFailed("item_id is not an item of the company", field="item_id")
    base_uom = str(getattr(item.base_uom, "code", "UND"))
    if presentation_id is None:
        return qty, base_uom
    pres = _model("Presentation").objects.filter(item_id=item_id, id=presentation_id).first()
    if pres is None:
        raise ValidationFailed("presentation_id is not a presentation of the item", field="presentation_id")
    return round_half_up(qty * Decimal(pres.factor), QTY), base_uom


# --------------------------------------------------------------------------- CRUD


def _lines(uow: UnitOfWork, req: InternalRequisition, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a requisition needs at least one line", field="lines")
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        c.reject_unknown(line, LINE_FIELDS, f" in lines[{i}]")
        line_no = line.get("line_no", i + 1)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        item_id = c.uuid_req(line.get("item_id"), f"lines[{i}].item_id")
        presentation_id = c.uuid_in(line.get("presentation_id"), f"lines[{i}].presentation_id", required=False)
        qty, uom = c.qty_in(line.get("quantity"), f"lines[{i}].quantity")
        base, base_uom = _base_qty(req.company_id, item_id, presentation_id, qty)
        price = c.money_in(line.get("unit_price"), f"lines[{i}].unit_price")
        RequisitionLine.objects.create(
            tenant_id=req.tenant_id,
            company_id=req.company_id,
            requisition=req,
            line_no=line_no,
            item_id=item_id,
            presentation_id=presentation_id,
            description=c.text_in(line.get("description"), "description", 255),
            quantity=qty,
            uom=(uom or base_uom)[:20],
            base_qty=base,
            needed_by=req.needed_by,
            delivery_warehouse_id=req.requesting_warehouse_id,
            unit_price=None if price is None else round_half_up(price, AMOUNT),
            dimensions=c.dims_in(line.get("dimensions"), f"lines[{i}].dimensions") or dict(req.dimensions),
        )


def create(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, source: DocumentRef | None = None
) -> InternalRequisition:
    ctx = uow.ctx
    ctx.require_company(company_id)
    c.require(ctx, "purchasing.internal_requisition.create", {"company_id": company_id})
    c.reject_unknown(data, HEADER_FIELDS)
    doc_date = c.date_in(data.get("document_date"), "document_date", clock.business_date())
    warehouse = c.uuid_req(data.get("requesting_warehouse_id"), "requesting_warehouse_id")
    _warehouse(company_id, warehouse, "requesting_warehouse_id")
    needed_by = c.date_in(data.get("needed_by"), "needed_by")
    if needed_by < doc_date:
        raise ValidationFailed("needed_by cannot be before document_date", field="needed_by")
    priority = data.get("priority") or InternalRequisition.Priority.NORMAL
    if priority not in InternalRequisition.Priority.values:
        raise ValidationFailed("priority must be LOW, NORMAL, HIGH or URGENT", field="priority")
    currency = data.get("currency") or "COP"
    if currency != "COP":
        raise ValidationFailed("internal requisitions are valued in COP", field="currency")
    req = InternalRequisition.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=c.next_number(uow, company_id, "internal_requisition", doc_date),
        document_date=doc_date,
        requester_id=ctx.principal_id,
        requesting_warehouse_id=warehouse,
        needed_by=needed_by,
        priority=priority,
        currency="COP",
        notes=c.text_in(data.get("notes"), "notes"),
        dimensions=c.dims_in(data.get("dimensions")),
        source=None if source is None else {"type": source.type, "id": str(source.id), "number": source.number},
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _lines(uow, req, data.get("lines"))
    c.audit(uow, "purchasing.internal_requisition.create", DOC_TYPE, req.id, company_id, {"number": req.number})
    return req


def update(uow: UnitOfWork, req: InternalRequisition, data: Mapping[str, Any]) -> InternalRequisition:
    c.require(uow.ctx, "purchasing.internal_requisition.update", req)
    _transition(req, (S.DRAFT,), "edit")
    c.reject_unknown(data, HEADER_FIELDS)
    if not data:
        raise ValidationFailed("nothing to update")
    fields: list[str] = []
    if "document_date" in data:
        req.document_date = c.date_in(data["document_date"], "document_date")
        fields.append("document_date")
    if "requesting_warehouse_id" in data:
        req.requesting_warehouse_id = c.uuid_req(data["requesting_warehouse_id"], "requesting_warehouse_id")
        _warehouse(req.company_id, req.requesting_warehouse_id, "requesting_warehouse_id")
        fields.append("requesting_warehouse_id")
    if "needed_by" in data:
        req.needed_by = c.date_in(data["needed_by"], "needed_by")
        fields.append("needed_by")
    if req.needed_by < req.document_date:
        raise ValidationFailed("needed_by cannot be before document_date", field="needed_by")
    if "priority" in data:
        if data["priority"] not in InternalRequisition.Priority.values:
            raise ValidationFailed("priority must be LOW, NORMAL, HIGH or URGENT", field="priority")
        req.priority = data["priority"]
        fields.append("priority")
    if "notes" in data:
        req.notes = c.text_in(data["notes"], "notes")
        fields.append("notes")
    if "dimensions" in data:
        req.dimensions = c.dims_in(data["dimensions"])
        fields.append("dimensions")
    if "currency" in data and data["currency"] != "COP":
        raise ValidationFailed("internal requisitions are valued in COP", field="currency")
    if "lines" in data:
        req.lines.all().delete()
        _lines(uow, req, data["lines"])
    else:
        req.lines.update(needed_by=req.needed_by, delivery_warehouse_id=req.requesting_warehouse_id)
    req.save_versioned(uow.ctx.principal_id, fields)
    c.audit(uow, "purchasing.internal_requisition.update", DOC_TYPE, req.id, req.company_id, {"fields": fields})
    return req


def delete(uow: UnitOfWork, req: InternalRequisition) -> None:
    c.require(uow.ctx, "purchasing.internal_requisition.delete", req)
    _transition(req, (S.DRAFT,), "delete")
    c.audit(uow, "purchasing.internal_requisition.delete", DOC_TYPE, req.id, req.company_id, {"number": req.number})
    req.delete()


# --------------------------------------------------------------------------- approval (P07, SoD)


def submit(uow: UnitOfWork, req: InternalRequisition) -> InternalRequisition:
    c.require(uow.ctx, "purchasing.internal_requisition.update", req)
    _transition(req, (S.DRAFT,), "submit")
    approval = services.get(WorkflowService).request_approval(uow, req.company_id, doc_ref(req), APPROVAL_POLICY)
    req.status = S.SUBMITTED
    req.approval_request_id = approval.id
    req.save_versioned(uow.ctx.principal_id, ["status", "approval_request_id"])
    c.audit(uow, "purchasing.internal_requisition.submit", DOC_TYPE, req.id, req.company_id, {})
    return req


def _decide(uow: UnitOfWork, req: InternalRequisition, approve: bool, reason: str) -> None:
    ctx = uow.ctx
    c.require(ctx, "purchasing.internal_requisition.approve", req)
    _transition(req, (S.SUBMITTED,), "approve" if approve else "reject")
    if ctx.principal_id == req.requester_id:
        raise SodViolation("the requester cannot approve or reject the own requisition (D-023)")
    if req.approval_request_id is None:
        raise InvalidStateTransition("the requisition has no approval request")
    services.get(WorkflowService).decide(uow, req.approval_request_id, approve, reason)
    req.status = S.APPROVED if approve else S.REJECTED
    req.decided_by = ctx.principal_id
    req.decided_at = clock.now()
    req.decision_reason = reason
    req.save_versioned(ctx.principal_id, ["status", "decided_by", "decided_at", "decision_reason"])
    c.audit(
        uow,
        f"purchasing.internal_requisition.{'approve' if approve else 'reject'}",
        DOC_TYPE,
        req.id,
        req.company_id,
        {"reason": reason},
    )


def approve(uow: UnitOfWork, req: InternalRequisition, reason: str = "") -> dict[str, Any]:
    """Approve and run the D-012 sourcing policy as a system action in the same unit of work."""
    _decide(uow, req, True, reason)
    return source(uow, req, "apply", by=SourcingDecision.CreatedBy.SYSTEM_POLICY)


def reject(uow: UnitOfWork, req: InternalRequisition, reason: str) -> InternalRequisition:
    if not reason.strip():
        raise ReasonRequired("a reason is required to reject", field="reason")
    _decide(uow, req, False, reason)
    return req


def cancel(uow: UnitOfWork, req: InternalRequisition, reason: str) -> InternalRequisition:
    """Cancel what is not sourced yet; a requisition with fulfilled quantities closes as PARTIALLY_FULFILLED_CLOSED.

    Transfers and purchase orders already created stay (warehouse users / buyers cancel those documents).
    """
    c.require(uow.ctx, "purchasing.internal_requisition.void", req)
    if not reason.strip():
        raise ReasonRequired("a reason is required to cancel", field="reason")
    _transition(req, (S.DRAFT, S.SUBMITTED, S.APPROVED, S.IN_PROGRESS), "cancel")
    any_fulfilled = False
    for line in RequisitionLine.objects.select_for_update().filter(requisition=req).order_by("line_no"):
        rest = line.remaining
        if rest > 0:
            line.qty_cancelled += rest
        if line.fulfilled_qty > 0:
            any_fulfilled = True
        if line.qty_transfer == 0 and line.fulfilled_qty == 0 and _consolidated_qty(line) == 0:
            line.status = L.CANCELLED
        line.save(update_fields=["qty_cancelled", "status"])
    req.status = S.PARTIALLY_FULFILLED_CLOSED if any_fulfilled else S.CANCELLED
    req.decision_reason = reason
    req.save_versioned(uow.ctx.principal_id, ["status", "decision_reason"])
    c.audit(uow, "purchasing.internal_requisition.cancel", DOC_TYPE, req.id, req.company_id, {"reason": reason})
    return req


# --------------------------------------------------------------------------- sourcing (D-012)


@dataclass
class LinePlan:
    line: RequisitionLine
    transfers: list[tuple[Any, Decimal]] = field(default_factory=list)  # (warehouse, qty)
    procure: Decimal = ZERO

    def as_json(self) -> dict[str, Any]:
        qty_t = sum((q for _w, q in self.transfers), ZERO)
        return {
            "line_no": self.line.line_no,
            "item_id": str(self.line.item_id),
            "requested": str(self.line.remaining),
            "transfer": [
                {"from_warehouse_id": str(w.id), "from_warehouse_code": w.code, "qty": str(q)}
                for w, q in self.transfers
            ],
            "qty_transfer": str(qty_t),
            "qty_purchase": str(self.procure),
            "source": _source_kind(qty_t, self.procure),
        }


def _source_kind(qty_transfer: Decimal, qty_purchase: Decimal) -> str:
    if qty_transfer > 0 and qty_purchase > 0:
        return SourcingDecision.Source.MIXED
    return SourcingDecision.Source.TRANSFER if qty_transfer > 0 else SourcingDecision.Source.PURCHASE


def _candidates(
    company_id: uuid.UUID, item_id: uuid.UUID, delivery: uuid.UUID, priority: list[str]
) -> list[tuple[Any, Decimal]]:
    """Source warehouses of the same company with free stock, in D-012 order."""
    free = {a.warehouse_id: a.available for a in _inventory().availability(company_id, item_id) if a.available > 0}
    rows = list(_model("Warehouse").objects.filter(company_id=company_id, id__in=list(free)).exclude(id=delivery))
    rank = {w: i for i, w in enumerate(priority)}
    rows.sort(key=lambda w: (rank.get(str(w.id), len(rank)), -free[w.id], w.code))
    return [(w, free[w.id]) for w in rows]


def plan(req: InternalRequisition) -> list[LinePlan]:
    settings = settings_for(req.tenant_id, req.company_id)
    out: list[LinePlan] = []
    used: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(Decimal)  # (item, warehouse) already planned
    for line in RequisitionLine.objects.filter(requisition=req, status=L.OPEN).order_by("line_no"):
        need = line.remaining
        if need <= 0:
            continue
        lp = LinePlan(line)
        for wh, free in _candidates(
            req.company_id, line.item_id, line.delivery_warehouse_id, settings.warehouse_priority
        ):
            left = free - used[(line.item_id, wh.id)]
            if need <= 0 or left <= 0:
                continue
            take = min(need, left)
            lp.transfers.append((wh, take))
            used[(line.item_id, wh.id)] += take
            need -= take
        lp.procure = need
        out.append(lp)
    return out


def _transfer_once(
    uow: UnitOfWork, req: InternalRequisition, wh: Any, to: uuid.UUID, lines: list[StockLine]
) -> uuid.UUID:
    with transaction.atomic():
        return _inventory().create_transfer(uow, req.company_id, doc_ref(req), wh.id, to, lines)


def _apply_transfer(
    uow: UnitOfWork, req: InternalRequisition, wh: Any, to: uuid.UUID, wanted: list[tuple[LinePlan, Decimal]]
) -> tuple[uuid.UUID | None, list[tuple[LinePlan, Decimal]]]:
    """Create one DRAFT transfer (with reservations) from ``wh``; after a lost race re-read availability and retry.

    P09 reserves under row locks and refuses more than the free quantity (``stock_insufficient``); the failed
    attempt is rolled back to its savepoint, and READ COMMITTED then shows the competitor's committed reservation.
    """
    for _attempt in range(MAX_SOURCING_RETRIES):
        lines = [
            StockLine(line_ref=str(lp.line.line_no), item_id=lp.line.item_id, warehouse_id=wh.id, quantity=qty)
            for lp, qty in wanted
            if qty > 0
        ]
        if not lines:
            return None, []
        try:
            return _transfer_once(uow, req, wh, to, lines), wanted
        except DomainError as exc:
            if exc.code != "stock_insufficient":
                raise
        budget: dict[uuid.UUID, Decimal] = {}
        regranted: list[tuple[LinePlan, Decimal]] = []
        for lp, qty in wanted:
            item = lp.line.item_id
            if item not in budget:
                free = {a.warehouse_id: a.available for a in _inventory().availability(req.company_id, item)}
                budget[item] = max(free.get(wh.id, ZERO), ZERO)
            take = min(qty, budget[item])
            budget[item] -= take
            regranted.append((lp, take))
        wanted = regranted
    return None, []


def source(
    uow: UnitOfWork, req: InternalRequisition, mode: str, *, by: str = SourcingDecision.CreatedBy.USER
) -> dict[str, Any]:
    """``:source`` — ``simulate`` returns the plan (nothing written); ``apply`` reserves + transfers + TO_PROCURE."""
    ctx = uow.ctx
    if by == SourcingDecision.CreatedBy.USER:
        c.require(ctx, "purchasing.internal_requisition.post", req)
    if mode not in ("simulate", "apply"):
        raise ValidationFailed("mode must be simulate or apply", field="mode")
    if req.status not in (S.APPROVED, S.IN_PROGRESS):
        raise RequisitionNotApproved(
            f"requisition {req.number} is {req.status}: only approved requisitions are sourced"
        )
    plans = plan(req)
    if mode == "simulate":
        return {"status": "SIMULATED", "lines": [p.as_json() for p in plans], "policy_ref": SOURCING_POLICY}
    now = clock.now()
    by_warehouse: dict[uuid.UUID, tuple[Any, list[tuple[LinePlan, Decimal]]]] = {}
    for p in plans:
        for wh, qty in p.transfers:
            by_warehouse.setdefault(wh.id, (wh, []))[1].append((p, qty))
    granted: dict[int, list[tuple[Any, Decimal, uuid.UUID]]] = defaultdict(list)
    for wh, wanted in sorted(by_warehouse.values(), key=lambda v: v[0].code):
        transfer_id, done = _apply_transfer(uow, req, wh, req.requesting_warehouse_id, wanted)
        for lp, qty in done:
            if transfer_id is not None and qty > 0:
                granted[lp.line.line_no].append((wh, qty, transfer_id))
    result_lines: list[dict[str, Any]] = []
    for p in plans:
        line = RequisitionLine.objects.select_for_update().get(pk=p.line.pk)
        moved = granted.get(line.line_no, [])
        qty_t = sum((q for _w, q, _t in moved), ZERO)
        procure = line.remaining - qty_t
        if qty_t + procure != line.remaining or procure < 0:
            raise SourcingOverallocation(f"line {line.line_no}: sourcing exceeds the requested quantity")
        line.qty_transfer += qty_t
        line.qty_procure += procure
        for wh, qty, tid in moved:
            number = _model("Transfer").objects.filter(id=tid).values_list("number", flat=True).first() or ""
            line.lineage.append(
                {"kind": "transfer", "id": str(tid), "number": number, "from_warehouse_id": str(wh.id), "qty": str(qty)}
            )
        line.status = L.TO_PROCURE if line.qty_procure > _consolidated_qty(line) else L.RESERVED
        line.save(update_fields=["qty_transfer", "qty_procure", "lineage", "status"])
        decision = SourcingDecision.objects.create(
            tenant_id=req.tenant_id,
            company_id=req.company_id,
            line=line,
            source=_source_kind(qty_t, procure),
            from_warehouse_id=moved[0][0].id if moved else None,
            qty_transfer=qty_t,
            qty_purchase=procure,
            transfer_ids=sorted({str(t) for _w, _q, t in moved}),
            reason="disponible en bodegas de la compañía; remanente a compras" if procure else "disponible",
            created_by_kind=by,
            policy_ref=SOURCING_POLICY,
            decided_by=ctx.principal_id,
            decided_at=now,
        )
        result_lines.append(
            {
                "line_no": line.line_no,
                "source": decision.source,
                "qty_transfer": str(qty_t),
                "qty_purchase": str(procure),
                "transfers": decision.transfer_ids,
                "status": line.status,
            }
        )
    if req.status == S.APPROVED:
        req.status = S.IN_PROGRESS
        req.save_versioned(ctx.principal_id, ["status"])
    c.audit(
        uow,
        "purchasing.internal_requisition.source",
        DOC_TYPE,
        req.id,
        req.company_id,
        {"policy_ref": SOURCING_POLICY, "by": by, "lines": result_lines},
    )
    _refresh_status(uow, req)
    return {"status": "APPLIED", "lines": result_lines, "policy_ref": SOURCING_POLICY, "created_by": by}


# --------------------------------------------------------------------------- consolidation (D-031) + D-011


def _consolidated_qty(line: RequisitionLine) -> Decimal:
    return sum((Decimal(e["qty"]) for e in line.lineage if e.get("kind") == "consolidation"), ZERO)


def _week(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


@dataclass
class Group:
    item_id: uuid.UUID
    presentation_id: uuid.UUID | None
    week: dt.date
    warehouse_id: uuid.UUID
    sources: list[tuple[RequisitionLine, Decimal]] = field(default_factory=list)

    @property
    def quantity(self) -> Decimal:
        return sum((q for _l, q in self.sources), ZERO)


def _groups(company_id: uuid.UUID) -> list[Group]:
    groups: dict[tuple[Any, ...], Group] = {}
    lines = (
        RequisitionLine.objects.select_for_update(of=("self",))
        .select_related("requisition")
        .filter(company_id=company_id, status=L.TO_PROCURE, requisition__status__in=[S.APPROVED, S.IN_PROGRESS])
        .order_by("needed_by", "requisition__number", "line_no")
    )
    for line in lines:
        pending = line.qty_procure - _consolidated_qty(line)
        if pending <= 0:
            continue
        key = (line.item_id, line.presentation_id, _week(line.needed_by), line.delivery_warehouse_id)
        g = groups.setdefault(key, Group(line.item_id, line.presentation_id, key[2], line.delivery_warehouse_id))
        g.sources.append((line, pending))
    return sorted(groups.values(), key=lambda g: (g.week, str(g.warehouse_id), str(g.item_id)))


def _procurement_plan(uow: UnitOfWork, company_id: uuid.UUID, g: Group, limit: Decimal) -> dict[str, Any]:
    price = purchasing_contract.valid_vendor_price(uow, company_id, g.item_id, clock.business_date())
    if price is None:
        return {"action": "BUYER_TASK", "reason_code": VENDOR_PRICE_MISSING, "estimated_total": None}
    unit_cop = round_half_up(price.unit_price * price.fx_rate, AMOUNT)
    total = round_half_up(unit_cop * g.quantity, AMOUNT)
    if total > limit:
        return {"action": "BUYER_TASK", "reason_code": AUTO_PO_LIMIT_EXCEEDED, "estimated_total": total, "price": price}
    return {"action": "DRAFT_PO", "estimated_total": total, "price": price}


def run_consolidation(uow: UnitOfWork, company_id: uuid.UUID, *, dry_run: bool) -> dict[str, Any]:
    """D-031 consolidation of APPROVED TO_PROCURE remainders, then D-011 drafts or buyer tasks."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    c.require(ctx, "purchasing.consolidation.create", {"company_id": company_id})
    limit = settings_for(ctx.tenant_id, company_id).auto_po_limit
    groups = _groups(company_id)
    plans = [(g, _procurement_plan(uow, company_id, g, limit)) for g in groups]
    preview = [
        {
            "item_id": str(g.item_id),
            "presentation_id": str(g.presentation_id) if g.presentation_id else None,
            "needed_week": g.week.isoformat(),
            "delivery_warehouse_id": str(g.warehouse_id),
            "quantity": str(g.quantity),
            "sources": [_source_json(line, q) for line, q in g.sources],
            "action": p["action"],
            "reason_code": p.get("reason_code"),
            "estimated_total": None if p.get("estimated_total") is None else str(p["estimated_total"]),
        }
        for g, p in plans
    ]
    warnings = sorted({p["reason_code"] for _g, p in plans if p.get("reason_code") == AUTO_PO_LIMIT_EXCEEDED})
    if dry_run or not plans:
        return {"batch": None, "lines": preview, "warnings": warnings, "dry_run": dry_run}
    now = clock.now()
    batch = ConsolidationBatch.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=c.next_number(uow, company_id, "consolidation", clock.business_date()),
        rule=CONSOLIDATION_RULE,
        run_at=now,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    drafts: dict[uuid.UUID, list[tuple[ConsolidationLine, Group, Any]]] = defaultdict(list)
    for n, (g, p) in enumerate(plans, start=1):
        cline = ConsolidationLine.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            batch=batch,
            line_no=n,
            item_id=g.item_id,
            presentation_id=g.presentation_id,
            needed_week=g.week,
            delivery_warehouse_id=g.warehouse_id,
            quantity=g.quantity,
            sources=[_source_json(line, q) for line, q in g.sources],
        )
        if sum((Decimal(s["qty"]) for s in cline.sources), ZERO) != cline.quantity:
            raise SourcingOverallocation("a consolidated line must equal the sum of its sources (D-031)")
        for line, qty in g.sources:
            line.lineage.append(
                {"kind": "consolidation", "id": str(batch.id), "number": batch.number, "line_no": n, "qty": str(qty)}
            )
            line.save(update_fields=["lineage"])
        if p["action"] == "DRAFT_PO":
            drafts[p["price"].supplier_id].append((cline, g, p["price"]))
        else:
            _buyer_task(uow, cline, p["reason_code"], p.get("estimated_total"), limit)
    for supplier_id, items in sorted(drafts.items(), key=lambda kv: str(kv[0])):
        _draft_order(uow, company_id, batch, supplier_id, items)
    c.audit(
        uow,
        "purchasing.consolidation.run",
        "purchasing.consolidation",
        batch.id,
        company_id,
        {"rule": CONSOLIDATION_RULE, "policy_ref": PROCUREMENT_POLICY, "lines": len(plans)},
    )
    return {"batch": batch, "lines": preview, "warnings": warnings, "dry_run": False}


def _source_json(line: RequisitionLine, qty: Decimal) -> dict[str, Any]:
    return {
        "line_id": str(line.id),
        "requisition_id": str(line.requisition_id),
        "requisition_number": line.requisition.number,
        "line_no": line.line_no,
        "needed_by": line.needed_by.isoformat(),
        "qty": str(qty),
    }


def _buyer_task(uow: UnitOfWork, cline: ConsolidationLine, reason: str, total: Decimal | None, limit: Decimal) -> None:
    text = (
        f"Total estimado {total} COP supera el límite de orden automática {limit} COP (D-011): revisar y comprar."
        if reason == AUTO_PO_LIMIT_EXCEEDED
        else "Sin precio vigente de proveedor (adjudicación aprobada ≤ 90 días, D-011): cotizar."
    )
    task = BuyerTask.objects.create(
        tenant_id=cline.tenant_id,
        company_id=cline.company_id,
        consolidation_line=cline,
        reason_code=reason,
        message=text,
        estimated_total=total,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    cline.status = ConsolidationLine.Status.BUYER_TASK
    cline.save(update_fields=["status"])
    _lineage_all(cline, {"kind": "buyer_task", "id": str(task.id), "reason_code": reason})


def _draft_order(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    batch: ConsolidationBatch,
    supplier_id: uuid.UUID,
    items: list[tuple[ConsolidationLine, Group, Any]],
) -> None:
    lines = []
    for n, (cline, g, price) in enumerate(items, start=1):
        lines.append(
            {
                "line_no": n,
                "item_id": str(g.item_id),
                "quantity": {"value": str(cline.quantity), "uom": price.uom},
                "unit_price": {"amount": str(price.unit_price), "currency": price.currency},
                "warehouse_id": str(g.warehouse_id),
            }
        )
    first_price = items[0][2]
    data: dict[str, Any] = {
        "party": {"id": str(supplier_id)},
        "currency": first_price.currency,
        "notes": f"Borrador automático de la consolidación {batch.number} (D-011); requiere aprobación.",
        "lines": lines,
    }
    if first_price.currency != "COP":
        data["fx_rate"] = str(first_price.fx_rate)
    order = purchasing_contract.draft_order(
        uow, company_id, DocumentRef("purchasing.consolidation", batch.id, batch.number), data
    )
    for n, (cline, _g, price) in enumerate(items, start=1):
        cline.status = ConsolidationLine.Status.IN_PURCHASE
        cline.purchase_order_id = order.id
        cline.purchase_order_line_no = n
        cline.unit_price = round_half_up(price.unit_price * price.fx_rate, AMOUNT)
        cline.supplier_id = supplier_id
        cline.save(update_fields=["status", "purchase_order_id", "purchase_order_line_no", "unit_price", "supplier_id"])
        _lineage_all(
            cline,
            {"kind": "purchase_order", "id": str(order.id), "number": order.number, "line_no": n},
            in_purchase=True,
        )


def _lineage_all(cline: ConsolidationLine, entry: Mapping[str, Any], *, in_purchase: bool = False) -> None:
    for src in cline.sources:
        line = RequisitionLine.objects.select_for_update().get(pk=uuid.UUID(src["line_id"]))
        line.lineage.append({**entry, "qty": src["qty"], "consolidation_line": cline.line_no})
        fields = ["lineage"]
        if in_purchase and line.qty_procure <= sum(
            (Decimal(e["qty"]) for e in line.lineage if e.get("kind") == "purchase_order"), ZERO
        ):
            line.status = L.IN_PURCHASE
            fields.append("status")
        line.save(update_fields=fields)


# --------------------------------------------------------------------------- fulfilment


def _line_done(line: RequisitionLine) -> bool:
    return line.fulfilled_qty >= line.base_qty - line.qty_cancelled


def sync_fulfilment(uow: UnitOfWork, req: InternalRequisition) -> InternalRequisition:
    """Received transfers (P09) and received PO quantities (P11a) fulfil the source lines; closes the requisition."""
    c.require(uow.ctx, "purchasing.internal_requisition.read", req)
    transfer_model = _model("Transfer")
    received = set(
        str(t)
        for t in transfer_model.objects.filter(
            company_id=req.company_id, source_type=DOC_TYPE, source_id=req.id, status="RECEIVED"
        ).values_list("id", flat=True)
    )
    po_received = _po_allocation(req)
    for line in RequisitionLine.objects.select_for_update().filter(requisition=req).order_by("line_no"):
        from_transfers = sum(
            (Decimal(e["qty"]) for e in line.lineage if e.get("kind") == "transfer" and e["id"] in received), ZERO
        )
        from_po = po_received.get(line.id, ZERO)
        total = min(from_transfers + from_po, line.base_qty - line.qty_cancelled)
        if total == line.fulfilled_qty:
            continue
        line.fulfilled_qty = total
        if _line_done(line):
            line.status = L.FULFILLED
        line.save(update_fields=["fulfilled_qty", "status"])
    _refresh_status(uow, req)
    return req


def _po_allocation(req: InternalRequisition) -> dict[uuid.UUID, Decimal]:
    """Received quantity of each consolidated PO line split over its sources in needed-by order."""
    order_line = apps.get_model("purchasing", "PurchaseOrderLine")
    out: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    clines = ConsolidationLine.objects.filter(
        company_id=req.company_id, purchase_order_id__isnull=False, sources__contains=[{"requisition_id": str(req.id)}]
    )
    for cl in clines:
        received = (
            order_line.objects.filter(order_id=cl.purchase_order_id, line_no=cl.purchase_order_line_no)
            .values_list("received_qty", flat=True)
            .first()
        )
        left = min(Decimal(received or 0), cl.quantity)
        for src in sorted(cl.sources, key=lambda s: (s["needed_by"], s["requisition_number"], s["line_no"])):
            take = min(left, Decimal(src["qty"]))
            left -= take
            if src["requisition_id"] == str(req.id):
                out[uuid.UUID(src["line_id"])] += take
        if cl.fulfilled_qty != min(Decimal(received or 0), cl.quantity):
            cl.fulfilled_qty = min(Decimal(received or 0), cl.quantity)
            if cl.fulfilled_qty == cl.quantity:
                cl.status = ConsolidationLine.Status.FULFILLED
            cl.save(update_fields=["fulfilled_qty", "status"])
    return out


def _refresh_status(uow: UnitOfWork, req: InternalRequisition) -> None:
    if req.status != S.IN_PROGRESS:
        return
    lines = list(RequisitionLine.objects.filter(requisition=req))
    if lines and all(_line_done(ln) for ln in lines):
        req.status = S.FULFILLED
        req.save_versioned(uow.ctx.principal_id, ["status"])
        c.audit(uow, "purchasing.internal_requisition.fulfilled", DOC_TYPE, req.id, req.company_id, {})


def get_requisition(company_id: uuid.UUID, pk: uuid.UUID) -> InternalRequisition:
    req = InternalRequisition.objects.filter(company_id=company_id, pk=pk).first()
    if req is None:
        raise NotFound("not found")
    return req
