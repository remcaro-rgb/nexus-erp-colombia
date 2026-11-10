"""Plumbing of the payroll API: company header, idempotent commands, keyset paging.

Every handler authorizes through the PolicyService inside the service functions (deny by
default; the same checks serve UI, API, exports, AI and MCP). Writes require ``Idempotency-Key``
and run in one unit of work; replays return the stored response.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from typing import Any

from django.db.models import QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView
from kernel.errors import DomainError, ValidationFailed
from kernel.idempotency import StoredResponse
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


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
    key = str(request.headers.get("Idempotency-Key", "") or "")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    if not 8 <= len(key) <= 128:
        raise ValidationFailed("Idempotency-Key must have 8..128 characters", field="Idempotency-Key")
    return key


def body(request: Request) -> dict[str, Any]:
    data = request.data if request.data not in (None, "") else {}
    if not isinstance(data, dict):
        raise ValidationFailed("request body must be a JSON object")
    return dict(data)


def command(
    request: Request,
    ctx: TenantContext,
    scope: str,
    fn: Callable[[UnitOfWork], tuple[Any, int, dict[str, str]]],
) -> Response:
    key = idempotency_key(request)
    headers_box: dict[str, str] = {}

    def run(uow: UnitOfWork) -> tuple[Any, int]:
        payload, status, headers = fn(uow)
        headers_box.update(headers)
        uow.set_response({"body": payload, "headers": headers}, status)
        return payload, status

    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=body(request), scope=scope)
    if isinstance(result, StoredResponse):
        stored = result.body or {}
        response = Response(stored.get("body"), status=result.status_code)
        for name, value in (stored.get("headers") or {}).items():
            response[name] = value
        response["Idempotent-Replayed"] = "true"
        return response
    payload, status = result
    response = Response(payload, status=status)
    for name, value in headers_box.items():
        response[name] = value
    return response


def page(request: Request, queryset: QuerySet[Any], to_json: Callable[[Any], dict[str, Any]]) -> Response:
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


def etag(obj: Any) -> dict[str, str]:
    tag = getattr(obj, "etag", None)
    return {"ETag": tag} if tag else {}


class PayrollAPIView(ModuleSecuredView):
    """IAM authentication; tenant context required (401); every handler authorizes its permission."""
