"""API plumbing of the fixed-assets endpoints: secured base view, company, policy, idempotency, ETags, paging.

* Every view inherits :class:`iam.api.base.ModuleSecuredView` (ADR-004 SEC-VIEW): IAM authentication,
  throttling, the request's tenant context bound for the whole handler (forced RLS).
* Every handler authorizes its own permission code (``contracts/openapi/modules/assets.yaml`` ``x-permission``)
  through the registered ``PolicyService`` (deny by default; fails closed when none is registered) and reads
  through ``scope_queryset``. Restricted fields (``custodian_employee_id``, restricted-pii) are dropped unless the
  field mask makes them readable.
* Every state change requires an ``Idempotency-Key`` and runs in one unit of work (replays return the stored body).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from django.db.models import Q, QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView
from kernel import services
from kernel.errors import NotFound, PermissionDenied, PreconditionFailed, PreconditionRequired, ValidationFailed
from kernel.events import to_jsonable
from kernel.idempotency import StoredResponse, validate_key
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT, MAX_LIMIT = 50, 500
RESTRICTED_FIELDS = {"assets.asset": {"custodian_employee_id"}}
FILTER_OPS = {
    "eq": "",
    "ne": "",
    "lt": "__lt",
    "lte": "__lte",
    "gt": "__gt",
    "gte": "__gte",
    "in": "__in",
    "contains": "__icontains",
    "isnull": "__isnull",
}


class IdempotencyKeyRequired(ValidationFailed):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class PolicyDenied(PermissionDenied):
    code, title = "policy_denied", "Denied by access policy"


class FilterInvalid(ValidationFailed):
    code, status, title = "filter_invalid", 400, "Invalid filter"


class VersionConflict(PreconditionFailed):
    code = "version_conflict"


def policy() -> PolicyService:
    return services.get(PolicyService)


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    if not policy().authorize(ctx, action, resource).allowed:
        raise PolicyDenied(f"missing permission {action}")


def scoped(ctx: TenantContext, action: str, qs: QuerySet[Any]) -> QuerySet[Any]:
    require(ctx, action)
    result: QuerySet[Any] = policy().scope_queryset(ctx, action, qs)
    return result


def visible(ctx: TenantContext, resource_type: str, body: dict[str, Any]) -> dict[str, Any]:
    restricted = RESTRICTED_FIELDS.get(resource_type, set())
    if not restricted:
        return body
    readable = policy().field_mask(ctx, resource_type).readable
    return {k: v for k, v in body.items() if k not in restricted or k in readable}


def writable_check(ctx: TenantContext, resource_type: str, data: dict[str, Any]) -> None:
    restricted = RESTRICTED_FIELDS.get(resource_type, set()) & set(data)
    if restricted:
        writable = policy().field_mask(ctx, resource_type).writable
        denied = sorted(restricted - set(writable))
        if denied:
            raise PermissionDenied(f"field(s) not writable: {', '.join(denied)}", code="field_forbidden")


def idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    return validate_key(key)


def check_if_match(request: Request, obj: Any) -> None:
    header = request.headers.get("If-Match")
    if not header:
        raise PreconditionRequired("If-Match header required")
    candidates = {c.strip() for c in header.split(",")}
    if "*" not in candidates and obj.etag not in candidates:
        raise VersionConflict("the resource changed since it was read")


def respond(
    body: Any, status: int = 200, etag: str | None = None, replayed: bool | None = None, location: str | None = None
) -> Response:
    resp = Response(to_jsonable(body) if body is not None else None, status=status)
    if etag:
        resp["ETag"] = etag
    if replayed is not None:
        resp["Idempotent-Replayed"] = "true" if replayed else "false"
    if location:
        resp["Location"] = location
    return resp


def command(
    request: Request, ctx: TenantContext, scope: str, fn: Callable[[UnitOfWork], tuple[Any, int, str | None]]
) -> Response:
    key = idempotency_key(request)
    payload = {"path": request.path, "body": request.data if request.data else None}

    def run(uow: UnitOfWork) -> tuple[Any, int, str | None]:
        body, status, etag = fn(uow)
        body = to_jsonable(body)
        uow.set_response({"body": body, "etag": etag}, status)
        return body, status, etag

    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=payload, scope=scope)
    if isinstance(result, StoredResponse):
        stored = result.body or {}
        return respond(stored.get("body"), result.status_code, stored.get("etag"), replayed=True)
    body, status, etag = result
    return respond(body, status, etag, replayed=False)


def parse_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise NotFound("not found") from None


def body_of(request: Request, allowed: Iterable[str]) -> dict[str, Any]:
    data = request.data
    if not isinstance(data, dict):
        raise ValidationFailed("a JSON object is required", pointer="/", reason="type")
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown field(s): {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="unknown")
    return dict(data)


def paginate(
    request: Request,
    qs: QuerySet[Any],
    to_json: Callable[[Any], dict[str, Any]],
    *,
    filters: dict[str, str],
    search: Iterable[str] = (),
    sorts: Iterable[str] = (),
) -> Response:
    params = request.query_params
    for raw_key, value in params.items():
        name, op = _filter_key(raw_key)
        if name is None:
            continue
        if name not in filters or op not in FILTER_OPS:
            raise FilterInvalid(f"unknown filter {raw_key}")
        lookup = filters[name] + FILTER_OPS[op]
        val: Any = value.split(",") if op == "in" else (value.lower() == "true" if op == "isnull" else value)
        cond = Q(**{lookup: val})
        qs = qs.exclude(cond) if op == "ne" else qs.filter(cond)
    q = (params.get("q") or "").strip()
    if q:
        if len(q) > 200:
            raise FilterInvalid("q too long")
        cond = Q(pk__in=[])
        for f in search:
            cond |= Q(**{f"{f}__icontains": q})
        qs = qs.filter(cond)
    order = []
    for part in (params.get("sort") or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part.lstrip("-") not in sorts:
            raise FilterInvalid(f"cannot sort by {part.lstrip('-')}")
        order.append(part)
    qs = qs.order_by(*order, "id")
    try:
        limit = int(params.get("limit", DEFAULT_LIMIT))
    except ValueError:
        raise FilterInvalid("limit must be an integer") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise FilterInvalid("limit must be between 1 and 500")
    offset = 0
    if params.get("cursor"):
        try:
            offset = int(base64.urlsafe_b64decode(params["cursor"].encode()).decode())
        except (ValueError, binascii.Error, UnicodeDecodeError):
            raise FilterInvalid("invalid cursor") from None
    rows = list(qs[offset : offset + limit + 1])
    more = len(rows) > limit
    fields = {f.strip() for f in (params.get("fields") or "").split(",") if f.strip()}
    items = []
    for obj in rows[:limit]:
        item = to_json(obj)
        if fields:
            item = {k: v for k, v in item.items() if k in fields or k == "id"}
        items.append(item)
    next_cursor = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if more else None
    return respond({"items": items, "next_cursor": next_cursor})


def _filter_key(key: str) -> tuple[str | None, str]:
    """``filter[field][op]`` or ``field[op]``; other query parameters are not filters."""
    if key.startswith("filter[") and key.endswith("]"):
        inner = key[len("filter[") : -1]
        if "][" in inner:
            name, op = inner.split("][", 1)
            return name, op
        return inner, "eq"
    if "[" in key and key.endswith("]"):
        name, op = key[:-1].split("[", 1)
        return name, op
    return None, ""


class AssetsAPIView(ModuleSecuredView):
    """Secured base for every assets endpoint (ADR-004 SEC-VIEW); each handler authorizes its permission code."""

    def company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        ctx = self.ctx
        raw = request.headers.get("X-Company-Id")
        if raw:
            return ctx, ctx.require_company(raw)
        if len(ctx.company_ids) == 1:
            return ctx, next(iter(ctx.company_ids))
        raise ValidationFailed("X-Company-Id header required", pointer="/headers/X-Company-Id", reason="required")
