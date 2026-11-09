"""Roles (``/api/v1/iam/roles``) and role assignments (``/api/v1/iam/role-assignments``)."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import SecuredView, check_if_match
from iam.models import Role, RoleAssignment
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine
from iam.roles import services
from kernel import tenancy
from kernel.uow import UnitOfWork

_AUDIT = "internal"
field_registry.register(
    "iam.role",
    dict.fromkeys(
        (
            "tenant_id",
            "code",
            "name",
            "status",
            "template_key",
            "grants",
            "denies",
            "max_classification",
            "version",
            "audit",
        ),
        _AUDIT,
    )
    | {"id": "public"},
)
field_registry.register(
    "iam.role_assignment",
    dict.fromkeys(
        (
            "tenant_id",
            "company_id",
            "active",
            "user_id",
            "role_id",
            "scope",
            "valid_from",
            "valid_to",
            "version",
            "audit",
        ),
        _AUDIT,
    )
    | {"id": "public"},
)


def role_dict(r: Role) -> dict[str, Any]:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "code": r.key,
        "name": r.name,
        "status": "ACTIVE" if r.active else "ARCHIVED",
        "template_key": None,
        "grants": list(r.grants),
        "denies": list(r.denies),
        "max_classification": r.max_classification,
        "version": r.version,
    }


def assignment_dict(a: RoleAssignment) -> dict[str, Any]:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "company_id": a.company_id,
        "active": a.active,
        "user_id": a.user_id,
        "role_id": a.role_id,
        "scope": a.scope,
        "valid_from": a.valid_from,
        "valid_to": a.valid_to,
        "version": a.version,
    }


class RolesView(SecuredView):
    resource_type = "iam.role"
    permissions = {"GET": "iam.role.read", "POST": "iam.role.create"}

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return role_dict(obj)

    def get(self, request: Request) -> Response:
        qs = get_engine().scope_queryset(self.ctx, "iam.role.read", Role.objects.all())
        return self.paginate(request, qs, search_fields=("key", "name"), sort_fields=("name",))

    def post(self, request: Request) -> Response:
        self.check_writable(request.data, ("code", "name", "grants", "denies", "max_classification", "template_key"))
        return self.mutate(
            request, lambda uow: (self.serialize(services.create_role(uow, request.data)), 201), "iam.role.create"
        )


class RoleAssignmentsView(SecuredView):
    resource_type = "iam.role_assignment"
    permissions = {"GET": "iam.role_assignment.read", "POST": "iam.role_assignment.create"}

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return assignment_dict(obj)

    def get(self, request: Request) -> Response:
        qs = get_engine().scope_queryset(self.ctx, "iam.role_assignment.read", RoleAssignment.objects.all())
        return self.paginate(
            request, qs, filter_fields={"user_id": "user_id", "role_id": "role_id", "active": "active"}
        )

    def post(self, request: Request) -> Response:
        self.check_writable(request.data, ("user_id", "role_id", "scope", "active", "valid_from", "valid_to"))
        company = sorted(self.ctx.company_ids)[0]

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            return self.serialize(services.assign(uow, company, request.data)), 201

        return self.mutate(request, run, "iam.role_assignment.create")


class RoleAssignmentDetailView(SecuredView):
    resource_type = "iam.role_assignment"
    permissions = {"GET": "iam.role_assignment.read", "DELETE": "iam.role_assignment.delete"}

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return assignment_dict(obj)

    def get(self, request: Request, pk: str) -> Response:
        with tenancy.scope(self.ctx):
            obj = self.get_scoped(RoleAssignment.objects.all(), pk, "iam.role_assignment.read")
            return Response(self.serialize(obj))

    def delete(self, request: Request, pk: str) -> Response:
        def run(uow: UnitOfWork) -> tuple[Any, int]:
            obj = self.get_scoped(RoleAssignment.objects.select_for_update(), pk, "iam.role_assignment.delete")
            check_if_match(request, self.resource_type, obj, required=True)
            services.unassign(uow, obj)
            return None, 204

        return self.mutate(request, run, "iam.role_assignment.delete")
