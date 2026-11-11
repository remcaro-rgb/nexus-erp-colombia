"""Customer portal API (``/api/portal/v1/customer/*``, F-PORTAL-CUS; contract ``portal.yaml``; owner P10, part P10b)
and its back-office side under ``/api/v1/sales/`` (portal requests, payment intents).

Security (D-018, D-033, F-PORTAL-CORE) through :class:`iam.external.portal.PortalView`: ``erp-portal`` tokens of
ACTIVE customer accounts only (``portal_kind = "customer"``); the party and company are the account's, never a value
of the request (scope ids in the query/body are refused, ``external_account_scope_violation``). Reads filter by
that party in the repository layer (:mod:`sales.portal.service`); a foreign id is a 404 (no existence oracle).
Responses are field allowlists (``portal_fields``): never other customers' prices, costs, margins, price sources,
snapshots or formulas (T-PORTAL-CUS-04). Every write is idempotent (``Idempotency-Key``) and audited; every
download is audited. Rate limits: the per-principal/tenant throttles of the secured base.
"""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from django.http import HttpResponse
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import PrincipalThrottle, TenantThrottle, to_json
from iam.external.portal import PortalView, own_account_id
from kernel import clock, tenancy
from kernel.errors import ValidationFailed
from kernel.uow import UnitOfWork, unit_of_work
from sales.api.base import SalesView, action_body, command, page
from sales.interfaces import PERM_CREDIT_NOTE, PERM_INVOICE, PERM_ORDER, PERM_QUOTE, PERM_RECEIPT
from sales.portal import documents, service
from sales.portal.models import CustomerRequest, PaymentIntent
from sales.services.common import audit

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DOC_FIELDS = ("id", "tenant_id", "status", "number", "total", "valid_until", "quote_status", "version", "audit")


class FilterInvalid(ValidationFailed):
    code, status, title = "filter_invalid", 400, "Invalid filter"


class _JsonOnlyNegotiation(DefaultContentNegotiation):  # type: ignore[misc]  # DRF ships no type stubs
    """``?format=csv|pdf`` is the contract's export parameter, not DRF's renderer override."""

    def select_renderer(self, request: Request, renderers: Sequence[Any], format_suffix: str | None = None) -> Any:
        return renderers[0], renderers[0].media_type


def _audit(view: PortalView, action: str, resource: str, obj_id: Any) -> None:
    """Audit of an access (downloads, statement); writes nothing else."""
    portal = view.effective.portal
    if portal is None:
        return
    with unit_of_work(view.ctx) as uow:
        audit(uow, action, resource, obj_id, portal.company_id, {"channel": "portal"})


def _file(content: bytes, media_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type=media_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    response["X-Fiscal-Validity"] = "SINTETICO - sin validez fiscal"
    return response


class CustomerPortalThrottle(PrincipalThrottle):
    """Per-account rate limit of the customer portal (own bucket: F-PORTAL-CUS "rate limits per account")."""

    scope_name = "portal_customer"


class CustomerPortalView(PortalView):
    portal_kind = "customer"
    throttle_classes: ClassVar[list[Any]] = [CustomerPortalThrottle, TenantThrottle]
    permissions: ClassVar[dict[str, str]] = {}
    portal_fields: ClassVar[tuple[str, ...]] = DOC_FIELDS

    def scope(self) -> service.Scope:
        portal = self.effective.portal
        assert portal is not None  # noqa: S101 - PortalView.check_access guarantees it
        if portal.party_id is None:
            raise service.PortalDocumentNotOwned("the account has no customer party")
        return service.Scope(self.ctx, portal.company_id, portal.party_id, own_account_id(self))

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return dict(obj) if isinstance(obj, Mapping) else {}

    def page_list(self, request: Request, items: Sequence[Mapping[str, Any]]) -> Response:
        """Cursor pagination over an already party-scoped list (no free filters on portal lists)."""
        for key in request.query_params:
            if key not in ("cursor", "limit", "fields", *self.ignored_params):
                raise FilterInvalid(f"unknown filter {key}")
        try:
            limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
            raw = request.query_params.get("cursor")
            offset = int(base64.urlsafe_b64decode(raw.encode()).decode()) if raw else 0
        except (ValueError, UnicodeDecodeError) as exc:
            raise FilterInvalid("invalid cursor or limit") from exc
        if not 1 <= limit <= MAX_LIMIT or offset < 0:
            raise FilterInvalid(f"limit must be between 1 and {MAX_LIMIT}")
        chunk = items[offset : offset + limit]
        nxt = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if len(items) > offset + limit else None
        fields_param = request.query_params.get("fields")
        fields = [f.strip() for f in fields_param.split(",")] if fields_param else None
        return Response({"items": [self.serialize(i, fields) for i in chunk], "next_cursor": nxt})

    def body(self, request: Request) -> dict[str, Any]:
        data = request.data if isinstance(request.data, Mapping) else {}
        return {k: v for k, v in data.items() if k not in self.ignored_params}


class _DocList(CustomerPortalView):
    """List + detail of a P10a projection (functions of :mod:`sales.portal.service`)."""

    lister: ClassVar[Any] = None
    getter: ClassVar[Any] = None

    def get(self, request: Request, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            scope = self.scope()
            if pk is None:
                return self.page_list(request, type(self).lister(scope))
            return Response(self.serialize(type(self).getter(scope, pk)))


class QuotesView(_DocList):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_QUOTE}
    lister, getter = service.quotes, service.quote


class OrdersView(_DocList):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_ORDER}
    lister, getter = service.orders, service.order


