"""Reporting API (``contracts/openapi/modules/reporting.yaml``, P17a core part): semantic-model introspection,
``/query`` (+ ``:explain``), saved reports (CRUD, ``:run``, ``:schedule``, ``:publish``, ``:archive``) and the
``reportes`` KPIs. Every handler authorizes through ``PolicyService``; queries go through :mod:`reporting.engine`."""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from documents.http import SecuredView, action_body, body, check_if_match, command, file_response, page
from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.uow import UnitOfWork
from reporting import engine, exports, kpis, reports

SCOPE = "reporting"
FORMATS = ("json", "csv", "xlsx", "pdf")


def _format(request: Request) -> str:
    fmt = request.query_params.get("format", "json")
    if fmt not in FORMATS:
        raise ValidationFailed("format is json, csv, xlsx or pdf", field="format", code="filter_invalid")
    return str(fmt)


def respond(result: dict[str, Any], fmt: str, title: str) -> Any:
    if fmt == "json":
        return Response(result)
    content, media, ext = exports.export(result, fmt, title)
    return file_response(content, media, f"{result.get('report', 'reporte')}.{ext}")


class SemanticModel(SecuredView):
    def get(self, request: Request) -> Any:
        ctx, _company = self.ctx_company(request)
        services.get(PolicyService).require(ctx, "reporting.semantic_model.read")
        fmt = _format(request)
        described = engine.describe(ctx)
        rows: list[dict[str, Any]] = []
        for e in described:
            for f in e["fields"]:
                rows.append({"entity": e["entity"], "kind": "field", "key": f["key"], "label": f["label"],
                             "type": f["type"], "hierarchy": f["hierarchy"]})  # fmt: skip
            for m in e["measures"]:
                rows.append({"entity": e["entity"], "kind": "measure", "key": m["key"], "label": m["label"],
                             "type": m["type"], "hierarchy": None})  # fmt: skip
        result = {
            "report": "reporting.semantic_model",
            "columns": [
                {"key": "entity", "label": "Entidad", "type": "string"},
                {"key": "kind", "label": "Tipo", "type": "string"},
                {"key": "key", "label": "Clave", "type": "string"},
                {"key": "label", "label": "Nombre", "type": "string"},
                {"key": "type", "label": "Tipo de dato", "type": "string"},
                {"key": "hierarchy", "label": "Jerarquía", "type": "string"},
            ],
            "rows": rows,
            "totals": {"entities": len(described)},
            "parameters": {},
            "warnings": [],
            "synthetic": False,
            "entities": described,
        }
        return respond(result, fmt, "Modelo semántico")


def _companies(request: Request, ctx: Any, company: uuid.UUID, params: dict[str, Any]) -> list[uuid.UUID]:
    raw = params.pop("companies", None)
    if raw is None:
        return [company]
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("parameters.companies is a non-empty list of company ids", field="companies")
    try:
        return [ctx.require_company(uuid.UUID(str(c))) for c in raw]
    except ValueError as exc:
        raise ValidationFailed("company ids are UUIDs", field="companies") from exc


class Query(SecuredView):
    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        services.get(PolicyService).require(ctx, "reporting.query.read")
        _, params = action_body(request)
        companies = _companies(request, ctx, company, params)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return engine.run(ctx, companies, params), 200

        return command(request, ctx, f"{SCOPE}.query", fn)


class QueryExplain(SecuredView):
    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        services.get(PolicyService).require(ctx, "reporting.query.read")
        _, params = action_body(request)
        companies = _companies(request, ctx, company, params)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return {"status": "ok", "data": engine.explain_query(ctx, companies, params), "warnings": []}, 200

        return command(request, ctx, f"{SCOPE}.query.explain", fn)


def _etag(data: Any) -> str | None:
    from iam.api.base import etag_for

    return etag_for("reporting.SavedReport", data["id"], data["version"]) if isinstance(data, dict) else None


class ReportList(SecuredView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = reports.visible(ctx, company)
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(code__icontains=q)
        return page(request, qs, reports.report_json, {"status": "status", "code": "code"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return reports.report_json(reports.create(uow, company, body(request))), 201

        def headers(data: Any) -> dict[str, str]:
            return {"Location": f"/api/v1/reporting/reports/{data['id']}"}

        return command(request, ctx, f"{SCOPE}.report.create", fn, etag=_etag, headers=headers)


class ReportDetail(SecuredView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        r = reports.get(ctx, company, pk)
        response = Response(reports.report_json(r))
        response["ETag"] = r.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            r = reports.get(ctx, company, pk)
            check_if_match(request, r, required=True)
            return reports.report_json(reports.update(uow, r, body(request))), 200

        return command(request, ctx, f"{SCOPE}.report.update", fn, etag=_etag)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            r = reports.get(ctx, company, pk)
            check_if_match(request, r, required=False)
            reports.delete(uow, r)
            return None, 204

        return command(request, ctx, f"{SCOPE}.report.delete", fn)


class ReportAction(SecuredView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        _, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            r = reports.get(ctx, company, pk)
            if action == "run":
                services.get(PolicyService).require(ctx, "reporting.report.update")
                _run, result = reports.run(uow, r, trigger="api", parameters=params)
                return result, 200
            if action == "schedule":
                r = reports.schedule(uow, r, params)
            elif action == "publish":
                r = reports.publish(uow, r)
            elif action == "archive":
                r = reports.archive(uow, r)
            else:
                raise NotFound("unknown action")
            return {"status": r.status, "resource": {"type": "reporting.report", "id": str(r.id), "number": r.code},
                    "data": reports.report_json(r), "warnings": []}, 200  # fmt: skip

        return command(request, ctx, f"{SCOPE}.report.{action}", fn)


class KpiView(SecuredView):
    def _company(self, request: Request) -> tuple[Any, uuid.UUID]:
        raw = request.query_params.get("company_id")
        ctx = self.context()
        if raw:
            try:
                return ctx, ctx.require_company(uuid.UUID(raw))
            except ValueError as exc:
                raise ValidationFailed("company_id must be a UUID", field="company_id") from exc
        return self.ctx_company(request)

    def get(self, request: Request) -> Response:
        ctx, company = self._company(request)
        period = request.query_params.get("period")
        compare = request.query_params.get("compare", "previous") == "previous"
        return Response(kpis.reportes_kpis(ctx, company, period, compare))


class KpiPanelView(KpiView):
    def get(self, request: Request, panel: str = "") -> Response:
        if panel != "ejecuciones_diarias":
            raise NotFound("panel not found")
        ctx, company = self._company(request)
        return Response(kpis.daily_runs_panel(ctx, company, request.query_params.get("period")))
