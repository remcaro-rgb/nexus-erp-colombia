"""Stock issues by concept — salidas de inventario por concepto (F-INV-ISSUE).

States (contract ``InventoryIssue.status``): DRAFT → SUBMITTED (pending approval) → APPROVED → POSTED → REVERSED;
SUBMITTED → REJECTED; DRAFT/SUBMITTED/APPROVED/REJECTED → CANCELLED (reservations released).

* ``submit`` reserves the stock (bins/lots named on the line, else FEFO) under row lock and routes the document to
  the workflow (``WorkflowService.request_approval``, policy ``inventory.stock_issue``) when the concept requires
  approval or the estimated value reaches the concept's ``approval_threshold_value``; otherwise it is APPROVED.
* ``post`` — one transaction: stock moves out of the reserved bins (``StockService.issue``: cost from the
  valuation layers, FIFO or weighted average), valuation, the journal entry through ``PostingService``
  (Dr concept account role with the line dimensions / Cr ``INVENTORY``, branch of the warehouse) and the outbox
  event ``inventory.issue.posted.v1``. Any failure (stock, period, dimensions, accounts) rolls everything back.
* ``reverse`` — mirror journal entry (``PostingService.reverse``) and the stock returned to the original
  bin/lot at the original cost (``StockService.return_moves``); the posted issue itself is never edited.
* ``simulate`` runs exactly the ``post`` path inside a savepoint that is rolled back, so the preview (moves, cost,
  journal lines) and the validation errors are those of ``post``.

Validation (create, submit, post): concept ACTIVE (``issue_concept_inactive``), reason when the concept needs one
(``issue_reason_required``), dimensions the concept requires on every line (``dimension_required``), leaf bins
(``location_not_leaf``), lots for lot-tracked items in a named bin (``lot_required``). Negative stock is never
allowed for issues (D-032; ``stock_insufficient``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction

from inventory import masters
from inventory.common import audit
from inventory.errors import error
from inventory.issues.models import IssueConcept, StockIssue, StockIssueLine
from inventory.models import Item, ItemValuation, Reservation, StockMove
from inventory.stock.service import SERVICE as STOCK
from inventory.valuation import core as val
from kernel import clock, sequences, services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine
from kernel.interfaces.workflow import WorkflowService
from kernel.models import Sequence
from kernel.money import q_amount
from kernel.uow import UnitOfWork

SOURCE_TYPE = "inventory.stock_issue"
REVERSAL_SOURCE_TYPE = "inventory.stock_issue.reversal"
SEQUENCE_CODE = "SAL"
SEQUENCE_PREFIX = "SAL-"
APPROVAL_POLICY = "inventory.stock_issue"
INVENTORY_ROLE = "INVENTORY"
ZERO = Decimal(0)
S = StockIssue.IssueStatus
K = IssueConcept.Kind

#: dimension key (kernel.dimensions) → column / DimensionValues slot
DIM_FIELDS: dict[str, str] = {
    "cost_center": "cost_center_id",
    "business_unit": "business_unit_id",
    "aux_abierto": "aux_abierto_id",
    "item_contable": "item_dimension_id",
}

#: seeded concepts (FEATURES-ADDENDUM §7): code, name, kind, debit account role, cost center, extra dims, reason
SEEDED: tuple[dict[str, Any], ...] = (
    {"code": "CONSUMO", "name": "Consumo interno a centro de costo", "kind": K.CONSUMPTION,
     "account_role": "INVENTORY_CONSUMPTION_EXPENSE", "requires_dimension": True},
    {"code": "CONSUMO_DIRECTO", "name": "Consumo directo a proyecto u obra", "kind": K.DIRECT_CONSUMPTION,
     "account_role": "INVENTORY_DIRECT_CONSUMPTION", "requires_dimension": True, "required_dims": ["item_contable"]},
    {"code": "BAJA", "name": "Baja de inventario (deterioro, vencimiento, pérdida)", "kind": K.SCRAP,
     "account_role": "INVENTORY_SCRAP_EXPENSE", "requires_reason": True,
     "reason_codes": ["VENCIDO", "DANADO", "OBSOLETO", "PERDIDA", "OTRO"]},
    {"code": "MUESTRA", "name": "Muestras comerciales", "kind": K.SAMPLE, "account_role": "INVENTORY_SAMPLES_EXPENSE"},
    {"code": "OTRO", "name": "Otras salidas", "kind": K.OTHER, "account_role": "INVENTORY_OTHER_ISSUE_EXPENSE",
     "requires_reason": True},
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class IssueLineInput:
    item_id: uuid.UUID
    quantity: Decimal | None = None
    presentation_id: uuid.UUID | None = None
    qty_packages: Decimal | None = None
    location_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    description: str = ""
    dimensions: dict[str, uuid.UUID | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IssueInput:
    concept_id: uuid.UUID
    warehouse_id: uuid.UUID
    document_date: dt.date
    lines: tuple[IssueLineInput, ...]
    posting_date: dt.date | None = None
    reason: str | None = None
    notes: str = ""
    dimensions: dict[str, uuid.UUID | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Preview:
    """Result of ``simulate`` / ``post``: per-line cost and the journal lines."""

    total: Decimal
    lines: list[dict[str, Any]]
    journal: list[dict[str, Any]]


class _Rollback(Exception):
    def __init__(self, preview: Preview) -> None:
        super().__init__("simulate rollback")
        self.preview = preview


# =========================================================================== concepts


def _concept_values(data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, limit in (("code", 40), ("name", 200), ("account_role", 60)):
        if key in data or not partial:
            v = data.get(key)
            if not isinstance(v, str) or not v.strip() or len(v.strip()) > limit:
                raise ValidationFailed(
                    f"{key} is required (1–{limit} characters)", pointer=f"/{key}", reason="required"
                )
            out[key] = v.strip().upper() if key in ("code", "account_role") else v.strip()
    for key in ("requires_dimension", "requires_reason", "requires_approval"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ValidationFailed(f"{key} must be a boolean", pointer=f"/{key}", reason="type")
            out[key] = data[key]
    if "kind" in data:
        if data["kind"] not in K.values:
            raise ValidationFailed(f"kind must be one of {', '.join(K.values)}", pointer="/kind", reason="enum")
        out["kind"] = data["kind"]
    if "required_dims" in data:
        dims = data["required_dims"]
        if not isinstance(dims, list) or any(d not in DIM_FIELDS for d in dims):
            raise ValidationFailed(f"required_dims ⊂ {', '.join(DIM_FIELDS)}", pointer="/required_dims", reason="enum")
        out["required_dims"] = sorted(set(dims))
    if "reason_codes" in data:
        codes = data["reason_codes"]
        if not isinstance(codes, list) or any(not isinstance(c, str) or not c.strip() for c in codes):
            raise ValidationFailed("reason_codes is a list of codes", pointer="/reason_codes", reason="type")
        out["reason_codes"] = [c.strip().upper() for c in codes]
    if "approval_threshold_value" in data:
        raw = data["approval_threshold_value"]
        if raw in (None, ""):
            out["approval_threshold_value"] = None
        else:
            amount = raw.get("amount") if isinstance(raw, dict) else raw
            if isinstance(amount, (float, bool)) or amount is None:
                raise ValidationFailed(
                    "approval_threshold_value is a decimal string", pointer="/approval_threshold_value"
                )
            try:
                value = q_amount(Decimal(str(amount)))
            except ArithmeticError:
                raise ValidationFailed(
                    "approval_threshold_value is a decimal string", pointer="/approval_threshold_value"
                ) from None
            if value < 0:
                raise ValidationFailed(
                    "approval_threshold_value ≥ 0", pointer="/approval_threshold_value", reason="range"
                )
            out["approval_threshold_value"] = value
    if "allow_negative" in data and data["allow_negative"] is not False:
        raise ValidationFailed("issues never allow negative stock (D-032)", pointer="/allow_negative", reason="const")
    return out


def create_concept(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> IssueConcept:
    uow.ctx.require_company(company_id)
    values = _concept_values(data, partial=False)
    if IssueConcept.objects.filter(company_id=company_id, code=values["code"]).exists():
        raise Conflict(f"issue concept {values['code']} already exists", code="conflict")
    c = IssueConcept.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
        **values,
    )
    audit(uow, "create", "inventory.issue_concept", c, {"code": c.code})
    return c


def update_concept(uow: UnitOfWork, c: IssueConcept, data: dict[str, Any]) -> IssueConcept:
    values = _concept_values(data, partial=True)
    if not values:
        raise ValidationFailed("nothing to update", pointer="/", reason="min_properties")
    if (
        "code" in values
        and IssueConcept.objects.filter(company_id=c.company_id, code=values["code"]).exclude(id=c.id).exists()
    ):
        raise Conflict(f"issue concept {values['code']} already exists", code="conflict")
    for k, v in values.items():
        setattr(c, k, v)
    c.save_versioned(uow.ctx.principal_id, list(values))
    c.refresh_from_db()
    audit(uow, "update", "inventory.issue_concept", c, {"fields": sorted(values)})
    return c


def archive_concept(uow: UnitOfWork, c: IssueConcept) -> IssueConcept:
    """DELETE archives (posted issues keep referencing it); new issues with it fail ``issue_concept_inactive``."""
    c.status = "ARCHIVED"
    c.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "archive", "inventory.issue_concept", c)
    return c


def seed_concepts(uow: UnitOfWork, company_id: uuid.UUID) -> list[IssueConcept]:
    """Seeded concepts CONSUMO, CONSUMO_DIRECTO, BAJA, MUESTRA, OTRO (idempotent; existing codes are kept)."""
    out = []
    for spec in SEEDED:
        existing = IssueConcept.objects.filter(company_id=company_id, code=spec["code"]).first()
        out.append(existing or create_concept(uow, company_id, dict(spec)))
    return out


# =========================================================================== validation helpers


def _dims_of(obj: Any) -> dict[str, uuid.UUID | None]:
    return {k: getattr(obj, col) for k, col in DIM_FIELDS.items()}


def effective_dims(issue: StockIssue, line: StockIssueLine) -> dict[str, uuid.UUID | None]:
    header, own = _dims_of(issue), _dims_of(line)
    return {k: own[k] or header[k] for k in DIM_FIELDS}


def _require_concept(concept: IssueConcept) -> None:
    if concept.status != "ACTIVE":
        raise error("issue_concept_inactive", f"issue concept {concept.code} is inactive")


def _require_reason(concept: IssueConcept, reason: str | None) -> None:
    if not concept.requires_reason:
        return
    text = (reason or "").strip()
    if not text:
        raise error("issue_reason_required", f"concept {concept.code} requires a reason", pointer="/reason")
    if concept.reason_codes:
        head = text.replace(":", " ").split()[0].upper()
        if head not in concept.reason_codes:
            raise error(
                "issue_reason_required",
                f"concept {concept.code}: the reason starts with one of {', '.join(concept.reason_codes)}",
                pointer="/reason",
            )


def _required_dims(concept: IssueConcept) -> list[str]:
    keys = ["cost_center"] if concept.requires_dimension else []
    return keys + [d for d in concept.required_dims if d not in keys]


def _check_dims(
    company_id: uuid.UUID, concept: IssueConcept, line_dims: list[dict[str, uuid.UUID | None]], day: dt.date
) -> None:
    required = _required_dims(concept)
    dims_svc = services.optional(Dimensions)
    for n, dims in enumerate(line_dims):
        for key in required:
            if dims.get(key) is None:
                raise error(
                    "dimension_required",
                    f"concept {concept.code} requires {key} on every line (line {n + 1})",
                    dimension=key,
                    pointer=f"/lines/{n}/dimensions/{DIM_FIELDS[key]}",
                )
        if dims_svc is not None:
            for key, node in dims.items():
                if node is not None:
                    dims_svc.assert_postable(company_id, key, node, day)


def _number(uow: UnitOfWork, company_id: uuid.UUID, day: dt.date) -> str:
    Sequence.objects.bulk_create(
        [
            Sequence(
                tenant_id=uow.ctx.tenant_id,
                company_id=company_id,
                sequence_code=SEQUENCE_CODE,
                prefix=SEQUENCE_PREFIX,
                padding=6,
            )
        ],
        ignore_conflicts=True,
    )
    return sequences.allocate(company_id, SEQUENCE_CODE, SEQUENCE_PREFIX, on_date=day).formatted


def _clean_dims(raw: dict[str, uuid.UUID | None], pointer: str) -> dict[str, uuid.UUID | None]:
    unknown = sorted(set(raw) - set(DIM_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown dimension {unknown[0]}", pointer=f"{pointer}/{unknown[0]}", reason="enum")
    return {k: raw.get(k) for k in DIM_FIELDS}


def _write_lines(uow: UnitOfWork, issue: StockIssue, lines: tuple[IssueLineInput, ...]) -> list[StockIssueLine]:
    if not lines:
        raise ValidationFailed("at least one line is required", pointer="/lines", reason="required")
    if len(lines) > 500:
        raise ValidationFailed("at most 500 lines", pointer="/lines", reason="range")
    out: list[StockIssueLine] = []
    for n, ln in enumerate(lines, start=1):
        ptr = f"/lines/{n - 1}"
        item = Item.objects.select_related("base_uom").filter(company_id=issue.company_id, id=ln.item_id).first()
        if item is None:
            raise ValidationFailed("unknown item", pointer=f"{ptr}/item_id", reason="not_found")
        if item.item_type not in (Item.ItemType.STOCKABLE, Item.ItemType.CONSUMABLE):
            raise ValidationFailed("only stockable/consumable items can be issued", pointer=f"{ptr}/item_id")
        presentation = STOCK._presentation(item, ln.presentation_id)
        if ln.quantity is None and ln.qty_packages is None:
            raise ValidationFailed("quantity is required", pointer=f"{ptr}/quantity", reason="required")
        qty = masters.quantity_in_base(item, presentation, ln.quantity or ZERO, ln.qty_packages)
        if qty <= 0:
            raise ValidationFailed("quantity must be positive", pointer=f"{ptr}/quantity", reason="positive")
        packages = STOCK._packages(item, presentation, qty)
        loc = STOCK._location(issue.company_id, ln.location_id, issue.warehouse_id) if ln.location_id else None
        lot = STOCK._lot(item, ln.lot_id) if ln.lot_id else None
        if loc is not None and lot is None and item.tracking == Item.Tracking.LOT:
            raise error("lot_required", f"item {item.code} is lot-tracked: name the lot of bin {loc.code}")
        dims = _clean_dims(ln.dimensions, f"{ptr}/dimensions")
        out.append(
            StockIssueLine.objects.create(
                tenant_id=uow.ctx.tenant_id,
                company_id=issue.company_id,
                issue=issue,
                line_no=n,
                item=item,
                description=ln.description[:255],
                presentation=presentation,
                qty_packages=packages,
                quantity=qty,
                uom=item.base_uom.code,
                location=loc,
                lot=lot,
                **{DIM_FIELDS[k]: v for k, v in dims.items()},
            )
        )
    return out


def _lines(issue: StockIssue) -> list[StockIssueLine]:
    return list(
        StockIssueLine.objects.filter(issue_id=issue.id)
        .select_related("item__base_uom", "location", "lot")
        .order_by("line_no")
    )


def _validate(issue: StockIssue, day: dt.date | None = None) -> None:
    concept = IssueConcept.objects.get(id=issue.concept_id)
    _require_concept(concept)
    _require_reason(concept, issue.reason)
    _check_dims(
        issue.company_id, concept, [effective_dims(issue, ln) for ln in _lines(issue)], day or issue.document_date
    )


def _doc(issue: StockIssue) -> DocumentRef:
    return DocumentRef(SOURCE_TYPE, issue.id, issue.number or None)


# =========================================================================== document lifecycle


def create(uow: UnitOfWork, company_id: uuid.UUID, data: IssueInput) -> StockIssue:
    uow.ctx.require_company(company_id)
    concept = IssueConcept.objects.filter(company_id=company_id, id=data.concept_id).first()
    if concept is None:
        raise ValidationFailed("unknown issue concept", pointer="/concept_id", reason="not_found")
    _require_concept(concept)
    wh = STOCK._warehouse(company_id, data.warehouse_id)
    dims = _clean_dims(data.dimensions, "/dimensions")
    issue = StockIssue.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        number=_number(uow, company_id, data.document_date),
        document_date=data.document_date,
        posting_date=data.posting_date,
        branch_id=wh.branch_id,
        warehouse=wh,
        concept=concept,
        requested_by=uow.ctx.principal_id,
        reason=(data.reason or "").strip() or None,
        notes=data.notes[:10000],
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
        **{DIM_FIELDS[k]: v for k, v in dims.items()},
    )
    _write_lines(uow, issue, data.lines)
    _validate(issue)
    audit(uow, "create", "inventory.issue", issue, {"number": issue.number, "concept": concept.code})
    return issue


def update(
    uow: UnitOfWork, issue: StockIssue, changes: dict[str, Any], lines: tuple[IssueLineInput, ...] | None
) -> StockIssue:
    """PATCH a DRAFT (header fields and/or the full line set)."""
    issue = _lock(issue)
    _require(issue, S.DRAFT)
    fields: list[str] = []
    if "concept_id" in changes:
        concept = IssueConcept.objects.filter(company_id=issue.company_id, id=changes["concept_id"]).first()
        if concept is None:
            raise ValidationFailed("unknown issue concept", pointer="/concept_id", reason="not_found")
        _require_concept(concept)
        issue.concept = concept
        fields.append("concept")
    if "warehouse_id" in changes:
        wh = STOCK._warehouse(issue.company_id, changes["warehouse_id"])
        issue.warehouse, issue.branch_id = wh, wh.branch_id
        fields += ["warehouse", "branch_id"]
    for key in ("document_date", "posting_date", "notes"):
        if key in changes:
            setattr(issue, key, changes[key])
            fields.append(key)
    if "reason" in changes:
        issue.reason = (changes["reason"] or "").strip() or None
        fields.append("reason")
    if "dimensions" in changes:
        for k, v in _clean_dims(changes["dimensions"], "/dimensions").items():
            setattr(issue, DIM_FIELDS[k], v)
            fields.append(DIM_FIELDS[k])
    if fields:
        issue.save_versioned(uow.ctx.principal_id, fields)
    if lines is not None:
        StockIssueLine.objects.filter(issue_id=issue.id).delete()
        _write_lines(uow, issue, lines)
        if not fields:
            issue.save_versioned(uow.ctx.principal_id, [])
    issue.refresh_from_db()
    _validate(issue)
    audit(uow, "update", "inventory.issue", issue, {"fields": fields, "lines": lines is not None})
    return issue


def _lock(issue: StockIssue) -> StockIssue:
    locked = StockIssue.objects.select_for_update().filter(id=issue.id).first()
    if locked is None:
        raise NotFound("stock issue not found")
    return locked


def _require(issue: StockIssue, *allowed: str) -> None:
    if issue.status not in allowed:
        raise Conflict(
            f"stock issue is {issue.status}; expected {' or '.join(allowed)}", code="invalid_state_transition"
        )


def _reservations(issue: StockIssue) -> list[Reservation]:
    return list(
        Reservation.objects.filter(
            company_id=issue.company_id, source_type=SOURCE_TYPE, source_id=issue.id, state=Reservation.State.ACTIVE
        ).order_by("line_ref", "id")
    )


def _reserve(uow: UnitOfWork, issue: StockIssue) -> None:
    lines = [
        StockLine(
            line_ref=f"L{ln.line_no}",
            item_id=ln.item_id,
            warehouse_id=issue.warehouse_id,
            quantity=ln.quantity,
            location_id=ln.location_id,
            lot_id=ln.lot_id,
            presentation_id=ln.presentation_id,
        )
        for ln in _lines(issue)
    ]
    STOCK.reserve(uow, issue.company_id, _doc(issue), lines)


def estimate(issue: StockIssue) -> Decimal:
    """Value estimate for the approval threshold: Σ qty × current average cost (the exact cost is set at post)."""
    total = ZERO
    for ln in _lines(issue):
        v = ItemValuation.objects.filter(company_id=issue.company_id, item_id=ln.item_id).first()
        total += ln.quantity * (v.avg_cost if v is not None else ZERO)
    return q_amount(total)


def needs_approval(issue: StockIssue) -> tuple[bool, Decimal]:
    concept = IssueConcept.objects.get(id=issue.concept_id)
    value = estimate(issue)
    threshold = concept.approval_threshold_value
    return concept.requires_approval or (threshold is not None and value >= threshold), value


def submit(uow: UnitOfWork, issue: StockIssue) -> StockIssue:
    """Reserve the stock and route to approval when the concept / threshold requires it (else APPROVED)."""
    issue = _lock(issue)
    _require(issue, S.DRAFT)
    _validate(issue)
    _reserve(uow, issue)
    required, value = needs_approval(issue)
    fields = ["status"]
    if required:
        concept = IssueConcept.objects.get(id=issue.concept_id)
        approval = services.get(WorkflowService).request_approval(
            uow,
            issue.company_id,
            _doc(issue),
            APPROVAL_POLICY,
            f"Salida {issue.number} concepto {concept.code} por {value} COP",
        )
        issue.approval_id = approval.id
        issue.status = S.SUBMITTED if approval.state != "APPROVED" else S.APPROVED
        fields.append("approval_id")
    else:
        issue.status = S.APPROVED
    issue.save_versioned(uow.ctx.principal_id, fields)
    issue.refresh_from_db()
    audit(uow, "submit", "inventory.issue", issue, {"approval": bool(required), "estimate": format(value, "f")})
    return issue


def approve(uow: UnitOfWork, issue: StockIssue, approve_it: bool = True, reason: str = "") -> StockIssue:
    """Decide the pending workflow approval (SoD enforced by the workflow); REJECTED releases reservations."""
    issue = _lock(issue)
    _require(issue, S.SUBMITTED)
    if issue.approval_id is None:
        raise Conflict("the stock issue has no pending approval", code="invalid_state_transition")
    decided = services.get(WorkflowService).decide(uow, issue.approval_id, approve_it, reason)
    if decided.state == "APPROVED":
        issue.status = S.APPROVED
    elif decided.state in ("REJECTED", "CANCELLED"):
        STOCK.release(uow, [r.id for r in _reservations(issue)])
        issue.status = S.REJECTED
    if issue.status != S.SUBMITTED:
        issue.save_versioned(uow.ctx.principal_id, ["status"])
        issue.refresh_from_db()
    audit(uow, "approve" if approve_it else "reject", "inventory.issue", issue, {"workflow_state": decided.state})
    return issue


def _sync_approval(issue: StockIssue) -> None:
    if (
        issue.status == S.SUBMITTED
        and issue.approval_id is not None
        and services.get(WorkflowService).state(issue.approval_id).state == "APPROVED"
    ):
        issue.status = S.APPROVED
        return
    if issue.status == S.SUBMITTED:
        raise Conflict(
            "the stock issue needs approval before posting",
            code="approval_required",
            approval_id=str(issue.approval_id) if issue.approval_id else None,
        )


def _line_no(line_ref: str) -> int:
    return int(line_ref.split("#", 1)[0].removeprefix("L"))


def _post_core(uow: UnitOfWork, issue: StockIssue, day: dt.date) -> Preview:
    """Stock + valuation + GL of an APPROVED issue (its reservations are consumed)."""
    concept = IssueConcept.objects.get(id=issue.concept_id)
    _require_concept(concept)
    _require_reason(concept, issue.reason)
    lines = _lines(issue)
    _check_dims(issue.company_id, concept, [effective_dims(issue, ln) for ln in lines], day)
    reservations = _reservations(issue)
    covered = {_line_no(r.line_ref) for r in reservations}
    if covered != {ln.line_no for ln in lines}:
        raise error("reservation_conflict", "every line needs its active reservation (submit the issue again)")
    stock_lines = [
        StockLine(
            line_ref=r.line_ref,
            item_id=r.item_id,
            warehouse_id=issue.warehouse_id,
            quantity=r.quantity,
            location_id=r.location_id,
            lot_id=r.lot_id,
            presentation_id=r.presentation_id,
        )
        for r in reservations
    ]
    result = STOCK.issue(uow, issue.company_id, _doc(issue), stock_lines, day)
    moves = list(StockMove.objects.filter(id__in=result.move_ids).order_by("line_ref", "id"))
    by_line: dict[int, list[StockMove]] = defaultdict(list)
    for m in moves:
        by_line[_line_no(m.line_ref)].append(m)
    posting: list[PostingLine] = []
    preview_lines: list[dict[str, Any]] = []
    total = ZERO
    for ln in lines:
        value = q_amount(sum((abs(m.value) for m in by_line[ln.line_no]), ZERO))
        ln.value, ln.unit_cost = value, val.average_of(ln.quantity, value)
        ln.save(update_fields=["value", "unit_cost"])
        total += value
        dims = effective_dims(issue, ln)
        dv = DimensionValues(**{DIM_FIELDS[k]: v for k, v in dims.items()})
        memo = f"Salida {issue.number} {concept.code} {ln.item.code}"
        if value > 0:
            posting.append(
                PostingLine(concept.account_role, "DEBIT", value, item_id=ln.item_id, warehouse_id=issue.warehouse_id,
                            dimensions=dv, memo=memo)
            )  # fmt: skip
            posting.append(
                PostingLine(INVENTORY_ROLE, "CREDIT", value, item_id=ln.item_id, warehouse_id=issue.warehouse_id,
                            memo=memo)
            )  # fmt: skip
        preview_lines.append(
            {
                "line_no": ln.line_no,
                "item_id": str(ln.item_id),
                "quantity": {"value": format(ln.quantity, "f"), "uom": ln.uom},
                "unit_cost": format(ln.unit_cost, "f"),
                "value": {"amount": format(value, "f"), "currency": "COP"},
                "moves": [
                    {"location_id": str(m.from_location_id), "lot_id": str(m.lot_id) if m.lot_id else None,
                     "quantity": format(m.quantity, "f"), "unit_cost": format(m.unit_cost, "f"),
                     "value": format(abs(m.value), "f")}
                    for m in by_line[ln.line_no]
                ],
            }
        )  # fmt: skip
    total = q_amount(total)
    entry_id: uuid.UUID | None = None
    journal: list[dict[str, Any]] = []
    if posting:
        res = services.get(PostingService).post(
            uow,
            PostingRequest(
                company_id=issue.company_id,
                posting_date=day,
                source=_doc(issue),
                purpose="post",
                lines=tuple(posting),
                journal_code="INVENTORY",
                branch_id=issue.branch_id,
            ),
        )
        entry_id = res.entry_id
        journal = _journal_lines(entry_id)
    issue.total, issue.journal_entry_id = total, entry_id
    return Preview(total, preview_lines, journal)


def _journal_lines(entry_id: uuid.UUID) -> list[dict[str, Any]]:
    from ledger.models import JournalLine

    rows = (
        JournalLine.objects.filter(entry_id=entry_id, entry__book__code="NIIF")
        .select_related("account")
        .order_by("debit", "id")
    )
    if not rows.exists():
        rows = JournalLine.objects.filter(entry_id=entry_id).select_related("account").order_by("debit", "id")
    return [
        {
            "account_code": r.account.code,
            "account_name": r.account.name,
            "debit": format(r.debit, "f"),
            "credit": format(r.credit, "f"),
            "cost_center_id": str(r.cost_center_id) if getattr(r, "cost_center_id", None) else None,
            "item_dimension_id": str(r.item_dimension_id) if getattr(r, "item_dimension_id", None) else None,
            "branch_id": str(r.branch_id) if getattr(r, "branch_id", None) else None,
        }
        for r in rows
    ]


def post(uow: UnitOfWork, issue: StockIssue, day: dt.date | None = None) -> StockIssue:
    issue = _lock(issue)
    if issue.status == S.DRAFT:
        required, _value = needs_approval(issue)
        if required:
            raise Conflict("the stock issue needs approval: submit it first", code="approval_required")
        issue = submit(uow, issue)
        issue = _lock(issue)
    _sync_approval(issue)
    _require(issue, S.APPROVED)
    day = day or issue.posting_date or clock.business_date()
    preview = _post_core(uow, issue, day)
    issue.status, issue.posting_date, issue.posted_at = S.POSTED, day, clock.now()
    issue.save_versioned(uow.ctx.principal_id, ["status", "posting_date", "posted_at", "total", "journal_entry_id"])
    issue.refresh_from_db()
    if issue.journal_entry_id is not None:
        uow.emit(
            "inventory.issue.posted.v1",
            {
                "issue_id": str(issue.id),
                "number": issue.number,
                "concept_id": str(issue.concept_id),
                "total": {"amount": format(preview.total, "f"), "currency": "COP"},
                "journal_entry_id": str(issue.journal_entry_id),
            },
            company_id=issue.company_id,
        )
    audit(uow, "post", "inventory.issue", issue, {"total": format(preview.total, "f")})
    return issue


def simulate(uow: UnitOfWork, issue: StockIssue, day: dt.date | None = None) -> Preview:
    """Exactly the ``post`` path (reservation of a draft included) inside a savepoint that is rolled back."""
    _require(issue, S.DRAFT, S.SUBMITTED, S.APPROVED)
    mark = len(uow.events)
    try:
        with transaction.atomic(using=uow.using):
            locked = _lock(issue)
            if locked.status == S.DRAFT:
                _validate(locked)
                _reserve(uow, locked)
            preview = _post_core(uow, locked, day or locked.posting_date or clock.business_date())
            raise _Rollback(preview)
    except _Rollback as rb:
        return rb.preview
    finally:
        del uow.events[mark:]


def reverse(uow: UnitOfWork, issue: StockIssue, reason: str, day: dt.date | None = None) -> StockIssue:
    """Mirror entry + stock back to the original bin/lot at the original cost; the issue becomes REVERSED."""
    issue = _lock(issue)
    _require(issue, S.POSTED)
    if not (reason or "").strip():
        raise ValidationFailed("a reversal needs a reason", code="reason_required", pointer="/reason")
    day = day or clock.business_date()
    STOCK.return_moves(
        uow, issue.company_id, DocumentRef(REVERSAL_SOURCE_TYPE, issue.id, issue.number or None), _doc(issue)
    )
    reversal_id = None
    if issue.journal_entry_id is not None:
        reversal_id = services.get(PostingService).reverse(uow, issue.journal_entry_id, day, reason.strip()).entry_id
    issue.status, issue.reversal_entry_id = S.REVERSED, reversal_id
    issue.reversal_reason, issue.reversed_at = reason.strip()[:2000], clock.now()
    issue.save_versioned(uow.ctx.principal_id, ["status", "reversal_entry_id", "reversal_reason", "reversed_at"])
    issue.refresh_from_db()
    audit(uow, "reverse", "inventory.issue", issue, {"reason": issue.reversal_reason})
    return issue


def cancel(uow: UnitOfWork, issue: StockIssue) -> StockIssue:
    issue = _lock(issue)
    _require(issue, S.DRAFT, S.SUBMITTED, S.APPROVED, S.REJECTED)
    STOCK.release(uow, [r.id for r in _reservations(issue)])
    if issue.status == S.SUBMITTED and issue.approval_id is not None:
        # optional capability of the P07 implementation (not part of the frozen Protocol)
        cancel_approval = getattr(services.get(WorkflowService), "cancel", None)
        if callable(cancel_approval):
            cancel_approval(uow, issue.approval_id, "salida anulada")
    issue.status = S.CANCELLED
    issue.save_versioned(uow.ctx.principal_id, ["status"])
    issue.refresh_from_db()
    audit(uow, "cancel", "inventory.issue", issue)
    return issue
