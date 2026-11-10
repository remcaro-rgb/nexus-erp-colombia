"""``/api/v1/ledger/dimensions/{cost-centers|business-units|aux-abierto|item-contable}``.

Permissions (contracts/permissions/catalog.yaml): ``ledger.dimension.read|create|update|delete|export``.
Row policy: ``PolicyService.scope_queryset`` (company scope, optional subtree grants by P04)
applies to list, tree, get, usage and export alike.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import uuid
from typing import Any

from django.db.models import Q, QuerySet
from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from kernel import tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger.api import LedgerAPIView, command, require, respond, scoped
from ledger.dimensions import service
from ledger.models import DimensionNode

SLUGS = {
    "cost-centers": "cost_center",
    "business-units": "business_unit",
    "aux-abierto": "aux_abierto",
    "item-contable": "item_contable",
}
READ, CREATE, UPDATE, DELETE, EXPORT = (
    "ledger.dimension.read",
    "ledger.dimension.create",
    "ledger.dimension.update",
    "ledger.dimension.delete",
    "ledger.dimension.export",
)
SORTS = {"code": "code", "description": "description", "path": "path", "sort_key": "sort_key", "depth": "depth"}
MAX_LIMIT = 500


def dimension_of(slug: str) -> str:
    try:
        return SLUGS[slug]
    except KeyError:
        raise NotFound("unknown dimension") from None


def serialize(n: DimensionNode, children: int | None = None) -> dict[str, Any]:
    body = {
        "id": str(n.id),
        "tenant_id": str(n.tenant_id),
        "company_id": str(n.company_id),
        "dimension": n.dimension,
        "code": n.code,
        "description": n.description,
        "node_type": n.node_type,
        "parent_id": str(n.parent_id) if n.parent_id else None,
        "valid_from": n.valid_from.isoformat() if n.valid_from else None,
        "valid_to": n.valid_to.isoformat() if n.valid_to else None,
        "status": n.status,
        "sort_key": n.sort_key,
        "external_ref": n.external_ref,
        "path": n.path,
        "depth": n.depth,
        "version": n.version,
        "audit": {
            "created_at": n.created_at.isoformat(),
            "created_by": str(n.created_by) if n.created_by else None,
            "updated_at": n.updated_at.isoformat(),
            "updated_by": str(n.updated_by) if n.updated_by else None,
        },
    }
    if children is not None:
        body["children_count"] = children
    return body


def _visible(ctx: TenantContext, company: uuid.UUID, dimension: str, action: str = READ) -> QuerySet[DimensionNode]:
    return scoped(ctx, action, service.nodes_qs(ctx, company, dimension))


def _visible_node(
    ctx: TenantContext, company: uuid.UUID, dimension: str, node_id: uuid.UUID, *, lock: bool = False
) -> DimensionNode:
    qs = _visible(ctx, company, dimension).filter(id=node_id)
    if lock:
        qs = qs.select_for_update()
    node = qs.first()
    if node is None:
        raise NotFound("dimension node not found")
    return node


def _date_param(value: str | None, name: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationFailed("invalid date", pointer=f"/query/{name}", reason="filter_invalid") from None


class DimensionListView(LedgerAPIView):
    def get(self, request: Request, slug: str) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        p = request.query_params
        with tenancy.scope(ctx):
            qs = _visible(ctx, company, dimension)
            if p.get("q"):
                q = p["q"].strip()
                qs = qs.filter(Q(code__icontains=q) | Q(description__icontains=q) | Q(path__icontains=q))
            if p.get("node_type"):
                qs = qs.filter(node_type=p["node_type"])
            if p.get("status"):
                qs = qs.filter(status=p["status"])
            if p.get("parent_id"):
                qs = qs.filter(parent_id=None if p["parent_id"] == "root" else _uuid(p["parent_id"]))
            valid_on = _date_param(p.get("valid_on"), "valid_on")
            if valid_on:
                qs = qs.filter(Q(valid_from__lte=valid_on), Q(valid_to__isnull=True) | Q(valid_to__gte=valid_on))
            order = []
            for part in (p.get("sort") or "path").split(","):
                desc = part.startswith("-")
                field = SORTS.get(part.lstrip("-"))
                if field is None:
                    raise ValidationFailed("unknown sort field", pointer="/query/sort", reason="filter_invalid")
                order.append(("-" if desc else "") + field)
            qs = qs.order_by(*order, "id")
            limit = min(int(p.get("limit") or 100), MAX_LIMIT)
            offset = _cursor(p.get("cursor"))
            rows = list(qs[offset : offset + limit + 1])
            counts = service.child_counts(company, dimension)
            items = [serialize(n, counts.get(n.id, 0)) for n in rows[:limit]]
        next_cursor = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if len(rows) > limit else None
        return Response({"items": items, "next_cursor": next_cursor})

    def post(self, request: Request, slug: str) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        require(ctx, CREATE)
        data = service.NodeInput.from_mapping(_body(request))

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            node = service.create_node(uow, company, dimension, data)
            return serialize(node, 0), 201, node.etag

        return command(request, ctx, f"ledger.dimension.{dimension}.create", fn)


class DimensionTreeView(LedgerAPIView):
    def get(self, request: Request, slug: str) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        p = request.query_params
        with tenancy.scope(ctx):
            allowed = set(_visible(ctx, company, dimension).values_list("id", flat=True))
            depth = int(p["depth"]) if p.get("depth") else None
            root = _uuid(p["root"]) if p.get("root") else None
            if root is not None and root not in allowed:
                raise NotFound("dimension node not found")
            body = service.tree(
                ctx,
                company,
                dimension,
                root=root,
                depth=depth,
                valid_on=_date_param(p.get("valid_on"), "valid_on"),
                allowed_ids=allowed,
            )
        return Response(body)


class DimensionExportView(LedgerAPIView):
    FIELDS = (
        "dimension",
        "code",
        "description",
        "node_type",
        "parent_code",
        "valid_from",
        "valid_to",
        "status",
        "sort_key",
        "external_ref",
    )

    def get(self, request: Request, slug: str) -> HttpResponse:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            require(ctx, READ)
            nodes = list(_visible(ctx, company, dimension, EXPORT).order_by("depth", "path"))
            codes = {n.id: n.code for n in service.nodes_qs(ctx, company, dimension)}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(self.FIELDS)
        for n in nodes:
            writer.writerow(
                [
                    n.dimension,
                    n.code,
                    n.description,
                    n.node_type,
                    codes.get(n.parent_id, "") if n.parent_id else "",
                    n.valid_from.isoformat() if n.valid_from else "",
                    n.valid_to.isoformat() if n.valid_to else "",
                    n.status,
                    n.sort_key or "",
                    n.external_ref or "",
                ]
            )
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{slug}.csv"'
        return resp


class DimensionDetailView(LedgerAPIView):
    def get(self, request: Request, slug: str, node_id: uuid.UUID) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            node = _visible_node(ctx, company, dimension, node_id)
            body = serialize(node, service.child_counts(company, dimension).get(node.id, 0))
        return respond(body, etag=node.etag)

    def patch(self, request: Request, slug: str, node_id: uuid.UUID) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        require(ctx, UPDATE)
        data = _body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            node = _visible_node(ctx, company, dimension, node_id, lock=True)
            node = service.update_node(uow, node, data, request.headers.get("If-Match"))
            return serialize(node), 200, node.etag

        return command(request, ctx, f"ledger.dimension.{dimension}.update", fn)

    def delete(self, request: Request, slug: str, node_id: uuid.UUID) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        require(ctx, DELETE)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            node = _visible_node(ctx, company, dimension, node_id, lock=True)
            service.delete_node(uow, node, request.headers.get("If-Match"))
            return None, 204, None

        return command(request, ctx, f"ledger.dimension.{dimension}.delete", fn)


class DimensionActionView(LedgerAPIView):
    ACTIONS = ("archive", "unarchive", "move")

    def post(self, request: Request, slug: str, node_id: uuid.UUID, action: str) -> Response:
        dimension = dimension_of(slug)
        if action not in self.ACTIONS:
            raise NotFound("unknown action")
        ctx, company = self.ctx_company(request)
        require(ctx, UPDATE)
        body = _body(request, optional=True)
        extra = set(body) - {"reason", "comment", "effective_date", "parameters"}
        if extra:
            raise ValidationFailed("unknown fields", pointer="/" + sorted(extra)[0], reason="additional_property")
        params = body.get("parameters") or {}
        reason = str(body.get("reason") or "")

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            node = _visible_node(ctx, company, dimension, node_id, lock=True)
            if_match = request.headers.get("If-Match")
            if if_match:
                node.check_if_match(if_match)
            if action == "archive":
                node = service.archive_node(uow, node, reason)
            elif action == "unarchive":
                node = service.unarchive_node(uow, node, reason)
            else:
                if "parent_id" not in params:
                    raise ValidationFailed(
                        "parameters.parent_id required", pointer="/parameters/parent_id", reason="required"
                    )
                node = service.move_node(uow, node, params.get("parent_id"))
            return serialize(node), 200, node.etag

        return command(request, ctx, f"ledger.dimension.{dimension}.{action}", fn)


class DimensionUsageView(LedgerAPIView):
    def get(self, request: Request, slug: str, node_id: uuid.UUID) -> Response:
        dimension = dimension_of(slug)
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            node = _visible_node(ctx, company, dimension, node_id)
            body = service.usage(node)
        return Response(body)


def _body(request: Request, optional: bool = False) -> dict[str, Any]:
    data = request.data
    if data in (None, "") and optional:
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed("JSON object expected", pointer="/", reason="type")
    return dict(data)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed("invalid id", pointer="/query", reason="filter_invalid") from None


def _cursor(value: str | None) -> int:
    if not value:
        return 0
    try:
        n = int(base64.urlsafe_b64decode(value.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        raise ValidationFailed("invalid cursor", pointer="/query/cursor", reason="filter_invalid") from None
    if n < 0:
        raise ValidationFailed("invalid cursor", pointer="/query/cursor", reason="filter_invalid")
    return n