class InvoicesView(_DocList):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_INVOICE}
    lister, getter = service.invoices, service.invoice


class CreditNotesView(_DocList):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_CREDIT_NOTE}
    lister, getter = service.credit_notes, service.credit_note


class ReceiptsView(_DocList):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_RECEIPT}
    lister, getter = service.receipts, service.receipt


class OpenItemsView(CustomerPortalView):
    """``GET /open-items`` — own open invoices (number, due date, balance) to choose what to pay (CCR P10b-2)."""

    permissions: ClassVar[dict[str, str]] = {"GET": PERM_INVOICE}
    portal_fields = ("document", "due_date", "balance")

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            return self.page_list(request, service.open_items(self.scope()))


class QuoteAcceptView(CustomerPortalView):
    """``POST /quotes/{id}:accept`` — the customer accepts a SENT quote → DRAFT sales order (CCR P10b-2)."""

    permissions: ClassVar[dict[str, str]] = {"POST": service.PERM_QUOTE_ACCEPT}
    portal_fields = ("status", "resource", "data", "warnings")

    def post(self, request: Request, pk: str) -> Response:
        data = self.body(request)
        unknown = sorted(set(data) - {"reason", "comment", "parameters"})
        if unknown:
            raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            scope = self.scope()
            q, order = service.accept_quote(uow, scope, pk)
            return {
                "status": "ACCEPTED",
                "resource": {"type": "sales.order", "id": str(order.id), "number": order.number},
                "data": {"quote": service.quote(scope, q.id)},
                "warnings": [],
            }, 200

        return self.mutate(request, run, scope=service.PERM_QUOTE_ACCEPT)


class _FileView(CustomerPortalView):
    kind: ClassVar[str] = "invoice"
    media: ClassVar[str] = "pdf"

    def get(self, request: Request, pk: str) -> HttpResponse:
        with tenancy.scope(self.ctx):
            scope = self.scope()
            if self.kind == "invoice":
                row: Any = service.own_invoice_row(scope, pk)
                data = documents.invoice_data(row)
                resource = "sales.invoice"
            else:
                row = service.own_note_row(scope, pk)
                data = documents.note_data(row)
                resource = "sales.credit_note"
            content = documents.document_pdf(data) if self.media == "pdf" else documents.document_xml(data)
        _audit(self, f"portal.customer.{self.kind}_{self.media}_downloaded", resource, row.id)
        if self.media == "pdf":
            return _file(content, "application/pdf", f"{row.number}-SINTETICO.pdf")
        return _file(content, "application/xml", f"{row.number}-SINTETICO.xml")


class InvoicePdfView(_FileView):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_INVOICE}
    kind, media = "invoice", "pdf"


class InvoiceXmlView(_FileView):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_INVOICE}
    kind, media = "invoice", "xml"


class CreditNotePdfView(_FileView):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_CREDIT_NOTE}
    kind, media = "credit_note", "pdf"


class CreditNoteXmlView(_FileView):
    permissions: ClassVar[dict[str, str]] = {"GET": PERM_CREDIT_NOTE}
    kind, media = "credit_note", "xml"


def _date(raw: str | None, name: str, default: dt.date) -> dt.date:
    if not raw:
        return default
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise FilterInvalid(f"{name} must be a date YYYY-MM-DD") from exc


