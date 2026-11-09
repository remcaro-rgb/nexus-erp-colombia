"""Branch services (F-ACC-BRANCH). Every write runs inside the caller's unit of work.

Rules: code unique per company (case-insensitive) — same code allowed in another
company; exactly one ACTIVE head office per company with active branches
(``branch_head_office_required``); ``company_id`` immutable; ACTIVE → ARCHIVED only
when no ACTIVE warehouse/POS references it (``branch_in_use`` with the dependency
list). Modules that reference branches register a :class:`BranchReference`
(P09 warehouses, P12 POS configs) and add the composite FK with
:func:`iam.branches.db.branch_fk`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.db.models.functions import Lower

from iam.errors import BranchCodeDuplicate, BranchCrossCompany, BranchHeadOfficeRequired, BranchInUse
from iam.models import Branch, Company
from kernel import clock, services
from kernel.db import _ident
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.dimensions import Dimensions
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

EDITABLE = (
    "code",
    "name",
    "is_head_office",
    "address",
    "municipality_code",
    "phone",
    "default_business_unit_id",
    "default_warehouse_id",
    "valid_from",
    "valid_to",
)
_PHONE = re.compile(r"^[0-9+() -]{5,25}$")
_DANE = re.compile(r"^[0-9]{5}$")


@dataclass(frozen=True, slots=True)
class BranchReference:
    """A table whose rows point to a branch (``tenant_id``, ``company_id``, ``branch_column``)."""

    name: str
    table: str
    branch_column: str = "branch_id"
    active_sql: str = "TRUE"
    label_column: str = "code"

    def __post_init__(self) -> None:
        _ident(self.table)
        _ident(self.branch_column)
        _ident(self.label_column)


_REFERENCES: dict[str, BranchReference] = {}
WAREHOUSE_REFERENCE = "inventory.warehouse"
POS_REFERENCE = "pos.config"


def register_reference(ref: BranchReference) -> None:
    _REFERENCES[ref.name] = ref


def unregister_reference(name: str) -> None:
    _REFERENCES.pop(name, None)


def active_references(branch: Branch) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ref in sorted(_REFERENCES.values(), key=lambda r: r.name):
        sql = (
            f"SELECT id, {ref.label_column} FROM {ref.table} "  # noqa: S608 - identifiers validated by _ident
            f"WHERE tenant_id = %s AND company_id = %s AND {ref.branch_column} = %s AND ({ref.active_sql}) "
            "ORDER BY 2 LIMIT 50"
        )
        with connection.cursor() as cur:
            cur.execute(sql, [str(branch.tenant_id), str(branch.company_id), str(branch.id)])
            out.extend({"type": ref.name, "id": str(r[0]), "label": str(r[1])} for r in cur.fetchall())
    return out


def _referenced_company(ref_name: str, tenant_id: uuid.UUID, row_id: uuid.UUID) -> uuid.UUID:
    ref = _REFERENCES.get(ref_name)
    if ref is None:
        raise ValidationFailed(f"{ref_name} is not available yet", code="validation_failed")
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT company_id FROM {ref.table} WHERE tenant_id = %s AND id = %s",  # noqa: S608 - validated identifier
            [str(tenant_id), str(row_id)],
        )
        row = cur.fetchone()
    if row is None:
        raise ValidationFailed(f"unknown {ref_name}")
    return uuid.UUID(str(row[0]))


# ------------------------------------------------------------------------ validation helpers


def validate_branch_link(ctx: TenantContext, company_id: uuid.UUID, branch_id: uuid.UUID) -> Branch:
    """For P09/P12 APIs: the branch must belong to ``company_id`` (``branch_cross_company``)."""
    branch = Branch.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=branch_id).first()
    if branch is None:
        raise BranchCrossCompany("the branch does not belong to this company")
    if branch.status != Branch.Status.ACTIVE:
        raise ValidationFailed("the branch is archived", code="BRANCH_ARCHIVED")
    return branch


def assert_postable_branch(ctx: TenantContext, company_id: uuid.UUID, branch_id: uuid.UUID) -> None:
    """For posting services: refuse archived or other-company branches (F-ACC-BRANCH posting)."""
    branch = Branch.objects.filter(tenant_id=ctx.tenant_id, id=branch_id).first()
    if branch is None or branch.company_id != company_id:
        raise ValidationFailed("branch of another company", code="BRANCH_COMPANY_MISMATCH")
    if branch.status != Branch.Status.ACTIVE:
        raise ValidationFailed("archived branch", code="BRANCH_ARCHIVED")


def _clean(data: Mapping[str, Any], company_id: uuid.UUID, ctx: TenantContext) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("code", "name"):
            if not isinstance(value, str) or not value.strip():
                raise ValidationFailed(f"{key} is required", field=key)
            limit = 40 if key == "code" else 200
            if len(value.strip()) > limit:
                raise ValidationFailed(f"{key} too long", field=key)
            out[key] = value.strip()
        elif key == "is_head_office":
            if not isinstance(value, bool):
                raise ValidationFailed("is_head_office must be boolean", field=key)
            out[key] = value
        elif key == "address":
            if not isinstance(value, str) or len(value) > 255:
                raise ValidationFailed("invalid address", field=key)
            out[key] = value
        elif key == "municipality_code":
            if value is not None and (not isinstance(value, str) or not _DANE.match(value)):
                raise ValidationFailed("municipality_code must be a 5-digit DANE code", field=key)
            out[key] = value
        elif key == "phone":
            if value is not None and (not isinstance(value, str) or not _PHONE.match(value)):
                raise ValidationFailed("invalid phone", field=key)
            out[key] = value
        elif key in ("valid_from", "valid_to"):
            if value is None and key == "valid_to":
                out[key] = None
                continue
            try:
                import datetime as dt

                out[key] = dt.date.fromisoformat(str(value))
            except ValueError as exc:
                raise ValidationFailed(f"{key} must be a date", field=key) from exc
        elif key == "default_business_unit_id":
            out[key] = _business_unit(ctx, company_id, value)
        elif key == "default_warehouse_id":
            out[key] = _warehouse(ctx, company_id, value)
    if out.get("valid_to") and out.get("valid_from") and out["valid_to"] < out["valid_from"]:
        raise ValidationFailed("valid_to before valid_from", field="valid_to")
    return out


def _uuid_or_none(value: Any, field: str) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a UUID", field=field) from exc


def _business_unit(ctx: TenantContext, company_id: uuid.UUID, value: Any) -> uuid.UUID | None:
    bu = _uuid_or_none(value, "default_business_unit_id")
    if bu is None:
        return None
    dims = services.optional(Dimensions)
    if dims is None:
        raise ValidationFailed("business units are not available yet", field="default_business_unit_id")
    try:
        node = dims.get_node(company_id, "business_unit", bu)
    except NotFound as exc:
        raise BranchCrossCompany("business unit of another company or unknown") from exc
    if node.company_id != company_id:
        raise BranchCrossCompany("business unit of another company")
    return bu


def _warehouse(ctx: TenantContext, company_id: uuid.UUID, value: Any) -> uuid.UUID | None:
    wh = _uuid_or_none(value, "default_warehouse_id")
    if wh is None:
        return None
    if _referenced_company(WAREHOUSE_REFERENCE, ctx.tenant_id, wh) != company_id:
        raise BranchCrossCompany("warehouse of another company")
    return wh


def _audit(uow: UnitOfWork, action: str, branch: Branch, changes: dict[str, Any]) -> None:
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(uow, AuditEntry(action, "iam.branch", branch.id, branch.company_id, changes))


def _emit(uow: UnitOfWork, branch: Branch, change: str) -> None:
    uow.emit(
        "iam.branch.changed.v1",
        {"branch_id": str(branch.id), "company_id": str(branch.company_id), "change": change},
        company_id=branch.company_id,
    )


def _code_taken(tenant_id: uuid.UUID, company_id: uuid.UUID, code: str, exclude: uuid.UUID | None = None) -> bool:
    qs = Branch.objects.annotate(lc=Lower("code")).filter(tenant_id=tenant_id, company_id=company_id, lc=code.lower())
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return qs.exists()


def _head_office(tenant_id: uuid.UUID, company_id: uuid.UUID) -> Branch | None:
    return (
        Branch.objects.select_for_update()
        .filter(tenant_id=tenant_id, company_id=company_id, status=Branch.Status.ACTIVE, is_head_office=True)
        .first()
    )


def _demote(head: Branch | None, keep: uuid.UUID | None, principal: uuid.UUID) -> None:
    if head is not None and head.id != keep:
        head.is_head_office = False
        head.save_versioned(principal, ["is_head_office"])


def _lock_company(tenant_id: uuid.UUID, company_id: uuid.UUID) -> None:
    """Serialize head-office changes per company (row lock on the company)."""
    if not Company.objects.select_for_update().filter(tenant_id=tenant_id, id=company_id).exists():
        raise NotFound("company not found")


def _save(fn: Any) -> Any:
    try:
        with transaction.atomic():
            return fn()
    except IntegrityError as exc:
        text = str(exc)
        if "iam_branch_code_uq" in text:
            raise BranchCodeDuplicate("branch code already used in this company") from exc
        if "iam_branch_one_head_office_uq" in text:
            raise BranchHeadOfficeRequired("exactly one head office per company") from exc
        raise


def create_branch(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Branch:
    ctx = uow.ctx
    for required in ("code", "name"):
        if required not in data:
            raise ValidationFailed(f"{required} is required", field=required)
    clean = _clean(data, company_id, ctx)
    _lock_company(ctx.tenant_id, company_id)
    if _code_taken(ctx.tenant_id, company_id, clean["code"]):
        raise BranchCodeDuplicate("branch code already used in this company")
    head = _head_office(ctx.tenant_id, company_id)
    wants_head = clean.get("is_head_office", False)
    if head is None and not wants_head:
        raise BranchHeadOfficeRequired("the first active branch of a company must be its head office")
    branch = Branch(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        valid_from=clean.pop("valid_from", clock.business_date()),
        **clean,
    )

    def write() -> Branch:
        if wants_head:
            _demote(head, None, ctx.principal_id)
        branch.save(force_insert=True)
        return branch

    _save(write)
    _audit(uow, "iam.branch.created", branch, {"fields": sorted(clean)})
    _emit(uow, branch, "created")
    return branch


def update_branch(uow: UnitOfWork, branch: Branch, data: Mapping[str, Any]) -> Branch:
    ctx = uow.ctx
    if not data:
        raise ValidationFailed("empty patch")
    clean = _clean(data, branch.company_id, ctx)
    _lock_company(ctx.tenant_id, branch.company_id)
    if "code" in clean and _code_taken(ctx.tenant_id, branch.company_id, clean["code"], exclude=branch.id):
        raise BranchCodeDuplicate("branch code already used in this company")
    head = _head_office(ctx.tenant_id, branch.company_id)
    if branch.status == Branch.Status.ACTIVE:
        if clean.get("is_head_office") is False and branch.is_head_office:
            raise BranchHeadOfficeRequired("promote another branch to head office first")
    elif clean.get("is_head_office"):
        raise BranchHeadOfficeRequired("an archived branch cannot be the head office")
    for k, v in clean.items():
        setattr(branch, k, v)

    def write() -> Branch:
        if clean.get("is_head_office"):
            _demote(head, branch.id, ctx.principal_id)
        branch.save_versioned(ctx.principal_id, list(clean))
        return branch

    _save(write)
    _audit(uow, "iam.branch.updated", branch, {"fields": sorted(clean)})
    _emit(uow, branch, "updated")
    return branch


def archive_branch(uow: UnitOfWork, branch: Branch) -> Branch:
    ctx = uow.ctx
    if branch.status == Branch.Status.ARCHIVED:
        return branch
    _lock_company(ctx.tenant_id, branch.company_id)
    refs = active_references(branch)
    if refs:
        raise BranchInUse("the branch is referenced by active records", dependencies=refs)
    others = Branch.objects.filter(
        tenant_id=ctx.tenant_id, company_id=branch.company_id, status=Branch.Status.ACTIVE
    ).exclude(pk=branch.pk)
    if branch.is_head_office and others.exists():
        raise BranchHeadOfficeRequired("promote another branch to head office before archiving this one")
    branch.status = Branch.Status.ARCHIVED
    branch.is_head_office = False
    _save(lambda: branch.save_versioned(ctx.principal_id, ["status", "is_head_office"]))
    _audit(uow, "iam.branch.archived", branch, {})
    _emit(uow, branch, "archived")
    return branch


def unarchive_branch(uow: UnitOfWork, branch: Branch) -> Branch:
    ctx = uow.ctx
    if branch.status == Branch.Status.ACTIVE:
        return branch
    _lock_company(ctx.tenant_id, branch.company_id)
    branch.status = Branch.Status.ACTIVE
    branch.is_head_office = _head_office(ctx.tenant_id, branch.company_id) is None
    _save(lambda: branch.save_versioned(ctx.principal_id, ["status", "is_head_office"]))
    _audit(uow, "iam.branch.unarchived", branch, {})
    _emit(uow, branch, "unarchived")
    return branch
