"""Documents services (P17a): company templates (versioned), preview, render + store, render batches.

Every function runs inside the caller's unit of work and authorizes through ``PolicyService`` with the codes of
``contracts/openapi/modules/documents.yaml`` (``documents.template.*``, ``documents.render.export``); rendering a
source record additionally needs the read permission of that record (see :mod:`documents.sources`).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError

from documents import builtin, sources
from documents.markup import TemplateError, parse
from documents.models import DocumentTemplate, DocumentTemplateVersion, GeneratedDocument, RenderBatch, Status
from documents.renderer import RENDERER, render_body
from kernel import clock, services
from kernel.errors import Conflict, DomainError, NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.documents import RenderedDocument
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

TEMPLATE = "documents.template"
RENDER = "documents.render"
ENGINES = ("weasyprint",)
MAX_BATCH = 200
TEMPLATE_FIELDS = ("code", "name", "document_type", "engine", "body", "locale")


class TemplateRenderFailed(DomainError):
    code, status, title = "template_render_failed", 422, "Document render failed"


def _policy() -> PolicyService:
    return services.get(PolicyService)


def audit(uow: UnitOfWork, action: str, resource: str, rid: uuid.UUID | None, company: uuid.UUID, **changes: Any) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, resource, rid, company, dict(changes)))


# --------------------------------------------------------------------------- templates


def templates_qs(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    _policy().require(ctx, f"{TEMPLATE}.read")
    return _policy().scope_queryset(ctx, f"{TEMPLATE}.read", DocumentTemplate.objects.filter(company_id=company_id))


def get_template(ctx: TenantContext, company_id: uuid.UUID, template_id: uuid.UUID) -> DocumentTemplate:
    t = templates_qs(ctx, company_id).filter(id=template_id).first()
    if t is None:
        raise NotFound("template not found")
    return t


def _text(data: Mapping[str, Any], key: str, limit: int, *, required: bool) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        if required:
            raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValidationFailed(f"{key} is a text of at most {limit} characters", pointer=f"/{key}")
    return value


def _validated(data: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = sorted(set(data) - set(TEMPLATE_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", pointer=f"/{unknown[0]}")
    out: dict[str, Any] = {}
    limits = {"code": 40, "name": 200, "document_type": 255, "engine": 20, "body": 10_000, "locale": 20}
    for key, limit in limits.items():
        if partial and key not in data:
            continue
        value = _text(data, key, limit, required=key in ("code", "name", "document_type", "body") and not partial)
        if value is not None:
            out[key] = value
    if "engine" in out and out["engine"] not in ENGINES:
        raise ValidationFailed("engine must be weasyprint", pointer="/engine", reason="enum")
    if "locale" in out and out["locale"] != "es-CO":
        raise ValidationFailed("locale must be es-CO (D-001)", pointer="/locale", reason="enum")
    if "body" in out:
        try:
            parse(out["body"])
        except TemplateError as exc:
            raise TemplateRenderFailed(str(exc), pointer="/body") from exc
    return out


def _snapshot(t: DocumentTemplate, principal: uuid.UUID) -> None:
    DocumentTemplateVersion.objects.create(
        tenant_id=t.tenant_id,
        company_id=t.company_id,
        template=t,
        version=t.version,
        body=t.body,
        body_sha256=hashlib.sha256(t.body.encode()).hexdigest(),
        created_by=principal,
    )


def create_template(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> DocumentTemplate:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _policy().require(ctx, f"{TEMPLATE}.create")
    values = _validated(data, partial=False)
    if DocumentTemplate.objects.filter(company_id=company_id, code=values["code"]).exists():
        raise Conflict(f"a template with code {values['code']} already exists", code="validation_failed")
    try:
        t = DocumentTemplate.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            created_by=ctx.principal_id,
            updated_by=ctx.principal_id,
            **values,
        )
    except IntegrityError as exc:
        raise Conflict("a template with this code already exists", code="validation_failed") from exc
    _snapshot(t, ctx.principal_id)
    audit(uow, "create", TEMPLATE, t.id, company_id, code=t.code, version=t.version)
    return t


def update_template(uow: UnitOfWork, t: DocumentTemplate, data: Mapping[str, Any]) -> DocumentTemplate:
    _policy().require(uow.ctx, f"{TEMPLATE}.update")
    if not data:
        raise ValidationFailed("at least one property is required")
    values = _validated(data, partial=True)
    if t.status != Status.ACTIVE:
        raise Conflict("an archived template cannot change", code="invalid_state_transition")
    if "code" in values and values["code"] != t.code:
        if DocumentTemplate.objects.filter(company_id=t.company_id, code=values["code"]).exclude(id=t.id).exists():
            raise Conflict(f"a template with code {values['code']} already exists", code="validation_failed")
    changed = [k for k, v in values.items() if getattr(t, k) != v]
    for k in changed:
        setattr(t, k, values[k])
    if changed:
        t.save_versioned(uow.ctx.principal_id, changed)
        _snapshot(t, uow.ctx.principal_id)
        audit(uow, "update", TEMPLATE, t.id, t.company_id, fields=changed, version=t.version)
    return t


def archive_template(uow: UnitOfWork, t: DocumentTemplate) -> None:
    """DELETE: templates are archived (rendered documents keep their template version), never removed."""
    _policy().require(uow.ctx, f"{TEMPLATE}.delete")
    if t.status == Status.ARCHIVED:
        return
    t.status = Status.ARCHIVED
    t.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "archive", TEMPLATE, t.id, t.company_id, version=t.version)


def template_json(t: DocumentTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "tenant_id": str(t.tenant_id),
        "company_id": str(t.company_id),
        "code": t.code,
        "name": t.name,
        "status": t.status,
        "document_type": t.document_type,
        "engine": t.engine,
        "body": t.body,
        "locale": t.locale,
        "version": t.version,
        "audit": {
            "created_at": t.created_at.isoformat(),
            "created_by": str(t.created_by) if t.created_by else None,
            "updated_at": t.updated_at.isoformat(),
            "updated_by": str(t.updated_by) if t.updated_by else None,
        },
    }


# --------------------------------------------------------------------------- rendering


@dataclass(frozen=True, slots=True)
class Rendered:
    document: RenderedDocument
    stored: GeneratedDocument | None
    sha256: str


def resolve_template(company_id: uuid.UUID, code: str) -> tuple[str | None, str]:
    """``(body, version)`` of the company's active template ``code`` (``None`` = use the built-in renderer)."""
    t = DocumentTemplate.objects.filter(company_id=company_id, code=code, status=Status.ACTIVE).first()
    if t is None:
        return None, builtin.builtin(code).version if builtin.builtin(code) else "builtin"
    return t.body, f"c{t.version}"