class StatementView(CustomerPortalView):
    """``GET /statement?from=&to=&format=json|csv|pdf`` — the customer's account statement (ReportResult)."""

    permissions: ClassVar[dict[str, str]] = {"GET": service.PERM_STATEMENT}
    portal_fields = ("report", "parameters", "columns", "rows", "totals", "warnings", "generated_at")
    ignored_params = (*PortalView.ignored_params, "from", "to", "format")
    content_negotiation_class = _JsonOnlyNegotiation

    def get(self, request: Request) -> Any:
        today = clock.business_date()
        date_to = _date(request.query_params.get("to"), "to", today)
        date_from = _date(request.query_params.get("from"), "from", date_to.replace(month=1, day=1))
        fmt = (request.query_params.get("format") or "json").lower()
        if fmt not in ("json", "csv", "pdf"):
            raise FilterInvalid("format must be json, csv or pdf")
        with tenancy.scope(self.ctx):
            scope = self.scope()
            report = service.statement(scope, date_from, date_to)
            customer = service.party_of(scope).legal_name
        _audit(self, f"portal.customer.statement_{fmt}", "sales.customer_statement", scope.party_id)
        name = f"estado-cuenta-{date_from.isoformat()}-{date_to.isoformat()}"
        if fmt == "csv":
            return _file(documents.statement_csv(report), "text/csv; charset=utf-8", f"{name}.csv")
        if fmt == "pdf":
            return _file(documents.statement_pdf(report, customer), "application/pdf", f"{name}.pdf")
        return Response(self.serialize(report))


