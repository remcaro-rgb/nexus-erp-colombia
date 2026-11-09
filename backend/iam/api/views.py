"""``/api/v1/iam/me`` and ``/api/v1/iam/cost-centers`` (scope projection)."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from rest_framework.request import Request
from rest_framework.response import Response

from iam import dimensions
from iam.api.base import SecuredView, parse_uuid
from iam.models import Membership
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine
from kernel import tenancy
from kernel.errors import NotFound

field_registry.register(
    "iam.cost_center_scope",
    dict.fromkeys(
        ("tenant_id", "company_id", "status", "code", "description", "path", "node_type", "parent_id"), "internal"
    )
    | {"id": "public"},
)


@functools.cache
def catalog_codes() -> tuple[str, ...]:
    """Permission codes of ``contracts/permissions/catalog.yaml`` (empty when not shipped)."""
    path = (
        Path(getattr(settings, "REPO_DIR", Path(__file__).resolve().parents[3])) / "contracts/permissions/catalog.yaml"
    )
    if not path.exists():
        return ()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(sorted({p["code"] for p in doc.get("codes", []) if isinstance(p, dict) and "code" in p}))


class MeView(SecuredView):
    resource_type = "iam.me"
    permissions = {"GET": "iam.me.read"}

    def get(self, request: Request) -> Response:
        ctx = self.ctx
        engine = get_engine()
        eff = engine.effective(ctx)
        user_id = ctx.on_behalf_of or ctx.principal_id
        with tenancy.scope(ctx):
            companies = sorted(
                str(c)
                for c in Membership.objects.filter(tenant_id=ctx.tenant_id, user_id=user_id).values_list(
                    "company_id", flat=True
                )
            )
        masks: dict[str, Any] = {}
        for resource in sorted(field_registry._REGISTRY):
            readable, writable, mask = engine.field_rules(ctx, resource)
            hidden = sorted(set(field_registry.fields_of(resource)) - readable)
            if hidden or mask:
                masks[resource] = {"hidden": hidden, "masked": mask, "readonly": sorted(readable - writable)}
        return Response(
            {
                "id": str(user_id),
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(user_id),
                "companies": companies,
                "permissions": eff.permissions(catalog_codes()),
                "field_masks": masks,
                "portal_kind": None,
            }
        )


def node_dict(n: Any) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "company_id": str(n.company_id),
        "status": "ACTIVE" if n.active else "ARCHIVED",
        "code": n.code,
        "description": n.description,
        "node_type": n.node_type,
        "parent_id": str(n.parent_id) if n.parent_id else None,
    }


class CostCentersView(SecuredView):
    resource_type = "iam.cost_center_scope"
    permissions = {"GET": "iam.cost_center_scope.read"}

    def get(self, request: Request) -> Response:
        company = sorted(self.ctx.company_ids)[0]
        nodes = dimensions.secure_nodes(
            self.ctx, "iam.cost_center_scope.read", dimensions.all_nodes(company), request.query_params.get("q", "")
        )
        if request.query_params.get("view") == "tree":
            return Response(
                {"items": dimensions.secure_tree(self.ctx, "iam.cost_center_scope.read", nodes), "next_cursor": None}
            )
        items = [self.serialize(node_dict(n)) for n in sorted(nodes, key=lambda n: n.code)]
        return Response({"items": items, "next_cursor": None})


class CostCenterDetailView(SecuredView):
    resource_type = "iam.cost_center_scope"
    permissions = {"GET": "iam.cost_center_scope.read"}

    def get(self, request: Request, pk: str) -> Response:
        company = sorted(self.ctx.company_ids)[0]
        node_id = parse_uuid(pk)
        nodes = [n for n in dimensions.all_nodes(company) if n.id == node_id]
        visible = dimensions.secure_nodes(self.ctx, "iam.cost_center_scope.read", nodes)
        if not visible:
            raise NotFound("not found")
        return Response(self.serialize(node_dict(visible[0])))
