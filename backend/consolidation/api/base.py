"""API plumbing of ``/api/v1/consolidation/…`` and ``/api/v1/intercompany/…`` (contracts consolidation.yaml and
intercompany.yaml 1.0.0, consumed unchanged).

* Every view inherits :class:`iam.api.base.ModuleSecuredView` (ADR-004 SEC-VIEW): IAM authentication, throttling,
  the request's tenant context bound for the whole handler (forced RLS).
* Every handler authorizes its own ``x-permission`` code through the registered ``PolicyService`` for **every
  company** the tenant-level resource touches (deny by default; resources with a hidden company → 404).
* Every state change requires an ``Idempotency-Key`` and runs in one unit of work (replays return the stored body).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from django.http import HttpResponse
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.response import Response

from consolidation import reports
from consolidation.errors import FilterInvalid
from iam.api.base import ModuleSecuredView
from kernel.errors import NotFound, PreconditionFailed, ValidationFailed
from kernel.events import to_jsonable
from kernel.idempotency import StoredResponse, validate_key
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT, MAX_LIMIT = 50, 500


class IdempotencyKeyRequired(ValidationFailed):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class VersionConflict(PreconditionFailed):
    code = "version_conflict"


def idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    return validate_key(key)


def check_if_match(request: Request, obj: Any, required: bool) -> None:
    header = request.headers.get("If-Match")
    if not header:
        if required:
            from kernel.errors import PreconditionRequired

            raise PreconditionRequired("If-Match header required")
        return
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


def opt_uuid(request: Request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise FilterInvalid(f"{name} must be a UUID") from None


def body_of(request: Request, allowed: Iterable[str]) -> dict[str, Any]:
    data = request.data if request.data is not None else {}
    if not isinstance(data, dict):
        raise ValidationFailed("a JSON object is required", pointer="/", reason="type")
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown field(s): {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="unknown")
    return dict(data)


def paginate(
    request: Request,
    items: Sequence[Any],
    to_json: Callable[[Any], dict[str, Any]],
    filters: dict[str, Callable[[Any], Any]],
) -> Response:
    """Cursor paging over an already policy-filtered list (tenant-level resources are few)."""
    params = request.query_params
    rows = list(items)
    for raw_key, value in params.items():
        name = raw_key[len("filter[") : -1].split("][")[0] if raw_key.startswith("filter[") else None
        if name is None:
            continue
        if name not in filters:
            raise FilterInvalid(f"unknown filter {raw_key}")
        rows = [r for r in rows if str(filters[name](r)) == value]
    q = (params.get("q") or "").strip().lower()
    if q:
        if len(q) > 200:
            raise FilterInvalid("q too long")
        rows = [
            r
            for r in rows
            if q in f"{getattr(r, 'code', '')} {getattr(r, 'name', '')} {getattr(r, 'number', '')}".lower()
        ]
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
    page = rows[offset : offset + limit]
    more = len(rows) > offset + limit
    fields = {f.strip() for f in (params.get("fields") or "").split(",") if f.strip()}
    out = []
    for obj in page:
        item = to_json(obj)
        if fields:
            item = {k: v for k, v in item.items() if k in fields or k == "id"}
        out.append(item)
    next_cursor = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if more else None
    return respond({"items": out, "next_cursor": next_cursor})


def report_response(request: Request, body: dict[str, Any], filename: str) -> Response | HttpResponse:
    fmt = (request.query_params.get("format") or "json").lower()
    if fmt == "json":
        return respond(body)
    if fmt == "csv":
        resp = HttpResponse(reports.to_csv(body), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        return resp
    raise FilterInvalid("format must be json or csv (xlsx/pdf are rendered by the reporting module)")


class _NoUrlFormatOverride:
    URL_FORMAT_OVERRIDE = None


class ContractNegotiation(DefaultContentNegotiation):  # type: ignore[misc]  # DRF ships no type stubs (Any)
    """The contract's ``format`` query parameter selects the export file (handled by :func:`report_response`),
    not a DRF renderer: DRF's URL format override is disabled for this module."""

    settings = _NoUrlFormatOverride()


class ConsolidationAPIView(ModuleSecuredView):
    """Secured base for every endpoint of this module; each handler authorizes its permission code."""

    content_negotiation_class = ContractNegotiation
