"""HTTP endpoints of ``/api/v1/consolidation/…`` and ``/api/v1/intercompany/…`` (contracts 1.0.0)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from consolidation import access, engine, intercompany, ledgerdata, reports, service
from consolidation.api.base import (
    ConsolidationAPIView,
    body_of,
    check_if_match,
    command,
    opt_uuid,
    paginate,
    parse_uuid,
    report_response,
    respond,
)
from consolidation.errors import FilterInvalid
from consolidation.models import ConsolidationGroup, ConsolidationRun, IntercompanyAgreement, RunStatus
from kernel.errors import NotFound
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

GROUP_FIELDS = ("code", "name", "companies", "presentation_currency")
RUN_FIELDS = ("document_date", "posting_date", "currency", "notes", "group_id", "period", "lines")
AGREEMENT_FIELDS = ("code", "name", "company_a_id", "company_b_id", "accounts")
ACTION_FIELDS = ("reason", "comment", "effective_date", "parameters")


def _audit(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("created_at", "created_by", "updated_at", "updated_by"):
        value = getattr(obj, key, None)
        if value is not None:
            out[key] = value
    channel = getattr(obj, "channel", "")
    if channel:
        out["channel"] = channel
    if hasattr(obj, "on_behalf_of"):
        out["on_behalf_of"] = obj.on_behalf_of
    return out


def group_json(g: ConsolidationGroup) -> dict[str, Any]:
    return {
        "id": g.id,
        "tenant_id": g.tenant_id,
        "code": g.code,
        "name": g.name,
        "status": g.status,
        "companies": [str(c) for c in service.company_ids(g)],
        "presentation_currency": g.presentation_currency,
        "version": g.version,
        "audit": _audit(g),
    }


def run_json(r: ConsolidationRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "number": r.number,
        "document_date": r.document_date.isoformat(),
        "posting_date": r.posting_date.isoformat() if r.posting_date else None,
        "status": r.status,
        "currency": r.currency,
        "total": {"amount": str(r.total), "currency": r.currency},
        "notes": r.notes,
        "source": {"type": "consolidation.group", "id": str(r.group_id), "number": None},
        "group_id": str(r.group_id),
        "period": r.period,
        "lines": [
            {
                "line_no": ln.line_no,
                "item_id": str(ln.item_id) if ln.item_id else None,
                "description": ln.description,
                "quantity": {"value": f"{ln.quantity:.6f}", "uom": ln.uom},
                "unit_price": {"amount": f"{ln.unit_price:.4f}", "currency": r.currency},
                "amount": {"amount": f"{ln.amount:.4f}", "currency": r.currency},
                "tax_code": ln.tax_code,
                "account_code": ln.account_code,
                "dimensions": {k: v for k, v in (ln.dimensions or {}).items() if k in _SLOTS},
            }
            for ln in r.lines.order_by("line_no")
        ],
        "version": r.version,
        "audit": _audit(r),
    }


_SLOTS = ("cost_center_id", "business_unit_id", "aux_abierto_id", "item_dimension_id", "branch_id")


def agreement_json(a: IntercompanyAgreement) -> dict[str, Any]:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "code": a.code,
        "name": a.name,
        "status": a.status,
        "company_a_id": str(a.company_a_id),
        "company_b_id": str(a.company_b_id),
        "accounts": intercompany.agreement_accounts(a),
        "version": a.version,
        "audit": _audit(a),
    }


# --------------------------------------------------------------------------- groups


class GroupCollection(ConsolidationAPIView):
    def get(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "consolidation.group.read")
        items = sorted(service.visible_groups(ctx), key=lambda g: g.code)
        return paginate(request, items, group_json, {"status": lambda g: g.status, "code": lambda g: g.code})

    def post(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "consolidation.group.create")
        data = body_of(request, GROUP_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            g = service.create_group(uow, data)
            return group_json(g), 201, g.etag

        resp = command(request, ctx, "consolidation.group.create", fn)
        if resp.status_code == 201 and isinstance(resp.data, dict):
            resp["Location"] = f"/api/v1/consolidation/groups/{resp.data.get('id')}"
        return resp


class GroupItem(ConsolidationAPIView):
    def get(self, request: Request, pk: str) -> Response:
        g = service.get_group(self.ctx, parse_uuid(pk), "consolidation.group.read")
        return respond(group_json(g), etag=g.etag)

    def patch(self, request: Request, pk: str) -> Response:
        ctx = self.ctx
        data = body_of(request, GROUP_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            g = service.get_group(ctx, parse_uuid(pk), "consolidation.group.update", lock=True)
            check_if_match(request, g, required=True)
            g = service.update_group(uow, g, data)
            return group_json(g), 200, g.etag

        return command(request, ctx, "consolidation.group.update", fn)

    def delete(self, request: Request, pk: str) -> Response:
        ctx = self.ctx

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            g = service.get_group(ctx, parse_uuid(pk), "consolidation.group.delete", lock=True)
            check_if_match(request, g, required=False)
            service.archive_group(uow, g)
            return None, 204, None

        return command(request, ctx, "consolidation.group.delete", fn)


# --------------------------------------------------------------------------- runs


class RunCollection(ConsolidationAPIView):
    def get(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "consolidation.run.read")
        items = service.visible_runs(ctx)
        return paginate(
            request,
            items,
            run_json,
            {"status": lambda r: r.status, "group_id": lambda r: r.group_id, "period": lambda r: r.period},
        )

    def post(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "consolidation.run.create")
        data = body_of(request, RUN_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            r = service.create_run(uow, data)
            return run_json(r), 201, r.etag

        resp = command(request, ctx, "consolidation.run.create", fn)
        if resp.status_code == 201 and isinstance(resp.data, dict):
            resp["Location"] = f"/api/v1/consolidation/runs/{resp.data.get('id')}"
        return resp


class RunItem(ConsolidationAPIView):
    def get(self, request: Request, pk: str) -> Response:
        r = service.get_run(self.ctx, parse_uuid(pk), "consolidation.run.read")
        return respond(run_json(r), etag=r.etag)

    def patch(self, request: Request, pk: str) -> Response:
        ctx = self.ctx
        data = body_of(request, RUN_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            r = service.get_run(ctx, parse_uuid(pk), "consolidation.run.update", lock=True)
            check_if_match(request, r, required=True)
            r = service.update_run(uow, r, data)
            r.refresh_from_db()
            return run_json(r), 200, r.etag

        return command(request, ctx, "consolidation.run.update", fn)

    def delete(self, request: Request, pk: str) -> Response:
        ctx = self.ctx

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            r = service.get_run(ctx, parse_uuid(pk), "consolidation.run.delete", lock=True)
            check_if_match(request, r, required=False)
            service.cancel_run(uow, r)
            return None, 204, None

        return command(request, ctx, "consolidation.run.delete", fn)


class RunPreview(ConsolidationAPIView):
    def post(self, request: Request, pk: str) -> Response:
        ctx = self.ctx
        body_of(request, ACTION_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            r = service.get_run(ctx, parse_uuid(pk), "consolidation.run.read")
            return service.preview(uow, r), 200, None

        return command(request, ctx, "consolidation.run.preview", fn)


class RunPost(ConsolidationAPIView):
    def post(self, request: Request, pk: str) -> Response:
        ctx = self.ctx
        body_of(request, ACTION_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            r = service.get_run(ctx, parse_uuid(pk), "consolidation.run.post", lock=True)
            check_if_match(request, r, required=False)
            r = service.post_run(uow, r)
            return run_json(r), 200, r.etag

        return command(request, ctx, "consolidation.run.post", fn)


# --------------------------------------------------------------------------- reports


def _report_data(ctx: TenantContext, request: Request) -> reports.RunData | None:
    access.require(ctx, "consolidation.consolidation_report.read")
    run_id = opt_uuid(request, "run_id")
    if run_id is None:
        posted = [
            r
            for r in service.visible_runs(ctx, "consolidation.consolidation_report.read")
            if r.status == RunStatus.POSTED
        ]
        if not posted:
            return None
        run = posted[0]
    else:
        try:
            run = service.get_run(ctx, run_id, "consolidation.consolidation_report.read")
        except NotFound:
            raise FilterInvalid("run_id not found") from None
    if run.status in (RunStatus.POSTED, RunStatus.REVERSED):
        return reports.RunData.from_posted(run)
    if run.status == RunStatus.CANCELLED:
        raise FilterInvalid("the run is cancelled")
    access.require_feature(ctx, service.company_ids(run.group))
    result, _rates = service.compute(ctx, run)
    return reports.RunData.from_result(run, result)


class BalanceReport(ConsolidationAPIView):
    def get(self, request: Request) -> Response | HttpResponse:
        data = _report_data(self.ctx, request)
        body = reports.balance(data) if data else reports.empty("consolidation.balance")
        return report_response(request, body, "estado-situacion-consolidado")


class IncomeStatementReport(ConsolidationAPIView):
    def get(self, request: Request) -> Response | HttpResponse:
        data = _report_data(self.ctx, request)
        if data is None:
            body = reports.empty("consolidation.income_statement")
        else:
            nci = engine._accounts(data.run.group.settings)["nci_profit"][0]
            body = reports.income_statement(data, nci)
        return report_response(request, body, "estado-resultados-consolidado")


class EliminationsReport(ConsolidationAPIView):
    def get(self, request: Request) -> Response | HttpResponse:
        data = _report_data(self.ctx, request)
        body = reports.eliminations(data) if data else reports.empty("consolidation.eliminations")
        return report_response(request, body, "eliminaciones-consolidacion")


# --------------------------------------------------------------------------- intercompany


class AgreementCollection(ConsolidationAPIView):
    def get(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "intercompany.agreement.read")
        items = sorted(intercompany.visible_agreements(ctx), key=lambda a: a.code)
        return paginate(
            request,
            items,
            agreement_json,
            {
                "status": lambda a: a.status,
                "code": lambda a: a.code,
                "company_a_id": lambda a: a.company_a_id,
                "company_b_id": lambda a: a.company_b_id,
            },
        )

    def post(self, request: Request) -> Response:
        ctx = self.ctx
        access.require(ctx, "intercompany.agreement.create")
        data = body_of(request, AGREEMENT_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            a = intercompany.create_agreement(uow, data)
            return agreement_json(a), 201, a.etag

        resp = command(request, ctx, "intercompany.agreement.create", fn)
        if resp.status_code == 201 and isinstance(resp.data, dict):
            resp["Location"] = f"/api/v1/intercompany/agreements/{resp.data.get('id')}"
        return resp


class AgreementItem(ConsolidationAPIView):
    def get(self, request: Request, pk: str) -> Response:
        a = intercompany.get_agreement(self.ctx, parse_uuid(pk), "intercompany.agreement.read")
        return respond(agreement_json(a), etag=a.etag)

    def patch(self, request: Request, pk: str) -> Response:
        ctx = self.ctx
        data = body_of(request, AGREEMENT_FIELDS)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            a = intercompany.get_agreement(ctx, parse_uuid(pk), "intercompany.agreement.update", lock=True)
            check_if_match(request, a, required=True)
            a = intercompany.update_agreement(uow, a, data)
            return agreement_json(a), 200, a.etag

        return command(request, ctx, "intercompany.agreement.update", fn)

    def delete(self, request: Request, pk: str) -> Response:
        ctx = self.ctx

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            a = intercompany.get_agreement(ctx, parse_uuid(pk), "intercompany.agreement.delete", lock=True)
            check_if_match(request, a, required=False)
            intercompany.archive_agreement(uow, a)
            return None, 204, None

        return command(request, ctx, "intercompany.agreement.delete", fn)


class ReconciliationReport(ConsolidationAPIView):
    def get(self, request: Request) -> Response | HttpResponse:
        ctx = self.ctx
        access.require(ctx, "intercompany.reconciliation.read")
        period = request.query_params.get("period")
        if period:
            if not service.PERIOD_RE.match(period):
                raise FilterInvalid("period must be YYYY-MM")
            until = ledgerdata.period_bounds(period)[2]
        else:
            until = dt.date.max
        rows = intercompany.reconciliation_rows(
            ctx, until, opt_uuid(request, "company_a_id"), opt_uuid(request, "company_b_id")
        )
        body = reports.reconciliation(rows, period or "")
        return report_response(request, body, "conciliacion-intercompania")
