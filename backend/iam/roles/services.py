"""Roles and role assignments.

Rules: only INTERNAL users with a membership in the company can receive a role
(``external_role_forbidden`` for portal identities — also enforced by a DB trigger);
scope ids must belong to the same company (branches) or exist in the company's
cost-center master (``kernel.interfaces.Dimensions``). Every change bumps the tenant
``policy_version`` (revocation effective on the next request) and emits
``iam.policy.changed.v1``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from django.db import DatabaseError, transaction

from iam.errors import BranchCrossCompany, ExternalRoleForbidden
from iam.models import Branch, ExternalAccount, Membership, Role, RoleAssignment, User
from iam.policy.engine import SCOPE_KEYS, PolicyEngine
from iam.policy.patterns import CLASSIFICATIONS
from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.dimensions import Dimensions
from kernel.uow import UnitOfWork


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(f"{field} must be a UUID", field=field) from exc


def changed(uow: UnitOfWork, policy_type: str, object_id: uuid.UUID, change: str) -> None:
    PolicyEngine.bump_version(uow.ctx.tenant_id)
    uow.emit(
        "iam.policy.changed.v1",
        {"policy_type": policy_type, "object_id": str(object_id), "change": change},
    )


def create_role(uow: UnitOfWork, data: Mapping[str, Any]) -> Role:
    ctx = uow.ctx
    code, name = str(data.get("code") or "").strip(), str(data.get("name") or "").strip()
    if not code or len(code) > 40 or not name:
        raise ValidationFailed("code and name are required")
    grants, denies = list(data.get("grants") or []), list(data.get("denies") or [])
    for p in grants + denies:
        if not isinstance(p, str) or len(p) > 255 or p.count(".") != 2:
            raise ValidationFailed("grants/denies are <module>.<resource>.<action> patterns")
    max_cls = data.get("max_classification", "internal")
    if max_cls not in CLASSIFICATIONS or max_cls == "secret":
        raise ValidationFailed("invalid max_classification")
    if Role.objects.filter(tenant_id=ctx.tenant_id, key=code).exists():
        raise ValidationFailed("role code already exists", field="code")
    role = Role.objects.create(
        tenant_id=ctx.tenant_id,
        key=code,
        name=name,
        grants=grants,
        denies=denies,
        max_classification=max_cls,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    changed(uow, "role", role.id, "created")
    return role


def _validate_scope(company_id: uuid.UUID, tenant_id: uuid.UUID, scope: Any, on: dt.date) -> dict[str, list[str]]:
    if scope is None:
        return {}
    if not isinstance(scope, Mapping) or set(scope) - set(SCOPE_KEYS):
        raise ValidationFailed(f"scope keys must be among {', '.join(SCOPE_KEYS)}", field="scope")
    out: dict[str, list[str]] = {}
    for key, values in scope.items():
        if not isinstance(values, list):
            raise ValidationFailed(f"scope.{key} must be a list", field="scope")
        ids = [_uuid(v, f"scope.{key}") for v in values]
        if key == "branch_ids":
            found = set(
                Branch.objects.filter(tenant_id=tenant_id, company_id=company_id, id__in=ids).values_list(
                    "id", flat=True
                )
            )
            if found != set(ids):
                raise BranchCrossCompany("scope names a branch of another company")
        elif key == "cost_center_ids":
            dims = services.optional(Dimensions)
            if dims is None:
                raise ValidationFailed("cost centers are not available yet", field="scope")
            for node_id in ids:
                try:
                    node = dims.get_node(company_id, "cost_center", node_id)
                except NotFound as exc:
                    raise ValidationFailed("unknown cost center for this company", field="scope") from exc
                if node.company_id != company_id:
                    raise ValidationFailed("cost center of another company", code="dimension_cross_company")
        out[key] = [str(i) for i in ids]
    return out


def assign(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> RoleAssignment:
    ctx = uow.ctx
    user_id, role_id = _uuid(data.get("user_id"), "user_id"), _uuid(data.get("role_id"), "role_id")
    if ExternalAccount.objects.filter(tenant_id=ctx.tenant_id, id=user_id).exists():
        raise ExternalRoleForbidden("portal accounts cannot receive internal roles")
    user = User.objects.filter(tenant_id=ctx.tenant_id, id=user_id).first()
    if user is None:
        raise ValidationFailed("unknown user", field="user_id")
    if user.kind != User.Kind.INTERNAL:
        raise ExternalRoleForbidden("portal accounts cannot receive internal roles")
    role = Role.objects.filter(tenant_id=ctx.tenant_id, id=role_id, active=True).first()
    if role is None:
        raise ValidationFailed("unknown role", field="role_id")
    if not Membership.objects.filter(tenant_id=ctx.tenant_id, user=user, company_id=company_id).exists():
        raise ValidationFailed("the user is not a member of this company", field="user_id")
    from kernel import clock

    valid_from = dt.date.fromisoformat(data["valid_from"]) if data.get("valid_from") else clock.business_date()
    valid_to = dt.date.fromisoformat(data["valid_to"]) if data.get("valid_to") else None
    if valid_to and valid_to < valid_from:
        raise ValidationFailed("valid_to before valid_from", field="valid_to")
    scope = _validate_scope(company_id, ctx.tenant_id, data.get("scope"), valid_from)
    try:
        with transaction.atomic():
            assignment = RoleAssignment.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                user=user,
                role=role,
                scope=scope,
                active=bool(data.get("active", True)),
                valid_from=valid_from,
                valid_to=valid_to,
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
            )
    except DatabaseError as exc:
        if "external_role_forbidden" in str(exc) or "internal users" in str(exc):
            raise ExternalRoleForbidden("portal accounts cannot receive internal roles") from exc
        raise
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(uow, AuditEntry("iam.role_assignment.created", "iam.role_assignment", assignment.id, company_id))
    changed(uow, "assignment", assignment.id, "created")
    return assignment


def unassign(uow: UnitOfWork, assignment: RoleAssignment) -> None:
    assignment.active = False
    assignment.save_versioned(uow.ctx.principal_id, ["active"])
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(
            uow, AuditEntry("iam.role_assignment.deleted", "iam.role_assignment", assignment.id, assignment.company_id)
        )
    changed(uow, "assignment", assignment.id, "deleted")
