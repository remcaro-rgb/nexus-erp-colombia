"""``/api/v1/dataio/…`` (contracts/openapi/modules/dataio.yaml) and ``/api/v1/kpis/datos``.

Every view inherits :class:`iam.api.base.ModuleSecuredView` (ADR-004 SEC-VIEW). Permission checks live in the
service layer (``dataio.service`` / ``dataio.exports``) so the API, the AI agent and MCP share them. Every state
change requires an ``Idempotency-Key``; simple commands replay their stored response, and the multi-transaction
actions (``:validate``, ``:dry-run``, ``:commit``, ``:resume``) are idempotent through the job state machine.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections.abc import Callable
from typing import Any

from django.http import HttpResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from dataio import exports, formats, kpis, service
from dataio.errors import FilterInvalid, ImportDuplicateBatch, ImportValidationFailed
from dataio.models import ExportJob, ImportJob, ImportMode, ImportStatus, MappingProfile
from dataio.templates import registry
from iam.api.base import ModuleSecuredView
from kernel import clock, services
from kernel.errors import PROBLEM_CONTENT_TYPE, DomainError, NotFound, ValidationFailed
from kernel.events import to_jsonable
from kernel.idempotency import StoredResponse, validate_key
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_LIMIT, MAX_LIMIT = 50, 500
STATE_FILTER = {"confirmado": ImportStatus.COMMITTED, "rechazado": ImportStatus.FAILED}


class IdempotencyKeyRequired(ValidationFailed):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


def idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyRequired("Idempotency-Key header required")
    return validate_key(key)


def respond(body: Any, status: int = 200, etag: str | None = None, replayed: bool | None = None) -> Response:
    resp = Response(to_jsonable(body) if body is not None else None, status=status)
    if etag:
        resp["ETag"] = etag
    if replayed is not None:
        resp["Idempotent-Replayed"] = "true" if replayed else "false"
    return resp


def problem(exc: DomainError, request: Request) -> Response:
    """A problem response *returned* (not raised) so state written before the error is kept."""
    return Response(exc.to_problem(instance=request.path), status=exc.status, content_type=PROBLEM_CONTENT_TYPE)


def command(request: Request, ctx: TenantContext, scope: str, fn: Callable[[UnitOfWork], tuple[Any, int]]) -> Response:
    key = idempotency_key(request)
    payload = {"path": request.path, "body": request.data if isinstance(request.data, dict) else None}

    def run(uow: UnitOfWork) -> tuple[Any, int]:
        body, status = fn(uow)
        body = to_jsonable(body)
        uow.set_response(body, status)
        return body, status

    result = run_in_unit_of_work(run, ctx, idempotency_key=key, request=payload, scope=scope)
    if isinstance(result, StoredResponse):
        return respond(result.body, result.status_code, replayed=True)
    body, status = result
    return respond(body, status, replayed=False)


def _body(request: Request, allowed: tuple[str, ...]) -> dict[str, Any]:
    data = request.data if request.data else {}
    if not isinstance(data, dict):
        raise ValidationFailed("a JSON object is required", pointer="/", reason="type")
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown field(s): {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="additional_property")
    return dict(data)


def _page(request: Request, items: list[dict[str, Any]]) -> Response:
    try:
        limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
    except ValueError:
        raise FilterInvalid("limit must be an integer") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise FilterInvalid("limit must be between 1 and 500")
    offset = 0
    if request.query_params.get("cursor"):
        try:
            offset = int(base64.urlsafe_b64decode(request.query_params["cursor"].encode()).decode())
        except (ValueError, binascii.Error, UnicodeDecodeError):
            raise FilterInvalid("invalid cursor") from None
    chunk = items[offset : offset + limit]
    more = len(items) > offset + limit
    return respond({"items": chunk, "next_cursor": base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if more else None})


def import_json(job: ImportJob, *, full: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(job.id),
        "tenant_id": str(job.tenant_id),
        "company_id": str(job.company_id),
        "status": job.import_status,
        "import_status": job.import_status,
        "entity": job.entity,
        "mode": job.mode,
        "batch_hash": job.batch_hash,
        "filename": job.filename,
        "file_format": job.file_format,
        "row_count": job.row_count,
        "error_count": job.error_count,
        "applied_rows": job.applied_rows,
        "counts": job.counts,
        "batches_done": job.batches_done,
        "batches_total": job.batches_total,
        "version": job.version,
        "audit": {"created_at": job.created_at, "created_by": job.created_by, "updated_at": job.updated_at, "updated_by": job.updated_by},
    }
    if full:
        body.update({"headers": job.headers, "mapping": job.mapping, "errors": job.errors[:1000], "preview": job.preview, "results": job.results})
    return body


def export_json(job: ExportJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "tenant_id": str(job.tenant_id),
        "company_id": str(job.company_id),
        "status": job.export_status,
        "export_status": job.export_status,
        "source": job.source,
        "format": job.format,
        "row_count": job.row_count,
        "filename": job.filename,
        "omitted_columns": job.omitted_columns,
        "version": job.version,
        "audit": {"created_at": job.created_at, "created_by": job.created_by, "updated_at": job.updated_at, "updated_by": job.updated_by},
    }


def profile_json(p: MappingProfile) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "tenant_id": str(p.tenant_id),
        "company_id": str(p.company_id),
        "name": p.name,
        "entity": p.entity,
        "mapping": p.mapping,
        "active": p.active,
        "version": p.version,
    }


class DataioView(ModuleSecuredView):
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        ctx = self.ctx
        raw = request.headers.get("X-Company-Id") or request.query_params.get("company_id")
        if raw:
            return ctx, ctx.require_company(raw)
        if len(ctx.company_ids) == 1:
            return ctx, next(iter(ctx.company_ids))
        raise ValidationFailed("X-Company-Id header required", pointer="/headers/X-Company-Id", reason="required")


def _file_response(content: bytes, content_type: str, filename: str) -> HttpResponse:
    resp = HttpResponse(content, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------------- templates


class TemplateList(DataioView):
    def get(self, request: Request) -> Response:
        ctx, _company = self.company(request)
        service.require(ctx, "dataio.template.read")
        q = (request.query_params.get("q") or "").strip().lower()
        items = []
        for tpl in registry.all_templates().values():
            d = tpl.describe()
            if q and q not in d["entity"] and q not in d["label"].lower():
                continue
            d["id"] = d["entity"]
            d["status"] = "ACTIVE"
            items.append(d)
        return _page(request, items)


class TemplateItem(DataioView):
    def get(self, request: Request, entity: str) -> Response:
        ctx, _company = self.company(request)
        service.require(ctx, "dataio.template.read")
        d = registry.get(entity).describe()
        d["id"] = d["entity"]
        d["status"] = "ACTIVE"
        return respond(d)


class TemplateDownload(DataioView):
    def get(self, request: Request, entity: str) -> HttpResponse:
        ctx, _company = self.company(request)
        fmt = request.query_params.get("format", "csv")
        examples = request.query_params.get("examples", "1") != "0"
        return _file_response(*exports.template_file(ctx, entity, fmt, examples))


# --------------------------------------------------------------------------- imports


class ImportCollection(DataioView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.import.read")
        qs = services.get(PolicyService).scope_queryset(
            ctx, "dataio.import.read", ImportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
        )
        estado = request.query_params.get("estado") or request.query_params.get("import_status")
        if estado:
            status = STATE_FILTER.get(estado, estado.upper())
            if status not in ImportStatus.values:
                raise FilterInvalid(f"unknown estado {estado}")
            qs = qs.filter(import_status=status)
        tipo = request.query_params.get("tipo") or request.query_params.get("entity")
        if tipo:
            qs = qs.filter(entity=tipo)
        return _page(request, [import_json(j) for j in qs.order_by("-created_at", "-id")[:5000]])

    def post(self, request: Request) -> Response:
        ctx, company = self.company(request)
        data = _body(request, ("entity", "mode", "batch_size", "decimal_comma"))
        if not data.get("entity"):
            raise ValidationFailed("entity is required", pointer="/entity", reason="required")

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = service.create_job(
                uow,
                company,
                str(data["entity"]),
                mode=str(data.get("mode") or ImportMode.UPSERT),
                batch_size=data.get("batch_size", 0),
                decimal_comma=bool(data.get("decimal_comma", False)),
            )
            return import_json(job), 201

        return command(request, ctx, "dataio.imports.create", fn)


class ImportItem(DataioView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.import.read")
        job = service.get_job(ctx, company, pk)
        return respond(import_json(job, full=True), etag=job.etag)


class ImportUpload(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        idempotency_key(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed("multipart field 'file' is required", pointer="/file", reason="required")
        if upload.size is not None and upload.size > formats.MAX_FILE_BYTES:
            raise ValidationFailed("file too large", pointer="/file", reason="size")
        data = upload.read(formats.MAX_FILE_BYTES + 1)
        profile = request.data.get("profile_id") if hasattr(request.data, "get") else None

        def fn(uow: UnitOfWork) -> ImportJob:
            job = service.get_job(uow.ctx, company, pk, lock=True)
            if job.file_hash and job.file_hash == hashlib.sha256(data).hexdigest():
                return job  # same file re-sent (retry): idempotent
            return service.upload(uow, job, upload.name or "", data, profile_id=profile)

        job = run_in_unit_of_work(fn, ctx)
        return respond(import_json(job, full=True), 200, etag=job.etag)


def _action_params(request: Request) -> dict[str, Any]:
    data = _body(request, ("reason", "comment", "effective_date", "parameters"))
    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValidationFailed("parameters must be an object", pointer="/parameters", reason="type")
    return params


class ImportMap(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        params = _action_params(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = service.get_job(uow.ctx, company, pk, lock=True)
            return import_json(service.set_mapping(uow, job, params.get("mapping") or {}), full=True), 200

        return command(request, ctx, "dataio.imports.map", fn)


class ImportValidate(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        idempotency_key(request)
        _action_params(request)
        job = service.get_job(ctx, company, pk)
        if job.import_status not in (ImportStatus.FAILED, ImportStatus.COMMITTED, ImportStatus.CANCELLED):
            job = service.dry_run(ctx, company, pk)
        return respond(import_json(job, full=True), etag=job.etag)


class ImportCommit(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        idempotency_key(request)
        _action_params(request)
        started = clock.now()
        try:
            job = service.commit(ctx, company, pk)
        except (ImportValidationFailed, ImportDuplicateBatch) as exc:
            return problem(exc, request)
        finished = clock.now()
        op = {
            "id": str(job.id),
            "kind": "dataio_imports_by_commit",
            "status": "SUCCEEDED" if job.import_status == ImportStatus.COMMITTED else "FAILED",
            "progress": "100" if job.batches_total and job.batches_done == job.batches_total else "0",
            "started_at": started,
            "finished_at": finished,
            "result_links": [f"/api/v1/dataio/imports/{job.id}", f"/api/v1/dataio/imports/{job.id}/report"],
            "import": import_json(job),
        }
        resp = respond(op, 202)
        resp["Location"] = f"/api/v1/dataio/imports/{job.id}"
        return resp


class ImportCancel(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        _action_params(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = service.get_job(uow.ctx, company, pk, lock=True)
            return import_json(service.cancel(uow, job)), 200

        return command(request, ctx, "dataio.imports.cancel", fn)


class ImportReport(DataioView):
    def get(self, request: Request, pk: uuid.UUID) -> Any:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.import.read")
        job = service.get_job(ctx, company, pk)
        fmt = request.query_params.get("format", "json")
        headers, rows = service.report_rows(job)
        if fmt == "json":
            return respond({"import": import_json(job), "errors": job.errors, "results": job.results, "counts": job.counts})
        if fmt not in formats.FORMATS:
            raise FilterInvalid("format must be json, csv, xlsx or jsonl")
        content = formats.write(fmt, headers, rows, sheet="resultado")
        return _file_response(content, formats.CONTENT_TYPES[fmt], f"resultado-{job.entity}-{job.id}.{fmt}")


# --------------------------------------------------------------------------- exports


class ExportCollection(DataioView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.export.read")
        qs = services.get(PolicyService).scope_queryset(
            ctx, "dataio.export.read", ExportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
        )
        return _page(request, [export_json(j) for j in qs.defer("content").order_by("-created_at", "-id")[:5000]])

    def post(self, request: Request) -> Response:
        ctx, company = self.company(request)
        data = _body(request, ("source", "format", "start"))
        if not data.get("source") or not data.get("format"):
            raise ValidationFailed("source and format are required", pointer="/source", reason="required")

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = exports.create_export(uow, company, str(data["source"]), str(data["format"]))
            if data.get("start", True):
                job = exports.start_export(uow, job)
            return export_json(job), 201

        return command(request, ctx, "dataio.exports.create", fn)


class ExportItem(DataioView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.export.read")
        job = exports.get_export(ctx, company, pk)
        return respond(export_json(job), etag=job.etag)


class ExportStart(DataioView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        _action_params(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = exports.get_export(uow.ctx, company, pk)
            return export_json(exports.start_export(uow, job)), 202

        return command(request, ctx, "dataio.exports.start", fn)


class ExportFile(DataioView):
    def get(self, request: Request, pk: uuid.UUID) -> HttpResponse:
        ctx, company = self.company(request)
        job = exports.get_export(ctx, company, pk)
        return _file_response(*exports.file_of(ctx, job))


# --------------------------------------------------------------------------- mapping profiles


def _profile(ctx: TenantContext, company: uuid.UUID, pk: uuid.UUID) -> MappingProfile:
    p = MappingProfile.objects.filter(tenant_id=ctx.tenant_id, company_id=company, id=pk).first()
    if p is None:
        raise NotFound("mapping profile not found")
    return p


def _profile_values(data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in data or not partial:
        name = str(data.get("name") or "").strip()
        if not 1 <= len(name) <= 200:
            raise ValidationFailed("name must have 1 to 200 characters", pointer="/name", reason="length")
        out["name"] = name
    if "entity" in data or not partial:
        out["entity"] = registry.get(str(data.get("entity") or "")).entity
    if "mapping" in data or not partial:
        mapping = data.get("mapping")
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValidationFailed("mapping must be an object of header → column", pointer="/mapping", reason="type")
        out["mapping"] = mapping
    if "active" in data:
        if not isinstance(data["active"], bool):
            raise ValidationFailed("active must be a boolean", pointer="/active", reason="type")
        out["active"] = data["active"]
    if "mapping" in out:
        tpl = registry.get(out.get("entity") or str(data.get("entity") or ""))
        names = {c.name for c in tpl.columns}
        bad = sorted(v for v in out["mapping"].values() if v and v not in names)
        if bad:
            raise ValidationFailed(f"unknown template column {bad[0]!r}", pointer="/mapping", reason="unknown_column")
    return out


class ProfileCollection(DataioView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.mapping_profile.read")
        qs = MappingProfile.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
        if request.query_params.get("entity"):
            qs = qs.filter(entity=request.query_params["entity"])
        return _page(request, [profile_json(p) for p in qs.order_by("entity", "name")])

    def post(self, request: Request) -> Response:
        ctx, company = self.company(request)
        data = _body(request, ("name", "entity", "mapping", "active"))

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            service.require(uow.ctx, "dataio.mapping_profile.create")
            values = _profile_values(data, partial=False)
            if MappingProfile.objects.filter(tenant_id=ctx.tenant_id, company_id=company, entity=values["entity"], name=values["name"]).exists():
                raise ValidationFailed("a profile with this name exists", pointer="/name", reason="duplicate")
            p = MappingProfile.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, created_by=ctx.principal_id, updated_by=ctx.principal_id, **values
            )
            return profile_json(p), 201

        return command(request, ctx, "dataio.mapping_profiles.create", fn)


class ProfileItem(DataioView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        service.require(ctx, "dataio.mapping_profile.read")
        p = _profile(ctx, company, pk)
        return respond(profile_json(p), etag=p.etag)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        data = _body(request, ("name", "entity", "mapping", "active"))
        if not data:
            raise ValidationFailed("empty patch", pointer="/", reason="min_properties")

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            service.require(uow.ctx, "dataio.mapping_profile.update")
            p = _profile(uow.ctx, company, pk)
            p.check_if_match(request.headers.get("If-Match") or "*")
            values = _profile_values({"entity": p.entity, **data}, partial=True)
            for k, v in values.items():
                setattr(p, k, v)
            p.save_versioned(uow.ctx.principal_id, list(values))
            p.refresh_from_db()
            return profile_json(p), 200

        return command(request, ctx, "dataio.mapping_profiles.update", fn)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            service.require(uow.ctx, "dataio.mapping_profile.delete")
            _profile(uow.ctx, company, pk).delete()
            return None, 204

        return command(request, ctx, "dataio.mapping_profiles.delete", fn)


# --------------------------------------------------------------------------- KPIs


class KpiView(DataioView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        compare = request.query_params.get("compare", "previous") == "previous"
        return respond(kpis.datos_kpis(ctx, company, period, compare))


class KpiPanelView(DataioView):
    def get(self, request: Request, panel: str = "") -> Response:
        if panel != "lotes_por_tipo":
            raise NotFound("panel not found")
        ctx, company = self.company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        return respond(kpis.lotes_por_tipo(ctx, company, period))