def _render(
    company_id: uuid.UUID, code: str, data: Mapping[str, Any], media_type: str, watermark: str | None
) -> RenderedDocument:
    body, version = resolve_template(company_id, code)
    try:
        if body is None:
            return RENDERER.render(code, dict(data), media_type=media_type, watermark=watermark)  # type: ignore[arg-type]  # media_type is validated by callers
        return render_body(body, code, version, data, media_type=media_type, watermark=watermark)  # type: ignore[arg-type]  # validated by callers
    except TemplateError as exc:
        raise TemplateRenderFailed(str(exc)) from exc


def _media(value: Any) -> str:
    media = value or "application/pdf"
    if media not in ("application/pdf", "text/html"):
        raise ValidationFailed("media_type is application/pdf or text/html", pointer="/parameters/media_type")
    return str(media)


def store(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    doc: RenderedDocument,
    *,
    source_type: str = "",
    source_id: uuid.UUID | None = None,
    source_number: str = "",
) -> GeneratedDocument:
    """Store once per (source, template, hash): re-rendering identical content returns the stored row."""
    digest = hashlib.sha256(doc.content).hexdigest()
    existing = GeneratedDocument.objects.filter(
        company_id=company_id,
        source_type=source_type,
        source_id=source_id,
        template_code=doc.template_code,
        sha256=digest,
    ).first()
    if existing is not None:
        return existing
    row = GeneratedDocument.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        template_code=doc.template_code,
        template_version=doc.template_version,
        media_type=doc.media_type,
        source_type=source_type,
        source_id=source_id,
        source_number=source_number,
        sha256=digest,
        size=len(doc.content),
        content=doc.content,
        watermark=doc.watermark or "",
        created_by=uow.ctx.principal_id,
    )
    audit(uow, "render", "documents.document", row.id, company_id, template=doc.template_code,
          version=doc.template_version, sha256=digest, source_type=source_type)  # fmt: skip
    return row


def render_source(
    uow: UnitOfWork, company_id: uuid.UUID, source_type: str, source_id: uuid.UUID, *, media_type: str | None = None
) -> Rendered:
    """Render a source record's document (RC/CE …) and store it linked to the record (with its hash)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    _policy().require(ctx, f"{RENDER}.export")
    src = sources.load(ctx, company_id, source_type, source_id)
    doc = _render(company_id, src.template_code, src.data, _media(media_type), src.watermark)
    row = store(uow, company_id, doc, source_type=source_type, source_id=source_id, source_number=src.number)
    return Rendered(doc, row, row.sha256)


def render_data(
    uow: UnitOfWork, company_id: uuid.UUID, template_code: str, data: Mapping[str, Any], *, media_type: str | None = None
) -> Rendered:
    """Render explicit data with a template code (company template or built-in); stored without a source link."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    _policy().require(ctx, f"{RENDER}.export")
    if not isinstance(data, Mapping):
        raise ValidationFailed("data must be an object", pointer="/parameters/data")
    doc = _render(company_id, template_code, data, _media(media_type), None)
    row = store(uow, company_id, doc)
    return Rendered(doc, row, row.sha256)


