"""Shared API plumbing for inventory views (ADR-004): secured base, company header, policy, idempotent commands.

* Authentication / context / throttling / forced RLS come from :class:`iam.api.base.ModuleSecuredView`.
* Every handler authorizes its own permission code through the registered ``PolicyService`` (deny by
  default) and lists through ``scope_queryset`` (company + branch/warehouse scopes + row rules).
* Responses go through the field policy (``serialize``): confidential cost fields are omitted for roles
  without that classification.
* Every state change requires ``Idempotency-Key`` and runs in one unit of work (replays return the stored body).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Model, QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView, check_if_match
from iam.errors import IdempotencyKeyRequired
from kernel import services
from kernel.errors import NotFound, PermissionDenied, ValidationFailed
from kernel.idempotency import StoredResponse, validate_key
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work


class PolicyDenied(PermissionDenied):
    code, title = "policy_denied", "Denied by access policy"


def policy() -> PolicyService:
    return services.get(PolicyService)


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    if not policy().authorize(ctx, action, resource).allowed:
        raise PolicyDenied(f"missing permission {action}")


def body(request: Request) -> dict[str, Any]:
    data = request.data
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed("request body must be a JSON object", pointer="/", reason="type")
    return dict(data)


def decimal(value: Any, field: str, *, required: bool = True) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{field} is required", pointer=f"/{field}", reason="required")
        return None
    if isinstance(value, (float, bool)):
        raise ValidationFailed("decimals are strings (never JSON numbers)", pointer=f"/{field}", reason="type")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"{field} must be a decimal string", pointer=f"/{field}", reason="format") from None
    if not out.is_finite():
        raise ValidationFailed(f"{field} must be finite", pointer=f"/{field}", reason="format")
    return out


def uuid_or_none(value: Any, field: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be a UUID", pointer=f"/{field}", reason="format") from None


def money(value: Decimal | None) -> dict[str, str] | None:
    return None if value is None else {"amount": format(value, "f"), "currency": "COP"}


def qty(value: Decimal | None, uom: str) -> dict[str, str] | None:
    return None if value is None else {"value": format(value, "f"), "uom": uom}


def s(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


class InventoryView(ModuleSecuredView):
    """Secured base for every inventory endpoint."""

    #: permission prefix ``inventory.<resource>``
    perm: str = ""

    def company(self, request: Request) -> uuid.UUID:
        ctx = self.ctx
        raw = request.headers.get("X-Company-Id")
        if raw:
            return ctx.require_company(raw)
        if len(ctx.company_ids) == 1:
            return next(iter(ctx.company_ids))
        raise ValidationFailed("X-Company-Id header required", pointer="/headers/X-Company-Id", reason="required")

    def allow(self, verb: str, resource: Any = None) -> None:
        require(self.ctx, f"{self.perm}.{verb}", resource)

    def scoped(self, verb: str, qs: QuerySet[Any], company_id: uuid.UUID) -> QuerySet[Any]:
        self.allow(verb)
        out: QuerySet[Any] = policy().scope_queryset(self.ctx, f"{self.perm}.{verb}", qs.filter(company_id=company_id))
        return out

    def one(self, verb: str, qs: QuerySet[Any], company_id: uuid.UUID, pk: Any) -> Any:
        try:
            pk = uuid.UUID(str(pk))
        except ValueError:
            raise NotFound("not found") from None
        obj = self.scoped(verb, qs, company_id).filter(pk=pk).first()
        if obj is None:
            raise NotFound("not found")
        return obj

    def if_match(self, request: Request, obj: Model) -> None:
        check_if_match(request, self.resource_type, obj, required=True)

    def command(self, request: Request, scope: str, fn: Callable[[UnitOfWork], tuple[Any, int]]) -> Response:
        key = request.headers.get("Idempotency-Key")
        if not key:
            raise IdempotencyKeyRequired("Idempotency-Key header required")
        key = validate_key(key)
        payload = {"path": request.path, "body": request.data if request.data else None}

        def run(uow: UnitOfWork) -> StoredResponse:
            data, status = fn(uow)
            uow.set_response(data, status)
            return StoredResponse(body=data, status_code=status)

        result = run_in_unit_of_work(run, self.ctx, idempotency_key=key, request=payload, scope=scope)
        if not isinstance(result, StoredResponse):  # pragma: no cover - run() always returns one
            raise ValidationFailed("unexpected command result")
        resp = Response(result.body, status=result.status_code)
        resp["Idempotent-Replayed"] = "false"
        if isinstance(result.body, dict) and "id" in result.body and "version" in result.body:
            from iam.api.base import etag_for

            resp["ETag"] = etag_for(self.resource_type, result.body["id"], result.body["version"])
        return resp

    def etagged(self, data: dict[str, Any], obj: Any) -> Response:
        from iam.api.base import etag_for

        resp = Response(data)
        if getattr(obj, "version", None) is not None:
            resp["ETag"] = etag_for(self.resource_type, obj.pk, obj.version)
        return resp
