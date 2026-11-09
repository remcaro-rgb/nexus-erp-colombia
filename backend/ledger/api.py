"""Shared API plumbing for ledger views: context, company, policy, idempotency, ETags.

* The tenant context comes from the authentication layer (P04): ``request.auth``
  when it is a :class:`TenantContext`, else ``request.erp_context`` / the
  middleware-activated context. No context → 401 (deny by default).
* Every action is authorized through the registered :class:`PolicyService`
  (fails closed with ``service_unavailable`` when none is registered).
* Every state change requires an ``Idempotency-Key`` and runs in one unit of work.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from django.db.models import QuerySet
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from kernel import services, tenancy
from kernel.errors import NotAuthenticated, PermissionDenied, ValidationFailed
from kernel.idempotency import StoredResponse, validate_key
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work


class IdempotencyKeyRequired(ValidationFailed):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class PolicyDenied(PermissionDenied):
    code, title = "policy_denied", "Denied by access policy"


def request_context(request: Request) -> TenantContext:
    auth = getattr(request, "auth", None)
    if isinstance(auth, TenantContext):
        return auth
    ctx = getattr(request._request, "erp_context", None) or tenancy.current()
    if not isinstance(ctx, TenantContext):
        raise NotAuthenticated("no tenant context for this principal")
    return ctx


def request_company(request: Request, ctx: TenantContext) -> uuid.UUID:
    raw = request.headers.get("X-Company-Id")
    if raw:
        return ctx.require_company(raw)
    if len(ctx.company_ids) == 1:
        return next(iter(ctx.company_ids))
    raise ValidationFailed("X-Company-Id header required", pointer="/headers/X-Company-Id", reason="required")


def policy() -> PolicyService:
    return services.get(PolicyService)


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    decision = policy().authorize(ctx, action, resource)
    if not decision.allowed:
        raise PolicyDenied(f"missing permission {action}")


def scoped(ctx: TenantContext, action: str, qs: QuerySet[Any]) -> QuerySet[Any]:
    require(ctx, action)
    return policy().scope_queryset(ctx, action, qs)


def idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    return validate_key(key)


def respond(body: Any, status: int = 200, etag: str | None = None, replayed: bool = False) -> Response:
    resp = Response(body, status=status)
    if etag:
        resp["ETag"] = etag
    resp["Idempotent-Replayed"] = "true" if replayed else "false"
    return resp


def command(
    request: Request,
    ctx: TenantContext,
    scope: str,
    fn: Callable[[UnitOfWork], tuple[Any, int, str | None]],
) -> Response:
    """Run ``fn`` in one UoW with the request's Idempotency-Key; replays return the stored body."""
    key = idempotency_key(request)
    payload = {"path": request.path, "body": request.data if request.data else None}
    holder: dict[str, Any] = {}

    def run(uow: UnitOfWork) -> Any:
        body, status, etag = fn(uow)
        uow.set_response({"body": body, "etag": etag}, status)
        holder["etag"] = etag
        return body, status

    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=payload, scope=scope)
    if isinstance(result, StoredResponse):
        stored = result.body or {}
        return respond(stored.get("body"), result.status_code, stored.get("etag"), replayed=True)
    body, status = result
    return respond(body, status, holder.get("etag"))


class LedgerAPIView(APIView):  # type: ignore[misc]
    permission_classes = [IsAuthenticated]

    def ctx_company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        ctx = request_context(request)
        return ctx, request_company(request, ctx)
