"""Shared plumbing of the einvoice API: context, company header, policy, idempotent commands, paging.

Every handler authorizes through ``PolicyService`` (deny by default; the same check serves UI, API,
exports, AI and MCP). Writes run in a unit of work keyed by the required ``Idempotency-Key``; replays
return the stored response. Actions with an external effect (``:test-connection``, ``:sync``, ``:run``)
do their external part outside the unit of work and record it in an idempotent command afterwards.
"""

from __future__ import annotations

import base64
import datetime as dt
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from django.db.models import Model, QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView
from kernel import services, tenancy
from kernel.errors import DomainError, NotAuthenticated, NotFound, ValidationFailed
from kernel.idempotency import StoredResponse
from kernel.interfaces.policy import PolicyService
from kernel.models import IdempotencyRecord
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


class IdempotencyKeyRequired(DomainError):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class FilterInvalid(DomainError):
    code, status, title = "filter_invalid", 400, "Invalid filter"


def context(request: Request) -> TenantContext:
    ctx = tenancy.current()
    if ctx is None:
        raise NotAuthenticated("no authenticated principal", code="unauthenticated")
    return ctx


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


def authorize(ctx: TenantContext, permission: str, resource: Any = None) -> None:
    services.get(PolicyService).require(ctx, permission, resource)


def scoped(ctx: TenantContext, permission: str, queryset: QuerySet[Any]) -> QuerySet[Any]:
    return services.get(PolicyService).scope_queryset(ctx, permission, queryset)  # type: ignore[no-any-return]


def idempotency_key(request: Request) -> str:
    key = str(request.headers.get("Idempotency-Key", "") or "")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    if not 8 <= len(key) <= 128:
        raise ValidationFailed("Idempotency-Key must have 8..128 characters", field="Idempotency-Key")
    return key


def already_used(ctx: TenantContext, request: Request) -> bool:
    """True when this Idempotency-Key was already committed (the command will replay; skip external work)."""
    return IdempotencyRecord.objects.filter(tenant_id=ctx.tenant_id, key=idempotency_key(request)).exists()


def command(
    request: Request,
    ctx: TenantContext,
    scope: str,
    fn: Callable[[UnitOfWork], tuple[Any, int, dict[str, str]]],
    payload: Any = None,
) -> Response:
    key = idempotency_key(request)
    headers_box: dict[str, str] = {}

    def run(uow: UnitOfWork) -> tuple[Any, int]:
        body, status, headers = fn(uow)
        headers_box.update(headers)
        uow.set_response({"body": body, "headers": headers}, status)
        return body, status

    body = request.data if payload is None else payload
    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=body, scope=scope)
    if isinstance(result, StoredResponse):
        stored = result.body or {}
        response = Response(stored.get("body"), status=result.status_code)
        for name, value in (stored.get("headers") or {}).items():
            response[name] = value
        response["Idempotent-Replayed"] = "true"
        return response
    out, status = result
    response = Response(out, status=status)
    for name, value in headers_box.items():
        response[name] = value
    return response


def body_object(request: Request, allowed: Iterable[str], required: Iterable[str] = ()) -> dict[str, Any]:
    data = request.data
    if data in (None, "") and not list(required):
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed("request body must be a JSON object")
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown, code="field_forbidden")
    missing = [k for k in required if data.get(k) in (None, "")]
    if missing:
        raise ValidationFailed(f"missing properties: {', '.join(missing)}", fields=missing)
    return dict(data)


def action_body(request: Request) -> tuple[dict[str, Any], str]:
    data = body_object(request, {"reason", "comment", "effective_date", "parameters"})
    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValidationFailed("parameters must be an object", field="parameters")
    return params, str(data.get("reason") or "")


def parse_uuid(raw: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{field} must be a UUID", field=field) from exc


def parse_date(raw: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{field} must be a date (YYYY-MM-DD)", field=field) from exc


def parse_int(raw: Any, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationFailed(f"{field} must be an integer", field=field)
    return raw


def page(request: Request, queryset: QuerySet[Any], to_json: Callable[[Any], dict[str, Any]]) -> Response:
    """Keyset pagination on the UUIDv7 primary key (``cursor`` is opaque)."""
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
    rows = list(qs[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = base64.urlsafe_b64encode(str(rows[-1].id).encode()).decode() if more and rows else None
    return Response({"items": [to_json(r) for r in rows], "next_cursor": next_cursor})


def get_or_404(queryset: QuerySet[Any], pk: uuid.UUID) -> Any:
    obj = queryset.filter(pk=pk).first()
    if obj is None:
        raise NotFound("not found")
    return obj


def audit(obj: Model) -> dict[str, Any]:
    created_at, updated_at = getattr(obj, "created_at", None), getattr(obj, "updated_at", None)
    created_by, updated_by = getattr(obj, "created_by", None), getattr(obj, "updated_by", None)
    out: dict[str, Any] = {}
    if created_at:
        out["created_at"] = created_at.isoformat()
    if created_by:
        out["created_by"] = str(created_by)
    if updated_at:
        out["updated_at"] = updated_at.isoformat()
    if updated_by:
        out["updated_by"] = str(updated_by)
    return out


def with_etag(response: Response, obj: Any) -> Response:
    etag = getattr(obj, "etag", None)
    if etag:
        response["ETag"] = etag
    return response


class EinvoiceAPIView(ModuleSecuredView):
    """P04's secured base: IAM authentication, tenant context required (401), each handler authorizes."""
