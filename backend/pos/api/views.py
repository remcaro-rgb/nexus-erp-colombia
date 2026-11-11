"""POS endpoints (contract ``contracts/openapi/modules/pos.yaml``, P12).

The generic resource handlers of the sales API (P10a: deny-by-default policy checks, forced-RLS reads,
``Idempotency-Key`` on every write, ``If-Match`` on PATCH, ETags, keyset paging, 404 for foreign ids) serve the POS
resources with the POS registry below. Row policy: cashiers see only their own sessions, tickets, returns and cash
movements; supervisors (``pos.z_report.read``) see the company's.

Collection actions: ``POST /sessions:open``, ``POST /tickets:checkout`` (the ONLY finalization, ADR-015),
``POST /tickets:preview`` (server recomputation, rolled back). Reports: ``GET /reports/z``, ``GET /catalog-snapshot``
(read-only delta sync with ETag / If-None-Match → 304).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response

from kernel.errors import NotFound
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from pos import catalog, kpis
from pos.api import serializers as ser
from pos.models import CashMovement, CashSession, PosConfig, PosReturn, Terminal, Ticket
from pos.services import checkout, returns, sessions, setup
from pos.services.common import is_supervisor, visible
from sales.api.base import SalesView, action_body, command
from sales.api.views import ActionView, CollectionView, ItemView, Resource, rolled_back
from sales.services.common import get_scoped, require


def _own(model: Any, field: str = "cashier_id") -> Any:
    def base(ctx: TenantContext) -> Any:
        qs = model.objects.all()
        return qs if is_supervisor(ctx) else qs.filter(**{field: ctx.principal_id})

    return base


RESOURCES: dict[str, Resource] = {
    "configs": Resource(
        "pos.config",
        PosConfig,
        ser.config_json,
        "pos.config.read",
        create=setup.create_config,
        update=setup.update_config,
        delete=setup.delete_config,
        filters={"name": "name", "branch_id": "branch_id", "warehouse_id": "warehouse_id", "active": "active"},
    ),
    "terminals": Resource(
        "pos.terminal",
        Terminal,
        ser.terminal_json,
        "pos.terminal.read",
        create=setup.create_terminal,
        update=setup.update_terminal,
        delete=setup.delete_terminal,
        actions={"register": lambda uow, t, r, p: setup.register_terminal(uow, t, p)},
        filters={"code": "code", "config_id": "config_id", "status": "status"},
    ),
    "sessions": Resource(
        "pos.session",
        CashSession,
        ser.session_json,
        "pos.session.read",
        create=sessions.open_session,
        actions={"close": sessions.close_session},
        filters={"status": "status", "terminal_id": "terminal_id", "cashier_id": "cashier_id", "number": "number"},
        base=_own(CashSession),
    ),
    "tickets": Resource(
        "pos.ticket",
        Ticket,
        ser.ticket_json,
        "pos.ticket.read",
        create=checkout.park,
        update=checkout.update_draft,
        delete=checkout.delete_draft,
        actions={"to-invoice": checkout.to_invoice},
        filters={"status": "status", "session_id": "session_id", "number": "number", "client_uuid": "client_uuid"},
        base=lambda ctx: _own(Ticket)(ctx).select_related("invoice_request"),
    ),
    "returns": Resource(
        "pos.return",
        PosReturn,
        ser.return_json,
        "pos.return.read",
        create=returns.create_return,
        update=returns.update_return,
        delete=returns.delete_return,
        actions={
            "post": lambda uow, x, r, p: returns.post_return(uow, x),
            "simulate": lambda uow, x, r, p: returns.simulate_return(uow, x),
        },
        filters={"status": "status", "session_id": "session_id", "original": "original_id"},
        base=lambda ctx: _own(PosReturn, "session__cashier_id")(ctx).select_related("original"),
        previews=frozenset({"simulate"}),
    ),
    "cash-movements": Resource(
        "pos.cash_movement",
        CashMovement,
        ser.movement_json,
        "pos.cash_movement.read",
        create=sessions.create_movement,
        filters={"session_id": "session_id", "movement_type": "movement_type"},
        base=_own(CashMovement, "session__cashier_id"),
    ),
}

SERIALIZERS: dict[type, Callable[[TenantContext, Any], dict[str, Any]]] = {
    PosConfig: ser.config_json,
    Terminal: ser.terminal_json,
    CashSession: ser.session_json,
    Ticket: ser.ticket_json,
    PosReturn: ser.return_json,
    CashMovement: ser.movement_json,
}


def serialize(ctx: TenantContext, obj: Any) -> Any:
    if isinstance(obj, dict):
        return obj
    for model, fn in SERIALIZERS.items():
        if isinstance(obj, model):
            return fn(ctx, obj)
    raise TypeError(f"no serializer for {type(obj).__name__}")


class PosMixin:
    registry: ClassVar[Mapping[str, Resource]] = RESOURCES
    created_actions: ClassVar[frozenset[str]] = frozenset()

    def serialize_result(self, ctx: TenantContext, obj: Any) -> Any:
        return serialize(ctx, obj)


def views_for(key: str) -> tuple[Any, Any, Any]:
    name = key.replace("-", "_").title()
    attrs: dict[str, Any] = {"key": key}

    def make(kind: str, base: type) -> Any:
        view: Any = type(f"Pos{name}{kind}", (PosMixin, base), attrs)
        return view.as_view()

    return make("Collection", CollectionView), make("Item", ItemView), make("Action", ActionView)


class SessionOpenView(SalesView):
    """``POST /sessions:open`` (ActionRequest ``parameters{terminal_id, opening_cash}``) → PosSession."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        _reason, params = action_body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            return ser.session_json(ctx, sessions.open_session(uow, company, params)), 200

        return command(request, ctx, "pos.session.open", run)


