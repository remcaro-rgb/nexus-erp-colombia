"""Exports (P18 Export): template entities to CSV/XLSX/JSONL with the target module's row policy and column
permissions applied, formula-injection protection, an XLSX watermark and an audit event with the row count.

The rows are read with ``PolicyService.scope_queryset(ctx, <template read permission>, qs)`` — the same check the
owner module's list endpoint applies — and columns guarded by an extra permission (cost columns) are left out
unless the principal holds it. The export file is the exact input of an import (export → import is a no-op).
"""

from __future__ import annotations

import uuid
from typing import Any

from dataio import formats
from dataio.errors import InvalidStateTransition, PolicyDenied
from dataio.models import ExportJob
from dataio.service import allowed, require
from dataio.templates import registry
from dataio.templates.base import Template, format_value
from kernel import clock, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

EVENT_COMPLETED = "dataio.export.completed.v1"


def visible_columns(ctx: TenantContext, tpl: Template) -> tuple[list[str], list[str]]:
    shown: list[str] = []
    omitted: list[str] = []
    for c in tpl.columns:
        (shown if not c.permission or allowed(ctx, c.permission) else omitted).append(c.name)
    return shown, omitted


def rows_for(ctx: TenantContext, company_id: uuid.UUID, tpl: Template) -> tuple[list[str], list[list[str]], list[str]]:
    """Header + rows of ``tpl`` in ``company_id`` as the principal may read them."""
    if not allowed(ctx, tpl.read_permission):
        raise PolicyDenied(f"missing permission {tpl.read_permission}")
    headers, omitted = visible_columns(ctx, tpl)
    qs = services.get(PolicyService).scope_queryset(ctx, tpl.read_permission, tpl.queryset(ctx, company_id))
    out: list[list[str]] = []
    for obj in qs:
        if getattr(obj, "company_id", company_id) != company_id:
            continue
        for row in tpl.export_rows(obj):
            out.append([format_value(row.get(h)) for h in headers])
    return headers, out, omitted


def get_export(ctx: TenantContext, company_id: uuid.UUID, export_id: Any) -> ExportJob:
    ctx.require_company(company_id)
    try:
        eid = uuid.UUID(str(export_id))
    except ValueError:
        raise NotFound("export not found") from None
    job = ExportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=eid).first()
    if job is None:
        raise NotFound("export not found")
    return job


def create_export(uow: UnitOfWork, company_id: uuid.UUID, source: str, fmt: str) -> ExportJob:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "dataio.export.create")
    tpl = registry.get(source)
    if fmt not in formats.FORMATS:
        raise ValidationFailed("format must be csv, xlsx or jsonl", pointer="/format", reason="enum")
    if not allowed(ctx, tpl.read_permission):
        raise PolicyDenied(f"missing permission {tpl.read_permission}")
    return ExportJob.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        source=tpl.entity,
        format=fmt,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )


def start_export(uow: UnitOfWork, job: ExportJob) -> ExportJob:
    """Generate the file (synchronous for the dev scale; same code path as an async worker)."""
    ctx = uow.ctx
    require(ctx, "dataio.export.update")
    if job.export_status == "COMPLETED":
        return job
    if job.export_status != "QUEUED":
        raise InvalidStateTransition(f"export is {job.export_status}")
    tpl = registry.get(job.source)
    headers, rows, omitted = rows_for(ctx, job.company_id, tpl)
    stamp = clock.now()
    job.content = formats.write(
        job.format, headers, rows, sheet=tpl.entity, watermark=f"Nexus ERP · {ctx.principal_id} · {stamp.isoformat()}"
    )
    job.content_type = formats.CONTENT_TYPES[job.format]
    job.filename = f"{tpl.entity}-{stamp:%Y%m%d%H%M%S}.{job.format}"
    job.row_count = len(rows)
    job.omitted_columns = omitted
    job.export_status = "COMPLETED"
    job.completed_at = stamp
    job.save_versioned(
        ctx.principal_id,
        ["content", "content_type", "filename", "row_count", "omitted_columns", "export_status", "completed_at"],
    )
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(
            uow,
            AuditEntry(
                action="dataio.export.completed",
                resource_type="dataio.export",
                resource_id=job.id,
                company_id=job.company_id,
                changes={"source": tpl.entity, "rows": len(rows), "format": job.format, "omitted_columns": omitted},
            ),
        )
    uow.emit(EVENT_COMPLETED, {"export_id": str(job.id), "rows": len(rows)}, company_id=job.company_id)
    return job


def file_of(ctx: TenantContext, job: ExportJob) -> tuple[bytes, str, str]:
    """The file; re-checks the source read permission (a revoked grant cannot download an old export)."""
    require(ctx, "dataio.export.export")
    if job.created_by != ctx.principal_id and not allowed(ctx, "dataio.export.read"):
        raise PolicyDenied("only the author or an export reader can download it")
    tpl = registry.get(job.source)
    if not allowed(ctx, tpl.read_permission):
        raise PolicyDenied(f"missing permission {tpl.read_permission}")
    if job.export_status != "COMPLETED" or job.content is None:
        raise InvalidStateTransition("the export has not been generated yet")
    return bytes(job.content), job.content_type, job.filename


def template_file(ctx: TenantContext, entity: str, fmt: str, with_examples: bool = True) -> tuple[bytes, str, str]:
    require(ctx, "dataio.template.read")
    tpl = registry.get(entity)
    if fmt not in formats.FORMATS:
        raise ValidationFailed("format must be csv, xlsx or jsonl", pointer="/format", reason="enum")
    headers = [c.name for c in tpl.columns]
    rows = [[e.get(h, "") for h in headers] for e in tpl.examples] if with_examples else []
    return (
        formats.write(fmt, headers, rows, sheet=tpl.entity),
        formats.CONTENT_TYPES[fmt],
        f"plantilla-{tpl.entity}.{fmt}",
    )