def render_request(uow: UnitOfWork, company_id: uuid.UUID, params: Mapping[str, Any]) -> Rendered:
    """``POST /render`` parameters: ``{source_type, source_id}`` or ``{template_code, data}`` (+ ``media_type``)."""
    if params.get("source_type"):
        try:
            sid = uuid.UUID(str(params.get("source_id")))
        except ValueError as exc:
            raise ValidationFailed("source_id must be a UUID", pointer="/parameters/source_id") from exc
        return render_source(uow, company_id, str(params["source_type"]), sid, media_type=params.get("media_type"))
    code = params.get("template_code")
    if not isinstance(code, str) or not code:
        raise ValidationFailed("template_code or source_type is required", pointer="/parameters/template_code")
    return render_data(uow, company_id, code, params.get("data") or {}, media_type=params.get("media_type"))


def preview(uow: UnitOfWork, t: DocumentTemplate, sample: Mapping[str, Any] | None) -> dict[str, Any]:
    """SimulationResult of rendering ``t`` against sample data (built-in sample by default); never persisted."""
    _policy().require(uow.ctx, f"{TEMPLATE}.read")
    data = sample if sample is not None else (builtin.builtin(t.code).sample if builtin.builtin(t.code) else {})
    if not isinstance(data, Mapping):
        raise ValidationFailed("sample must be an object", pointer="/parameters/sample")
    try:
        pdf = render_body(t.body, t.code, f"c{t.version}", data)
        html = render_body(t.body, t.code, f"c{t.version}", data, media_type="text/html")
    except TemplateError as exc:
        raise TemplateRenderFailed(str(exc), pointer="/body") from exc
    from documents.pdf.reader import page_texts

    return {
        "persisted": False,
        "documents": [
            {
                "template_code": t.code,
                "template_version": pdf.template_version,
                "media_type": "application/pdf",
                "sha256": hashlib.sha256(pdf.content).hexdigest(),
                "pages": len(page_texts(pdf.content)),
                "html": html.content.decode(),
            }
        ],
        "journal_entries": [],
        "stock_moves": [],
        "errors": [],
        "explain": [f"plantilla {t.code} v{t.version} renderizada con datos de ejemplo"],
    }


# --------------------------------------------------------------------------- batches (202)


def create_batch(uow: UnitOfWork, company_id: uuid.UUID, params: Mapping[str, Any]) -> RenderBatch:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _policy().require(ctx, f"{RENDER}.export")
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationFailed("parameters.items is a non-empty list", pointer="/parameters/items")
    if len(items) > MAX_BATCH:
        raise ValidationFailed(f"at most {MAX_BATCH} documents per batch", pointer="/parameters/items",
                               code="bulk_limit_exceeded")  # fmt: skip
    for n, item in enumerate(items):
        if not isinstance(item, Mapping) or not (item.get("source_type") or item.get("template_code")):
            raise ValidationFailed("each item needs source_type+source_id or template_code+data",
                                   pointer=f"/parameters/items/{n}")  # fmt: skip
    batch = RenderBatch.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        requests=[dict(i) for i in items],
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "create", "documents.render_batch", batch.id, company_id, items=len(items))
    return batch


def run_batch(uow: UnitOfWork, batch: RenderBatch) -> RenderBatch:
    """Render every item with the batch creator's context (same permissions as the synchronous render)."""
    batch.status = RenderBatch.State.RUNNING
    batch.started_at = clock.now()
    ids: list[str] = []
    try:
        for item in batch.requests:
            stored = render_request(uow, batch.company_id, item).stored
            if stored is not None:
                ids.append(str(stored.id))
    except DomainError as exc:
        batch.status = RenderBatch.State.FAILED
        batch.error = {"code": exc.code, "detail": str(exc)}
    else:
        batch.status = RenderBatch.State.SUCCEEDED
        batch.document_ids = ids
    batch.finished_at = clock.now()
    batch.save(update_fields=["status", "started_at", "finished_at", "document_ids", "error"])
    return batch


def operation_json(batch: RenderBatch) -> dict[str, Any]:
    return {
        "id": str(batch.id),
        "status": batch.status,
        "kind": "documents_render_render_batch",
        "progress": "100" if batch.status in ("SUCCEEDED", "FAILED") else "0",
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "error": batch.error,
        "result_links": [{"rel": "document", "href": f"urn:nexus:documents.document:{d}"} for d in batch.document_ids],
    }
