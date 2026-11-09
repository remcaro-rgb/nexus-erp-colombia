"""Parameter store, sources, compute, localization and exógena endpoints."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Q
from rest_framework.request import Request
from rest_framework.response import Response

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.uow import UnitOfWork
from localization_co import holidays as hol
from localization_co import nit
from localization_co.models import HolidayOverride, Municipality
from tax.api import base, serializers
from tax.engine.types import DocumentLine, PartyProfile
from tax.exogena import service as exogena_service
from tax.exogena.models import ExogenaFormatPack
from tax.models import Parameter, SourceRecord
from tax.parameters import workflow
from tax.service import compute_document

HOLIDAY_NS = uuid.UUID("6f1d4d70-5a0e-4c8e-9a4b-3d7c0b6f2a11")


# ----------------------------------------------------------------------------- parameters


def tenant_or_scoped(ctx: Any, qs: Any, permission: str) -> Any:
    """Tenant-wide rows (``company_id`` NULL) plus company rows filtered by the policy row scope."""
    qs = qs.filter(tenant_id=ctx.tenant_id)
    company_rows = services.get(PolicyService).scope_queryset(ctx, permission, qs.filter(company_id__isnull=False))
    return qs.filter(Q(company_id__isnull=True) | Q(id__in=company_rows.values("id")))


def _parameters(ctx: Any, action: str = "read") -> Any:
    return tenant_or_scoped(ctx, Parameter.objects.select_related("source"), f"tax.parameter.{action}")


def _proposal(data: dict[str, Any], *, code: str | None = None) -> workflow.ProposalInput:
    allowed = {"code", "value_type", "value", "valid_from", "valid_to", "parameter_status", "source_id", "source",
               "company_id", "scope_ref", "document_ref", "synthetic", "notes", "legal_history"}  # fmt: skip
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown parameters: {', '.join(unknown)}", fields=unknown)
    missing = [k for k in ("value_type", "valid_from", "parameter_status") if k not in data]
    if code is None and "code" not in data:
        missing.insert(0, "code")
    if missing:
        raise ValidationFailed(f"missing parameters: {', '.join(missing)}", fields=missing)
    return workflow.ProposalInput(
        code=code or str(data["code"]),
        value_type=str(data["value_type"]),
        value=data.get("value"),
        valid_from=base.parse_date(data["valid_from"], "valid_from"),
        valid_to=base.parse_date(data["valid_to"], "valid_to") if data.get("valid_to") else None,
        parameter_status=str(data["parameter_status"]),
        source_id=base.parse_uuid(data["source_id"], "source_id") if data.get("source_id") else None,
        source=data.get("source") if isinstance(data.get("source"), dict) else None,
        company_id=base.parse_uuid(data["company_id"], "company_id") if data.get("company_id") else None,
        scope_ref=str(data.get("scope_ref", "")),
        document_ref=str(data.get("document_ref", "")),
        synthetic=bool(data.get("synthetic", False)),
        notes=str(data.get("notes", "")),
        legal_history=tuple(data.get("legal_history") or ()),
    )


def _action(request: Request) -> tuple[dict[str, Any], str]:
    data = base.body_object(request, {"reason", "comment", "effective_date", "parameters"})
    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValidationFailed("parameters must be an object", field="parameters")
    return params, str(data.get("reason") or "")


class ParameterList(base.TaxAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.read")
        qs = _parameters(ctx)
        q = request.query_params
        if q.get("code"):
            qs = qs.filter(code=q["code"])
        if q.get("approval"):
            qs = qs.filter(approval=q["approval"])
        if q.get("parameter_status"):
            qs = qs.filter(parameter_status=q["parameter_status"])
        if q.get("valid_on"):
            on = base.parse_date(q["valid_on"], "valid_on")
            qs = qs.filter(valid_from__lte=on).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on))
        return base.page(request, qs, serializers.parameter)


class ParameterDetail(base.TaxAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.read")
        obj = base.get_or_404(_parameters(ctx), pk)
        return base.with_etag(Response(serializers.parameter(obj)), obj)


class ParameterImpact(base.TaxAPIView):
    """``GET /parameters/{id}/impact`` — documents that would change (CCR P06-1)."""

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.read")
        obj = base.get_or_404(_parameters(ctx), pk)
        return Response(workflow.impact_preview(ctx, obj))


class ParameterPropose(base.TaxAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.create")
        params, reason = _action(request)
        data = _proposal(params)

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = workflow.propose(uow, data, reason=reason)
            return serializers.parameter(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, "tax.parameter.propose", run)


class ParameterProposeChange(base.TaxAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.update")
        params, reason = _action(request)

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            current = base.get_or_404(_parameters(ctx), pk)
            data = _proposal({"company_id": str(current.company_id) if current.company_id else None,
                              "scope_ref": current.scope_ref, **params}, code=current.code)  # fmt: skip
            obj = workflow.propose_change(uow, pk, data, reason=reason)
            return serializers.parameter(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, "tax.parameter.propose_change", run)


class ParameterApprove(base.TaxAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.parameter.approve")
        params, reason = _action(request)
        legal_review = params.get("legal_review", False)
        if not isinstance(legal_review, bool):
            raise ValidationFailed("parameters.legal_review must be a boolean", field="parameters.legal_review")

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            current = base.get_or_404(_parameters(ctx), pk)
            if request.headers.get("If-Match"):
                current.check_if_match(request.headers.get("If-Match"))
            obj = workflow.approve(uow, pk, reason=reason, legal_review=legal_review)
            return serializers.parameter(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, "tax.parameter.approve", run)


# ----------------------------------------------------------------------------- sources


class SourceList(base.TaxAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.source.read")
        return base.page(request, SourceRecord.objects.filter(tenant_id=ctx.tenant_id), serializers.source)

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.source.create")
        data = base.body_object(request, {"url", "title", "accessed_at", "method", "excerpt_hash"},
                                ("url", "title", "accessed_at"))  # fmt: skip
        try:
            accessed = dt.datetime.fromisoformat(str(data["accessed_at"]))
        except ValueError as exc:
            raise ValidationFailed("accessed_at must be an ISO datetime", field="accessed_at") from exc
        if accessed.tzinfo is None:
            raise ValidationFailed("accessed_at needs a timezone", field="accessed_at")
        url = str(data["url"])
        if not url.startswith(("https://", "http://")) or len(url) > 2000:
            raise ValidationFailed("url must be an absolute http(s) URL", field="url")

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = SourceRecord.objects.create(
                tenant_id=ctx.tenant_id, url=url, title=str(data["title"])[:255], accessed_at=accessed,
                method=str(data.get("method", ""))[:255], excerpt_hash=str(data.get("excerpt_hash", ""))[:255],
                created_by=ctx.principal_id, updated_by=ctx.principal_id,
            )  # fmt: skip
            return serializers.source(obj), 201, {"ETag": obj.etag, "Location": f"/api/v1/tax/sources/{obj.id}"}

        return base.command(request, ctx, "tax.source.create", run)


class SourceDetail(base.TaxAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.source.read")
        obj = base.get_or_404(SourceRecord.objects.filter(tenant_id=ctx.tenant_id), pk)
        return base.with_etag(Response(serializers.source(obj)), obj)


# ----------------------------------------------------------------------------- compute


def _profile(raw: Any, field: str) -> PartyProfile | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationFailed(f"{field} must be an object", field=field)
    allowed = set(PartyProfile.__dataclass_fields__)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValidationFailed(f"{field}: unknown properties {unknown}", field=field)
    values = dict(raw)
    for key in ("ica_municipalities", "responsibilities"):
        if key in values:
            values[key] = tuple(values[key])
    return PartyProfile(**values)


def _lines(raw: Any) -> list[DocumentLine]:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("parameters.lines must be a non-empty list", field="lines")
    out = []
    for i, line in enumerate(raw):
        if not isinstance(line, dict) or "base_amount" not in line:
            raise ValidationFailed(f"lines[{i}] needs base_amount", field=f"lines[{i}]")
        amount = line["base_amount"]
        if isinstance(amount, (bool, float)) or not isinstance(amount, (str, int)):
            raise ValidationFailed(f"lines[{i}].base_amount must be a decimal string", field=f"lines[{i}].base_amount")
        try:
            base_amount = Decimal(str(amount))
        except ArithmeticError as exc:
            raise ValidationFailed(f"lines[{i}].base_amount is not a number", field=f"lines[{i}]") from exc
        out.append(DocumentLine(str(line.get("line_ref", i + 1)), base_amount, tuple(line.get("tax_codes", []) or []),
                                line.get("concept"), line.get("activity_code")))  # fmt: skip
    return out


def run_compute(ctx: Any, company: uuid.UUID, params: dict[str, Any]) -> dict[str, Any]:
    party = params.get("party_id")
    result = compute_document(
        ctx.tenant_id,
        company,
        str(params.get("document_type", "purchase.invoice")),
        base.parse_date(params.get("document_date"), "document_date"),
        base.parse_uuid(party, "party_id") if party else None,
        _lines(params.get("lines")),
        municipality=params.get("municipality"),
        rounding_mode=str(params.get("rounding_mode", "LINE")),
        seller=_profile(params.get("seller"), "seller"),
        buyer=_profile(params.get("buyer"), "buyer"),
    )
    warnings = [{"code": "parameter_disputed_warning", "detail": w} for w in result.warnings]
    return {"status": "COMPUTED", "data": result.to_json(), "warnings": warnings}


class Compute(base.TaxAPIView):
    """Stateless calculation (also the AI tool). Same permission for UI, API, AI and MCP."""

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, "tax.computation.read")
        params, _ = _action(request)
        if request.headers.get("Idempotency-Key"):
            return base.command(request, ctx, "tax.compute", lambda uow: (run_compute(ctx, company, params), 200, {}))
        return Response(run_compute(ctx, company, params))


# ----------------------------------------------------------------------------- localization


class NitValidate(base.TaxAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.nit.read")
        params, _ = _action(request)
        if "nit" not in params:
            raise ValidationFailed("parameters.nit is required", field="nit")
        result = nit.validate(str(params["nit"]), params.get("dv"))
        body = {"status": "VALID" if result.valid else "INVALID", "data": result.to_json()}
        if not result.valid:
            body["warnings"] = [{"code": "nit_invalid", "detail": "el dígito de verificación no corresponde"}]
        return Response(body)


def municipality_json(m: Municipality) -> dict[str, Any]:
    return {"id": str(m.id), "tenant_id": None, "company_id": None, "status": m.status, "code": m.code,
            "name": m.name, "department": m.department.name, "version": m.version,
            "audit": {"created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat()}}  # fmt: skip


class MunicipalityList(base.TaxAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.municipality.read")
        qs = Municipality.objects.select_related("department")
        q = request.query_params
        if q.get("department"):
            qs = qs.filter(department_id=q["department"])
        if q.get("q"):
            qs = qs.filter(Q(name__icontains=q["q"]) | Q(code__startswith=q["q"]))
        return base.page(request, qs, municipality_json)


class MunicipalityDetail(base.TaxAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.municipality.read")
        return Response(municipality_json(base.get_or_404(Municipality.objects.select_related("department"), pk)))


def _holidays(ctx: Any, company: uuid.UUID, year: int) -> list[dict[str, Any]]:
    overrides = [
        hol.Override(o.date, o.add, o.name)
        for o in HolidayOverride.objects.filter(tenant_id=ctx.tenant_id, company_id=company, date__year=year)
    ]
    out = []
    for h in hol.holidays_for_year(year, overrides):
        out.append({"id": str(uuid.uuid5(HOLIDAY_NS, f"{company}:{h.date.isoformat()}:{h.name}")),
                    "tenant_id": str(ctx.tenant_id), "company_id": str(company), "status": h.rule,
                    "date": h.date.isoformat(), "name": h.name, "version": 1})  # fmt: skip
    return out


class HolidayList(base.TaxAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, "tax.holiday.read")
        try:
            year = int(request.query_params.get("year", dt.date.today().year))  # noqa: DTZ011 - calendar year only
        except ValueError as exc:
            raise base.FilterInvalid("year must be an integer") from exc
        if not 1990 <= year <= 2100:
            raise base.FilterInvalid("year must be 1990..2100")
        return Response({"items": _holidays(ctx, company, year), "next_cursor": None})


class HolidayDetail(base.TaxAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, "tax.holiday.read")
        year = int(request.query_params.get("year", dt.date.today().year))  # noqa: DTZ011
        for h in _holidays(ctx, company, year):
            if h["id"] == str(pk):
                return Response(h)
        raise NotFound("holiday not found (pass ?year=)")


# ----------------------------------------------------------------------------- exógena packs


def _packs(ctx: Any) -> Any:
    return tenant_or_scoped(ctx, ExogenaFormatPack.objects.all(), "tax.exogena_format_pack.read")


class ExogenaPackList(base.TaxAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.exogena_format_pack.read")
        qs = _packs(ctx)
        if request.query_params.get("year"):
            qs = qs.filter(year=request.query_params["year"])
        return base.page(request, qs, serializers.exogena_pack)

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.exogena_format_pack.create")
        data = base.body_object(request, {"code", "year", "pack"}, ("code", "year", "pack"))
        pack = data["pack"]
        if not isinstance(pack, dict) or pack.get("pack_code") != data["code"] or pack.get("year") != data["year"]:
            raise ValidationFailed("pack.pack_code and pack.year must equal code and year", field="pack")

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = exogena_service.store_pack(uow, pack)
            return (serializers.exogena_pack(obj), 201,
                    {"ETag": obj.etag, "Location": f"/api/v1/tax/exogena/format-packs/{obj.id}"})  # fmt: skip

        return base.command(request, ctx, "tax.exogena_format_pack.create", run)


class ExogenaPackDetail(base.TaxAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.exogena_format_pack.read")
        obj = base.get_or_404(_packs(ctx), pk)
        return base.with_etag(Response(serializers.exogena_pack(obj)), obj)


class ExogenaPackImport(base.TaxAPIView):
    """Multipart JSON file import; validated synchronously, answered as a finished operation (202)."""

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "tax.exogena_format_pack.create")
        base.idempotency_key(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed("multipart field 'file' is required", field="file")
        if upload.size > 5 * 1024 * 1024:
            raise ValidationFailed("format pack larger than 5 MB", field="file")
        try:
            pack = json.loads(upload.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationFailed("file is not UTF-8 JSON", field="file") from exc
        if isinstance(pack, dict) and pack.get("source") == "SYNTHETIC":
            pass  # synthetic packs keep NOT_DIAN_VALID (schema enforces it)
        elif isinstance(pack, dict):
            pack.setdefault("source", "OFFICIAL_IMPORTED")
            pack.setdefault("legal_status", "PENDING_VERIFICATION")

        def run(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = exogena_service.store_pack(uow, pack)
            op = {"id": str(obj.id), "kind": "tax_exogena_format_packs_import", "status": "SUCCEEDED",
                  "progress": "100", "result": serializers.exogena_pack(obj)}  # fmt: skip
            return op, 202, {"Location": f"/api/v1/tax/exogena/format-packs/{obj.id}"}

        return base.command(request, ctx, "tax.exogena_format_pack.import", run, payload=pack)