class CheckoutView(SalesView):
    """``POST /tickets:checkout`` — Idempotency-Key = client UUID; one UoW (ADR-015)."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        _reason, params = action_body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            ticket = checkout.checkout(uow, company, params)
            data = ser.ticket_json(ctx, ticket)
            return {
                "status": "POSTED",
                "resource": {"type": "pos.ticket", "id": str(ticket.id), "number": ticket.number},
                "data": data,
                "warnings": [],
            }, 200

        return command(request, ctx, "pos.ticket.checkout", run)


class PreviewView(SalesView):
    """``POST /tickets:preview`` — server price/tax recomputation of a cart; persists nothing (CCR P12-1)."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        _reason, params = action_body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            data = rolled_back(lambda: checkout.preview(uow, company, params))
            return {"status": "OK", "data": data, "warnings": []}, 200

        return command(request, ctx, "pos.ticket.preview", run)


class ZReportView(SalesView):
    """``GET /reports/z?session_id=`` → ReportResult (own session, or any with the supervisor read)."""

    def get(self, request: Request) -> Response:
        ctx, _company = self.ctx_company(request)
        require(ctx, "pos.session.read")
        qs = visible(ctx, "pos.session.read", CashSession.objects.select_related("terminal"))
        session = get_scoped(ctx, "pos.session.read", qs, request.query_params.get("session_id"))
        if session.cashier_id != ctx.principal_id:
            require(ctx, "pos.z_report.read")
        z = session.z_report if session.status == CashSession.Status.CLOSED else sessions.z_report(session)
        if session.status != CashSession.Status.CLOSED and session.blind:
            z = {k: v for k, v in z.items() if k not in ("expected", "counted", "difference")}
        rows = [{"tender": k, "amount": v} for k, v in sorted(z.get("by_tender", {}).items())]
        data = {
            "report": "pos.z",
            "parameters": {"session_id": str(session.id)},
            "columns": [
                {"key": "tender", "label": "Medio de pago", "type": "string"},
                {"key": "amount", "label": "Valor", "type": "money"},
            ],
            "rows": rows,
            "totals": ser.masked(ctx, "pos.session", dict(z)),
            "warnings": [],
            "synthetic": False,
        }
        return Response(data)


class CatalogSnapshotView(SalesView):
    """``GET /catalog-snapshot?since=`` — read-only catalog for the PWA cache (no cost fields); ETag / 304."""

    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "pos.catalog_snapshot.read")
        data = catalog.snapshot(ctx, company, request.query_params.get("config_id"), request.query_params.get("since"))
        tag = 'W/"' + hashlib.sha256(json.dumps(data["rows"], sort_keys=True).encode()).hexdigest()[:32] + '"'
        response = Response(status=304) if request.headers.get("If-None-Match") == tag else Response(data)
        response["ETag"] = tag
        return response


class KpiView(SalesView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return Response(kpis.pos_kpis(ctx, company, request.query_params.get("period")))


class KpiPanelView(SalesView):
    def get(self, request: Request, panel_id: str) -> Response:
        ctx, company = self.ctx_company(request)
        if panel_id != "ventas_por_hora":
            raise NotFound("panel not found")
        return Response(kpis.sales_by_hour(ctx, company, request.query_params.get("period")))