class PaymentIntentsView(CustomerPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": service.PERM_INTENT_READ,
        "POST": service.PERM_INTENT_CREATE,
    }
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "documents",
        "amount",
        "intent_status",
        "provider",
        "version",
        "audit",
    )

    def get(self, request: Request, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            qs = service.intents(self.scope())
            if pk is None:
                return self.page_list(request, [service.intent_json(p) for p in qs.order_by("-created_at", "-id")])
            row = qs.filter(id=service._uuid(pk)).first()
            if row is None:
                raise service.PortalDocumentNotOwned("not found")
            return Response(self.serialize(service.intent_json(row)))

    def post(self, request: Request, pk: str | None = None) -> Response:
        data = self.body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            pi = service.create_intent(uow, self.scope(), data)
            return to_json(self.serialize(service.intent_json(pi))), 201

        response = self.mutate(request, run, scope=service.PERM_INTENT_CREATE)
        if response.status_code == 201 and isinstance(response.data, dict):
            response["Location"] = f"/api/portal/v1/customer/payment-intents/{response.data['id']}"
        return response


class PaymentIntentSimulateView(CustomerPortalView):
    """``POST /payment-intents/{id}:simulate`` — DEV simulated provider result (CCR P10b-2); never posts."""

    permissions: ClassVar[dict[str, str]] = {"POST": service.PERM_INTENT_CREATE}
    portal_fields = PaymentIntentsView.portal_fields

    def post(self, request: Request, pk: str) -> Response:
        data = self.body(request)
        unknown = sorted(set(data) - {"reason", "comment", "parameters"})
        if unknown:
            raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
        params = data.get("parameters") or {}
        if not isinstance(params, Mapping):
            raise ValidationFailed("parameters must be an object", field="parameters")
        outcome = str(params.get("outcome") or "success")

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            pi = service.simulate_intent(uow, self.scope(), pk, outcome)
            return to_json(self.serialize(service.intent_json(pi))), 200

        return self.mutate(request, run, scope=service.PERM_INTENT_CREATE)


REQUEST_FIELDS = (
    "id",
    "tenant_id",
    "status",
    "number",
    "request_type",
    "message",
    "items",
    "changes",
    "request_status",
    "resolution",
    "quote_id",
    "order_id",
    "version",
    "audit",
)


class RequestsView(CustomerPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": service.PERM_REQUEST_READ,
        "POST": service.PERM_REQUEST_CREATE,
    }
    portal_fields = REQUEST_FIELDS

    def get(self, request: Request, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            qs = service.requests(self.scope())
            if pk is None:
                return self.page_list(request, [service.request_json(r) for r in qs.order_by("-created_at", "-id")])
            row = qs.filter(id=service._uuid(pk)).first()
            if row is None:
                raise service.PortalDocumentNotOwned("not found")
            return Response(self.serialize(service.request_json(row)))

    def post(self, request: Request, pk: str | None = None) -> Response:
        data = self.body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            req = service.create_request(uow, self.scope(), data)
            return to_json(self.serialize(service.request_json(req))), 201

        response = self.mutate(request, run, scope=service.PERM_REQUEST_CREATE)
        if response.status_code == 201 and isinstance(response.data, dict):
            response["Location"] = f"/api/portal/v1/customer/requests/{response.data['id']}"
        return response


class ProfileView(CustomerPortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": service.PERM_PROFILE}
    portal_fields = ("id", "tenant_id", "legal_name", "nit", "email", "version", "audit")

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            return Response(self.serialize(service.profile(self.scope())))


def _period(raw: str | None) -> tuple[dt.date, dt.date]:
    today = clock.business_date()
    if raw:
        try:
            year, month = (int(x) for x in raw.split("-"))
            start = dt.date(year, month, 1)
        except ValueError as exc:
            raise FilterInvalid("period must be YYYY-MM") from exc
    else:
        start = today.replace(day=1)
    nxt = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, nxt - dt.timedelta(days=1)


class KpisView(CustomerPortalView):
    """``GET /kpis?period=YYYY-MM`` — the dashboard KPIs ``cp_*`` (same pattern as CCR P13a-2 / P11b-2)."""

    permissions: ClassVar[dict[str, str]] = {"GET": service.PERM_PROFILE}
    portal_fields = ("as_of", "period", "kpis", "generated_at", "portal")
    ignored_params = (*PortalView.ignored_params, "period", "compare", "portal")

    def get(self, request: Request) -> Response:
        start, end = _period(request.query_params.get("period"))
        prev_end = start - dt.timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        with tenancy.scope(self.ctx):
            scope = self.scope()
            values = service.kpis(scope, start, end)
            previous = service.kpis(scope, prev_start, prev_end)["cp_pagos_periodo"]
        counts = ("cp_facturas_vencidas", "cp_pedidos_en_curso")
        items = []
        for key, value in values.items():
            flow = key == "cp_pagos_periodo"
            items.append(
                {
                    "id": key,
                    "unit": "count" if key in counts else "COP",
                    "value": str(value),
                    "previous": str(previous) if flow else None,
                    "delta": str(value - previous) if flow else None,
                    "state": "ok",
                    "semantics": "flow" if flow else "snapshot",
                    "formula_ref": "module-registry.json › portals › clientes",
                }
            )
        body = {
            "as_of": clock.business_date(),
            "period": {"start": start, "end": end},
            "kpis": items,
            "generated_at": clock.now(),
            "portal": "clientes",
        }
        return Response({k: to_json(v) for k, v in body.items()})


# =========================================================================== internal (back office)


def _internal_request_json(r: CustomerRequest) -> dict[str, Any]:
    out = service.request_json(r)
    out["party"] = {"id": str(r.party_id), "kind": "customer"}
    out["external_account_id"] = str(r.external_account_id)
    return {k: to_json(v) for k, v in out.items()}


def _internal_intent_json(p: PaymentIntent) -> dict[str, Any]:
    out = service.intent_json(p)
    out["party"] = {"id": str(p.party_id), "kind": "customer"}
    out["receipt_id"] = str(p.receipt_id) if p.receipt_id else None
    out["provider_ref"] = p.provider_ref
    return {k: to_json(v) for k, v in out.items()}


class InternalRequestsView(SalesView):
    """``GET /api/v1/sales/portal-requests[/{id}]`` — what customers asked for through the portal."""

    def get(self, request: Request, pk: str | None = None) -> Response:
        ctx = self.context()
        if pk is None:
            filters = {"request_status": "request_status", "request_type": "request_type", "party": "party_id"}
            return page(request, service.internal_requests(ctx), _internal_request_json, filters)
        return Response(_internal_request_json(service.internal_request(ctx, pk)))


class InternalRequestActionView(SalesView):
    """``POST /api/v1/sales/portal-requests/{id}:quote|:approve|:reject``."""

    def post(self, request: Request, pk: str, action: str) -> Response:
        ctx = self.context()
        reason, params = action_body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            if action == "quote":
                req, q = service.quote_request(uow, pk, params)
                data = {
                    "status": req.request_status,
                    "resource": {"type": "sales.quote", "id": str(q.id), "number": q.number},
                    "data": {"request": _internal_request_json(req), "quote_status": q.status},
                    "warnings": [],
                }
                return data, 200
            if action == "approve":
                req = service.approve_request(uow, pk, reason)
            else:
                req = service.reject_request(uow, pk, reason)
            return _internal_request_json(req), 200

        return command(request, ctx, f"sales.portal_request.{action}", run)


class InternalIntentsView(SalesView):
    """``GET /api/v1/sales/payment-intents[/{id}]`` — treasury view of the simulated portal payments."""

    def get(self, request: Request, pk: str | None = None) -> Response:
        ctx = self.context()
        qs = service.internal_intents(ctx)
        if pk is None:
            filters = {"intent_status": "intent_status", "party": "party_id"}
            return page(request, qs, _internal_intent_json, filters)
        row = qs.filter(id=service._uuid(pk)).first()
        if row is None:
            from kernel.errors import NotFound

            raise NotFound("not found")
        return Response(_internal_intent_json(row))
