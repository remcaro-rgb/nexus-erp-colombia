"""Secured API base (ADR-004): every IAM endpoint goes through these helpers.

* authentication → ``TenantContext`` activated for the handler;
* ``permission`` per HTTP method checked with :mod:`iam.policy` (deny by default);
* lists: ``scope_queryset`` + cursor pagination (UUIDv7 order) + ``q`` search +
  ``filter[field][op]=value`` (unknown → ``filter_invalid``, non-readable → ``filter_not_allowed``);
* responses: field mask applied (unreadable fields omitted, masked fields masked);
* writes: ``Idempotency-Key`` required, ``run_in_unit_of_work`` (replays return the stored body),
  non-writable input fields → ``field_forbidden``;
* foreign / out-of-scope ids → 404 (never 403: no enumeration oracle).
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any, ClassVar

from django.db.models import Model, Q, QuerySet
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from kernel import tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.idempotency import StoredResponse
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

from iam.authn import InternalAuthentication
from iam.errors import FieldForbidden, FilterInvalid, FilterNotAllowed, IdempotencyKeyRequired, RateLimited, Unauthenticated
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine

MAX_LIMIT = 500
DEFAULT_LIMIT = 50
FILTER_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "between", "isnull")


class HasContext(BasePermission):
    def has_permission(self, request: Request, view: Any) -> bool:
        if getattr(request, "erp_context", None) is None:
            raise Unauthenticated("authentication required")
        return True


class PrincipalThrottle(SimpleRateThrottle):
    """Per-principal rate limit (API4); tenant-wide limit below."""

    rate = "600/min"
    scope_name = "principal"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        ctx = getattr(request, "erp_context", None)
        return f"iam:throttle:{self.scope_name}:{ctx.principal_id}" if ctx else None


class TenantThrottle(PrincipalThrottle):
    rate = "6000/min"
    scope_name = "tenant"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        ctx = getattr(request, "erp_context", None)
        return f"iam:throttle:{self.scope_name}:{ctx.tenant_id}" if ctx else None


def parse_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise NotFound("not found") from exc


def to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


class SecuredView(APIView):
    authentication_classes: ClassVar[list[Any]] = [InternalAuthentication]
    permission_classes: ClassVar[list[Any]] = [HasContext]
    throttle_classes: ClassVar[list[Any]] = [PrincipalThrottle, TenantThrottle]
    #: HTTP method → permission code
    permissions: ClassVar[dict[str, str]] = {}
    #: resource type for field policies (``<module>.<resource>``)
    resource_type: ClassVar[str] = ""
    #: query parameters validated elsewhere (not filters)
    ignored_params: ClassVar[tuple[str, ...]] = ()

    _ctx_stack: contextlib.ExitStack | None = None

    # ---------------------------------------------------------------- lifecycle
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        self._ctx_stack = contextlib.ExitStack()
        self._ctx_stack.enter_context(tenancy.activate(self.ctx))
        self.check_access(request)

    def check_access(self, request: Request) -> None:
        code = self.permissions.get(request.method or "")
        if code is None:
            raise MethodNotAllowed(request.method or "")
        get_engine().require(self.ctx, code)

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        if self._ctx_stack is not None:
            self._ctx_stack.close()
            self._ctx_stack = None
        return super().finalize_response(request, response, *args, **kwargs)

    def throttled(self, request: Request, wait: float | None) -> None:
        raise RateLimited("too many requests")

    @property
    def ctx(self) -> TenantContext:
        ctx = getattr(self.request, "erp_context", None)
        if ctx is None:
            raise Unauthenticated("authentication required")
        return ctx  # type: ignore[no-any-return]

    # ---------------------------------------------------------------- reads
    def field_view(self) -> tuple[set[str], set[str], dict[str, str]]:
        """(readable, writable, masks) for this view's resource and principal."""
        return get_engine().field_rules(self.ctx, self.resource_type)

    def serialize(self, obj: Model | Mapping[str, Any], fields: Iterable[str] | None = None) -> dict[str, Any]:
        readable, _w, masks = self.field_view()
        data = obj if isinstance(obj, Mapping) else self.to_dict(obj)
        wanted = set(fields) if fields else None
        return {
            k: field_registry.apply_mask(masks.get(k, "none"), to_json(v))
            for k, v in data.items()
            if k in readable and (wanted is None or k in wanted or k == "id")
        }

    def to_dict(self, obj: Model) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def get_scoped(self, queryset: QuerySet[Any], pk: Any, action: str) -> Any:
        obj = get_engine().scope_queryset(self.ctx, action, queryset).filter(pk=parse_uuid(pk)).first()
        if obj is None:
            raise NotFound("not found")
        return obj

    def paginate(
        self,
        request: Request,
        queryset: QuerySet[Any],
        *,
        search_fields: Iterable[str] = (),
        filter_fields: Mapping[str, str] | None = None,
        sort_fields: Iterable[str] = (),
    ) -> Response:
        filter_fields = dict(filter_fields or {})
        readable, _w, masks = self.field_view()
        queryset = self._apply_filters(request, queryset, filter_fields, readable, masks)
        q = (request.query_params.get("q") or "").strip()
        if q:
            if len(q) > 200:
                raise FilterInvalid("q too long")
            cond = Q(pk__in=[])
            for f in search_fields:
                if f in readable and f not in masks:
                    cond |= Q(**{f"{f}__icontains": q})
            queryset = queryset.filter(cond)
        order = ["id"]
        sort = request.query_params.get("sort")
        if sort:
            order = []
            for part in sort.split(","):
                name = part.strip().lstrip("-")
                if name not in sort_fields:
                    raise FilterInvalid(f"cannot sort by {name}")
                if name not in readable or name in masks:
                    raise FilterNotAllowed(f"cannot sort by {name}")
                order.append(part.strip())
            order.append("id")
        try:
            limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
        except ValueError as exc:
            raise FilterInvalid("limit must be an integer") from exc
        if not 1 <= limit <= MAX_LIMIT:
            raise FilterInvalid("limit must be between 1 and 500")
        offset = 0
        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
            except (ValueError, UnicodeDecodeError) as exc:
                raise FilterInvalid("invalid cursor") from exc
            if offset < 0:
                raise FilterInvalid("invalid cursor")
        fields_param = request.query_params.get("fields")
        fields = [f.strip() for f in fields_param.split(",")] if fields_param else None
        with tenancy.scope(self.ctx):
            rows = list(queryset.order_by(*order)[offset : offset + limit + 1])
            items = [self.serialize(r, fields) for r in rows[:limit]]
        next_cursor = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if len(rows) > limit else None
        return Response({"items": items, "next_cursor": next_cursor})

    def _apply_filters(
        self,
        request: Request,
        queryset: QuerySet[Any],
        filter_fields: Mapping[str, str],
        readable: set[str],
        masks: Mapping[str, str],
    ) -> QuerySet[Any]:
        for key, value in request.query_params.items():
            if key in ("q", "sort", "limit", "cursor", "fields") or key in self.ignored_params:
                continue
            name, op = key, "eq"
            if key.startswith("filter[") and key.endswith("]"):
                inner = key[len("filter[") : -1]
                name, _, op = inner.partition("][")
                op = op or "eq"
            elif "[" in key and key.endswith("]"):
                name, _, op = key[:-1].partition("[")
            if op not in FILTER_OPS or name not in filter_fields:
                raise FilterInvalid(f"unknown filter {key}")
            if name not in readable or name in masks:
                raise FilterNotAllowed(f"cannot filter on {name}")
            column = filter_fields[name]
            queryset = self._filter_one(queryset, column, op, value)
        return queryset

    @staticmethod
    def _filter_one(queryset: QuerySet[Any], column: str, op: str, value: str) -> QuerySet[Any]:
        from django.core.exceptions import FieldDoesNotExist
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            model_field = queryset.model._meta.get_field(column)
        except FieldDoesNotExist as exc:
            raise FilterInvalid(f"unknown filter {column}") from exc

        def conv(raw: str) -> Any:
            if model_field.get_internal_type() == "BooleanField":
                if raw.lower() not in ("true", "false"):
                    raise FilterInvalid(f"{column} expects true or false")
                return raw.lower() == "true"
            try:
                return model_field.to_python(raw)
            except DjangoValidationError as exc:
                raise FilterInvalid(f"invalid value for {column}") from exc

        if op == "isnull":
            if value.lower() not in ("true", "false"):
                raise FilterInvalid("isnull expects true or false")
            return queryset.filter(**{f"{column}__isnull": value.lower() == "true"})
        if op == "in":
            return queryset.filter(**{f"{column}__in": [conv(v) for v in value.split(",") if v]})
        if op == "between":
            low, _, high = value.partition(",")
            return queryset.filter(**{f"{column}__gte": conv(low), f"{column}__lte": conv(high)})
        if op == "contains":
            return queryset.filter(**{f"{column}__icontains": value})
        if op == "ne":
            return queryset.exclude(**{column: conv(value)})
        lookup = "exact" if op == "eq" else op
        return queryset.filter(**{f"{column}__{lookup}": conv(value)})

    # ---------------------------------------------------------------- writes
    def check_writable(self, payload: Mapping[str, Any], allowed: Iterable[str]) -> None:
        allowed = set(allowed)
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationFailed("unknown fields", fields=sorted(unknown))
        _r, writable, _m = self.field_view()
        forbidden = set(payload) - writable
        if forbidden:
            raise FieldForbidden("fields not writable", fields=sorted(forbidden))

    def mutate(self, request: Request, fn: Callable[[UnitOfWork], tuple[Any, int]], scope: str) -> Response:
        key = request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not key:
            raise IdempotencyKeyRequired("Idempotency-Key header required")
        payload = request.data if isinstance(request.data, Mapping) else {}
        body = {"path": request.path, "body": dict(payload)}

        def run(uow: UnitOfWork) -> StoredResponse:
            data, status = fn(uow)
            uow.set_response(data, status)
            return StoredResponse(body=data, status_code=status)

        result = run_in_unit_of_work(run, self.ctx, idempotency_key=key, request=body, scope=scope)
        assert isinstance(result, StoredResponse)  # noqa: S101 - run() always returns one
        response = Response(result.body, status=result.status_code)
        if isinstance(result.body, dict) and "version" in result.body and "id" in result.body:
            response["ETag"] = etag_for(self.resource_type, result.body["id"], result.body["version"])
        return response


def etag_for(resource_type: str, pk: Any, version: Any) -> str:
    import hashlib

    raw = f"{resource_type}:{pk}:{version}".encode()
    return 'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"'


def check_if_match(request: Request, resource_type: str, obj: Any, required: bool) -> None:
    from kernel.errors import PreconditionFailed, PreconditionRequired

    header = request.META.get("HTTP_IF_MATCH")
    if not header:
        if required:
            raise PreconditionRequired("If-Match header required")
        return
    candidates = {c.strip() for c in header.split(",")}
    if "*" not in candidates and etag_for(resource_type, obj.pk, obj.version) not in candidates:
        raise PreconditionFailed("the resource changed since it was read", code="version_conflict")
