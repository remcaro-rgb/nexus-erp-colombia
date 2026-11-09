"""Branch API (``/api/v1/iam/companies/{company_id}/branches``, ``/api/v1/iam/branches``)."""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from kernel import tenancy
from kernel.errors import NotFound
from kernel.uow import UnitOfWork

from iam.api.base import SecuredView, check_if_match, parse_uuid
from iam.branches import services
from iam.models import Branch
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine

RESOURCE = "iam.branch"
field_registry.register(
    RESOURCE,
    {
        "id": "public",
        "tenant_id": "internal",
        "code": "internal",
        "name": "internal",
        "status": "internal",
        "company_id": "internal",
        "is_head_office": "internal",
        "address": "internal",
        "municipality_code": "internal",
        "phone": "internal",
        "default_business_unit_id": "internal",
        "default_warehouse_id": "internal",
        "valid_from": "internal",
        "valid_to": "internal",
        "version": "internal",
        "audit": "internal",
    },
)
FILTERS = {"code": "code", "name": "name", "status": "status", "is_head_office": "is_head_office"}
SORTS = ("code", "name", "status")
SEARCH = ("code", "name", "address")


def branch_dict(b: Branch) -> dict[str, Any]:
    return {
        "id": b.id,
        "tenant_id": b.tenant_id,
        "code": b.code,
        "name": b.name,
        "status": b.status,
        "company_id": b.company_id,
        "is_head_office": b.is_head_office,
        "address": b.address,
        "municipality_code": b.municipality_code,
        "phone": b.phone,
        "default_business_unit_id": b.default_business_unit_id,
        "default_warehouse_id": b.default_warehouse_id,
        "valid_from": b.valid_from,
        "valid_to": b.valid_to,
        "version": b.version,
        "audit": {
            "created_at": b.created_at.isoformat(),
            "created_by": str(b.created_by) if b.created_by else None,
            "updated_at": b.updated_at.isoformat(),
            "updated_by": str(b.updated_by) if b.updated_by else None,
        },
    }


class _BranchView(SecuredView):
    resource_type = RESOURCE

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return branch_dict(obj)

    def _company(self, company_id: str) -> uuid.UUID:
        cid = parse_uuid(company_id)
        if cid not in self.ctx.company_ids:
            raise NotFound("not found")
        return cid

    def _load(self, pk: str, action: str, lock: bool = False) -> Branch:
        qs = Branch.objects.all()
        if lock:
            qs = qs.select_for_update()
        return self.get_scoped(qs, pk, action)  # type: ignore[no-any-return]


class CompanyBranchesView(_BranchView):
    permissions = {"GET": "iam.branch.read", "POST": "iam.branch.create"}

    def get(self, request: Request, company_id: str) -> Response:
        cid = self._company(company_id)
        qs = get_engine().scope_queryset(self.ctx, "iam.branch.read", Branch.objects.filter(company_id=cid))
        return self.paginate(request, qs, search_fields=SEARCH, filter_fields=FILTERS, sort_fields=SORTS)

    def post(self, request: Request, company_id: str) -> Response:
        cid = self._company(company_id)
        self.check_writable(request.data, services.EDITABLE)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            get_engine().require(self.ctx, "iam.branch.create", {"company_id": cid})
            branch = services.create_branch(uow, cid, request.data)
            return self.serialize(branch), 201

        response = self.mutate(request, run, scope="iam.branch.create")
        if response.status_code == 201 and isinstance(response.data, dict) and "id" in response.data:
            response["Location"] = f"/api/v1/iam/branches/{response.data['id']}"
        return response


class BranchesView(_BranchView):
    permissions = {"GET": "iam.branch.read"}

    def get(self, request: Request) -> Response:
        qs = get_engine().scope_queryset(self.ctx, "iam.branch.read", Branch.objects.all())
        return self.paginate(request, qs, search_fields=SEARCH, filter_fields=FILTERS, sort_fields=SORTS)


class BranchDetailView(_BranchView):
    permissions = {"GET": "iam.branch.read", "PATCH": "iam.branch.update"}

    def get(self, request: Request, pk: str) -> Response:
        with tenancy.scope(self.ctx):
            branch = self._load(pk, "iam.branch.read")
            response = Response(self.serialize(branch))
        response["ETag"] = _etag(branch)
        return response

    def patch(self, request: Request, pk: str) -> Response:
        self.check_writable(request.data, services.EDITABLE)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            branch = self._load(pk, "iam.branch.update", lock=True)
            check_if_match(request, RESOURCE, branch, required=True)
            services.update_branch(uow, branch, request.data)
            return self.serialize(branch), 200

        return self.mutate(request, run, scope="iam.branch.update")


class BranchActionView(_BranchView):
    permissions = {"POST": "iam.branch.update"}
    action_name = ""

    def post(self, request: Request, pk: str) -> Response:
        def run(uow: UnitOfWork) -> tuple[Any, int]:
            branch = self._load(pk, "iam.branch.update", lock=True)
            check_if_match(request, RESOURCE, branch, required=False)
            fn = services.archive_branch if self.action_name == "archive" else services.unarchive_branch
            fn(uow, branch)
            return self.serialize(branch), 200

        return self.mutate(request, run, scope=f"iam.branch.{self.action_name}")


class BranchArchiveView(BranchActionView):
    action_name = "archive"


class BranchUnarchiveView(BranchActionView):
    action_name = "unarchive"


def _etag(branch: Branch) -> str:
    from iam.api.base import etag_for

    return etag_for(RESOURCE, branch.pk, branch.version)
