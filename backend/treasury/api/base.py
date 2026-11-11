"""Treasury API plumbing: context, company, idempotent commands, If-Match, keyset paging.

Every handler authorizes through ``PolicyService`` (deny by default; the services call ``require``),
every state change needs an ``Idempotency-Key`` and runs in ONE unit of work whose stored response is
returned on replay (``Idempotent-Replayed: true``). Foreign / out-of-scope ids answer 404.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from django.db.models import QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView
from kernel.errors import DomainError, NotAuthenticated, ValidationFailed
from kernel.idempotency import StoredResponse, validate_key
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
ACTION_KEYS = ("reason", "comment", "effective_date", "parameters")


class IdempotencyKeyRequired(DomainError):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class FilterInvalid(DomainError):
    code, status, title = "filter_invalid", 400, "Invalid filter"


def company_id(request: Request, ctx: TenantContext) -> uuid.UUID:
    raw = request.headers.get("X-Company-Id")
    if raw:
        try:
            cid = uuid.UUID(raw)
        except ValueError as exc:
            raise ValidationFailed("X-Company-Id must be a UUID", field="X-Company-Id") from exc
        return ctx.require_company(cid)
    if len(ctx.company_ids) == 1:
        return next(iter(ctx.company_ids))
    raise ValidationFailed("X-Company-Id header required (several companies)", field="X-Company-Id")


def idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    return validate_key(key)


def body(request: Request) -> dict[str, Any]:
    data = request.data
    if data in (None, "") or (hasattr(data, "__len__") and not data):
        return {}
    if not isinstance(data, Mapping):
        raise ValidationFailed("request body must be a JSON object")
    return dict(data)


def action_body(request: Request) -> tuple[str, dict[str, Any]]:
    """``ActionRequest``: ``reason``, ``comment``, ``effective_date`` and action-specific ``parameters``."""
    data = body(request)
    unknown = sorted(set(data) - set(ACTION_KEYS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    params = data.get("parameters") or {}
    if not isinstance(params, Mapping):
        raise ValidationFailed("parameters must be an object", field="parameters")
    reason = data.get("reason") or ""
    if not isinstance(reason, str) or len(reason) > 2000:
        raise ValidationFailed("reason must be a text of at most 2000 characters", field="reason")
    return reason, dict(params)


def check_if_match(request: Request, obj: Any, *, required: bool) -> None:
    header = request.headers.get("If-Match")
    if not header and not required:
        return
    obj.check_if_match(header)


def command(
    request: Request,
    ctx: TenantContext,
    scope: str,
    fn: Callable[[UnitOfWork], tuple[Any, int]],
    *,
    etag: Callable[[Any], str | None] | None = None,
) -> Response:
    key = idempotency_key(request)
    payload = {"path": request.path, "body": body(request)}
    holder: dict[str, Any] = {}

    def run(uow: UnitOfWork) -> tuple[Any, int]:
        data, status = fn(uow)
        tag = etag(data) if etag else None
        holder["etag"] = tag
        uow.set_response({"body": data, "etag": tag}, status)
        return data, status

    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=payload, scope=scope)
    if isinstance(result, StoredResponse):
        stored = result.body or {}
        response = Response(stored.get("body"), status=result.status_code)
        if stored.get("etag"):
            response["ETag"] = stored["etag"]
        response["Idempotent-Replayed"] = "true"
        return response
    data, status = result
    response = Response(data, status=status)
    if holder.get("etag"):
        response["ETag"] = holder["etag"]
    response["Idempotent-Replayed"] = "false"
    return response


def page(
    request: Request,
    queryset: QuerySet[Any],
    to_json: Callable[[Any], dict[str, Any]],
    filters: Mapping[str, str] | None = None,
    ignored: Iterable[str] = (),
) -> Response:
    """Keyset pagination on the UUIDv7 primary key; ``filter[name]=value`` / ``name=value`` equality filters."""
    filters = dict(filters or {})
    for key, value in request.query_params.items():
        if key in ("limit", "cursor", "fields", "sort", "q") or key in ignored:
            continue
        name = key[len("filter[") : -1] if key.startswith("filter[") and key.endswith("]") else key
        if name not in filters:
            raise FilterInvalid(f"unknown filter {key}")
        queryset = queryset.filter(**{filters[name]: value})
    try:
        limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
    except ValueError as exc:
        raise FilterInvalid("limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise FilterInvalid(f"limit must be 1..{MAX_LIMIT}")
    qs = queryset.order_by("id")
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            last = uuid.UUID(base64.urlsafe_b64decode(cursor.encode()).decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise FilterInvalid("invalid cursor") from exc
        qs = qs.filter(id__gt=last)
    try:
        rows = list(qs[: limit + 1])
    except (ValueError, TypeError) as exc:
        raise FilterInvalid("invalid filter value") from exc
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = base64.urlsafe_b64encode(str(rows[-1].id).encode()).decode() if more and rows else None
    return Response({"items": [to_json(r) for r in rows], "next_cursor": next_cursor})


class TreasuryView(ModuleSecuredView):
    """P04's secured base (IAM authentication, throttling, forced-RLS read scope); handlers authorize."""

    def context(self) -> TenantContext:
        ctx = self.ctx
        if ctx is None:
            raise NotAuthenticated("authentication required")
        return ctx

    def ctx_company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        ctx = self.context()
        return ctx, company_id(request, ctx)
