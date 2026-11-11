"""Import pipeline (P18): upload → map → validate (dry-run) → commit in atomic batches; cancel; resume.

* **Same service layer.** Rows are applied through the owner module's service functions (template adapters);
  the same permission codes as the target module are checked per row (create vs update, column permissions).
* **Dry-run** executes the real service calls inside a transaction that is always rolled back; each row runs in
  its own savepoint so every row error is reported. A job with any row error is ``FAILED`` and applies
  **zero rows** (``import_validation_failed``).
* **Commit** re-applies the validated records in batches (``batch_size``; 0 = one all-or-nothing batch). Each
  batch is one unit of work that also inserts ``ImportBatch(job, batch_no)`` (unique) and advances
  ``batches_done``, so a crash between batches resumes at the next batch without duplicates.
* **Idempotent by batch hash.** The hash covers entity, mode and the canonical typed rows; committing the same
  batch twice in a company fails with ``import_duplicate_batch``.
* **Security.** Natural-key lookups are confined to the job's company (no cross-company references);
  reserved columns (``id``, ``tenant_id``, ``company_id`` …) reject the file; documents import as DRAFT.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from dataio import formats
from dataio.errors import (
    ImportDuplicateBatch,
    ImportFileRejected,
    ImportValidationFailed,
    InvalidStateTransition,
    PolicyDenied,
    RowError,
)
from dataio.models import ImportBatch, ImportJob, ImportMode, ImportStatus, MappingProfile
from dataio.templates import registry
from dataio.templates.base import Record, Template, format_value, parse_cell, same, topo_order
from kernel import clock, services
from kernel.errors import DomainError, NotFound, ValidationFailed
from kernel.idempotency import StoredResponse
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

RESERVED_COLUMNS = frozenset({"id", "tenant_id", "company_id", "empresa", "company", "tenant"})
EVENT_COMMITTED = "dataio.import.committed.v1"
MAX_PREVIEW = 1000


def run[T](fn: Callable[[UnitOfWork], T], ctx: TenantContext) -> T:
    """``run_in_unit_of_work`` without an Idempotency-Key (never a stored replay)."""
    result = run_in_unit_of_work(fn, ctx)
    if isinstance(result, StoredResponse):  # pragma: no cover - only with an idempotency key
        raise InvalidStateTransition("unexpected idempotent replay")
    return result


# --------------------------------------------------------------------------- policy


def allowed(ctx: TenantContext, action: str) -> bool:
    return bool(services.get(PolicyService).authorize(ctx, action).allowed)


def require(ctx: TenantContext, action: str) -> None:
    if not allowed(ctx, action):
        raise PolicyDenied(f"missing permission {action}")


def _audit(uow: UnitOfWork, action: str, job: ImportJob, changes: dict[str, Any]) -> None:
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(
            uow,
            AuditEntry(
                action=f"dataio.import.{action}",
                resource_type="dataio.import",
                resource_id=job.id,
                company_id=job.company_id,
                changes=changes,
            ),
        )


# --------------------------------------------------------------------------- jobs


def get_job(ctx: TenantContext, company_id: uuid.UUID, job_id: Any, *, lock: bool = False) -> ImportJob:
    ctx.require_company(company_id)
    try:
        jid = uuid.UUID(str(job_id))
    except ValueError:
        raise NotFound("import not found") from None
    qs = ImportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=jid)
    if lock:
        qs = qs.select_for_update()
    job = qs.first()
    if job is None:
        raise NotFound("import not found")
    return job


def create_job(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    entity: str,
    *,
    mode: str = ImportMode.UPSERT,
    batch_size: int = 0,
    decimal_comma: bool = False,
) -> ImportJob:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "dataio.import.create")
    tpl = registry.get(entity)
    if mode not in ImportMode.values:
        raise ValidationFailed("mode must be create_only, update_only or upsert", pointer="/mode", reason="enum")
    if not isinstance(batch_size, int) or not 0 <= batch_size <= 100_000:
        raise ValidationFailed("batch_size must be 0..100000", pointer="/batch_size", reason="range")
    job = ImportJob.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        entity=tpl.entity,
        mode=mode,
        batch_size=batch_size,
        decimal_comma=bool(decimal_comma),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _audit(uow, "create", job, {"entity": tpl.entity, "mode": mode})
    return job


def _save(job: ImportJob, ctx: TenantContext, fields: list[str]) -> None:
    job.save_versioned(ctx.principal_id, fields)


def _expect(job: ImportJob, *states: str) -> None:
    if job.import_status not in states:
        raise InvalidStateTransition(f"import is {job.import_status}; expected {' or '.join(states)}")


def upload(uow: UnitOfWork, job: ImportJob, filename: str, data: bytes, *, profile_id: Any = None) -> ImportJob:
    """Parse the file (size/bomb/zip-slip checks in :mod:`dataio.formats`) and auto-map its columns."""
    ctx = uow.ctx
    require(ctx, "dataio.import.create")
    _expect(job, ImportStatus.UPLOADED)
    if job.rows:
        raise InvalidStateTransition("a file was already uploaded to this import")
    tpl = registry.get(job.entity)
    tables = formats.parse(filename, data, tpl.entity)
    if len(tables) > 1:
        tables = [t for t in tables if t.sheet.rsplit(".", 1)[0].rsplit("/", 1)[-1] == tpl.entity] or tables[:1]
    table = tables[0]
    reserved = sorted({h for h in table.headers if _norm(h) in RESERVED_COLUMNS})
    if reserved:
        raise ImportFileRejected(
            f"reserved column(s) {', '.join(reserved)}: the company and ids are never taken from a file",
            reason="reserved_column",
        )
    job.filename = (filename or "")[:255]
    job.file_format = formats.detect_format(filename, data)
    job.file_hash = hashlib.sha256(data).hexdigest()
    job.headers = table.headers
    job.rows = table.rows
    job.row_count = len(table.rows)
    mapping = auto_map(tpl, table.headers)
    if profile_id:
        profile = MappingProfile.objects.filter(
            tenant_id=ctx.tenant_id, company_id=job.company_id, id=profile_id, entity=tpl.entity, active=True
        ).first()
        if profile is None:
            raise NotFound("mapping profile not found")
        mapping.update({k: v for k, v in profile.mapping.items() if k in table.headers})
    job.mapping = mapping
    _save(job, ctx, ["filename", "file_format", "file_hash", "headers", "rows", "row_count", "mapping"])
    _audit(uow, "upload", job, {"rows": job.row_count, "file_hash": job.file_hash})
    return job


def set_mapping(uow: UnitOfWork, job: ImportJob, mapping: dict[str, Any]) -> ImportJob:
    """Explicit mapping ``{file header: template column | ""}`` (``""`` ignores the header)."""
    ctx = uow.ctx
    require(ctx, "dataio.import.update")
    _expect(job, ImportStatus.UPLOADED, ImportStatus.MAPPED)
    tpl = registry.get(job.entity)
    if not isinstance(mapping, dict):
        raise ValidationFailed("mapping must be an object", pointer="/mapping", reason="type")
    names = {c.name for c in tpl.columns}
    merged = dict(job.mapping)
    for header, col in mapping.items():
        if header not in job.headers:
            raise ValidationFailed(f"unknown file column {header!r}", pointer="/mapping", reason="unknown_header")
        if col not in names and col not in ("", None):
            raise ValidationFailed(f"unknown template column {col!r}", pointer="/mapping", reason="unknown_column")
        merged[header] = col or ""
    job.mapping = merged
    job.import_status = ImportStatus.MAPPED
    _save(job, ctx, ["mapping", "import_status"])
    return job


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


SYNONYMS = {
    "code": ("codigo", "cod", "code"),
    "name": ("nombre", "name"),
    "description": ("descripcion", "detalle_descripcion", "desc"),
    "parent_code": ("padre", "codigo_padre", "parent"),
    "node_type": ("tipo_nodo", "tipo", "agrupador_detalle"),
    "valid_from": ("vigente_desde", "desde", "fecha_inicio"),
    "valid_to": ("vigente_hasta", "hasta", "fecha_fin"),
    "status": ("estado",),
    "quantity": ("cantidad",),
    "item_code": ("sku", "item", "producto", "codigo_item", "referencia"),
    "warehouse_code": ("bodega", "codigo_bodega"),
    "account_role": ("rol_contable", "cuenta"),
    "factor": ("factor_conversion",),
}


def auto_map(tpl: Template, headers: list[str]) -> dict[str, str]:
    """Header → column by normalized name, then by Spanish/English synonyms; unknown headers map to ``""``."""
    by_norm = {_norm(c.name): c.name for c in tpl.columns}
    for c in tpl.columns:
        for syn in (*SYNONYMS.get(c.name, ()), *c.synonyms):
            by_norm.setdefault(_norm(syn), c.name)
    out: dict[str, str] = {}
    used: set[str] = set()
    for h in headers:
        col = by_norm.get(_norm(h), "")
        if col in used:
            col = ""
        if col:
            used.add(col)
        out[h] = col
    return out


# --------------------------------------------------------------------------- records


@dataclass
class Built:
    records: list[Record]
    errors: list[dict[str, Any]]
    canonical: list[list[str]]


def _err(row: int, code: str, message: str, column: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"row": row, "code": code, "message": message}
    if column:
        out["column"] = column
    return out


def build_records(tpl: Template, job: ImportJob) -> Built:
    """Typed records of the job (row numbers are spreadsheet rows: header = 1)."""
    errors: list[dict[str, Any]] = []
    mapping = job.mapping or auto_map(tpl, job.headers)
    col_of_index: dict[int, str] = {}
    for i, h in enumerate(job.headers):
        col = mapping.get(h, "")
        if col:
            col_of_index[i] = col
        elif h.strip():
            errors.append(_err(1, "unknown_column", f"columna {h!r} no pertenece a la plantilla {tpl.entity}", h))
    present = frozenset(col_of_index.values())
    for c in tpl.columns:
        if c.required and c.name not in present:
            errors.append(_err(1, "missing_column", f"falta la columna obligatoria {c.name}", c.name))
    if errors:
        return Built([], errors, [])
    by_key: dict[tuple[Any, ...], Record] = {}
    order: list[Record] = []
    canonical: list[list[str]] = []
    line_cols = {c.name for c in tpl.line_columns()}
    for n, raw in enumerate(job.rows, start=2):
        values: dict[str, Any] = {}
        row_ok = True
        for i, col in col_of_index.items():
            column = tpl.column(col)
            try:
                values[col] = parse_cell(column, raw[i] if i < len(raw) else "", decimal_comma=job.decimal_comma)
            except RowError as exc:
                errors.append(_err(n, exc.code, exc.message, exc.column))
                row_ok = False
        for c in tpl.columns:
            if c.required and c.name in present and values.get(c.name) is None and row_ok:
                errors.append(_err(n, "required", f"{c.name} es obligatorio", c.name))
                row_ok = False
        if not row_ok:
            continue
        canonical.append([f"{k}={format_value(values[k])}" for k in sorted(values)])
        key = tuple(_key_part(values.get(k)) for k in tpl.natural_key)
        head = {k: v for k, v in values.items() if k not in line_cols}
        line = {k: v for k, v in values.items() if k in line_cols}
        if line_cols:
            rec = by_key.get(key)
            if rec is None:
                rec = Record(key, head, frozenset(present - line_cols), [n], [line])
                by_key[key] = rec
                order.append(rec)
            else:
                for k, v in head.items():
                    if not same(rec.values.get(k), v):
                        errors.append(_err(n, "header_mismatch", f"{k} difiere de la primera fila del registro", k))
                rec.rows.append(n)
                rec.lines.append(line)
            continue
        if key in by_key:
            errors.append(
                _err(n, "duplicate_in_file", f"clave natural repetida en el archivo (fila {by_key[key].rows[0]})")
            )
            continue
        rec = Record(key, head, frozenset(present), [n])
        by_key[key] = rec
        order.append(rec)
    if tpl.parent_column:
        ordered, cyclic = topo_order(order, tpl.node_key, tpl.parent_key)
        for rec in cyclic:
            errors.append(
                _err(
                    rec.rows[0],
                    "hierarchy_cycle",
                    "ciclo de padres en el archivo (o descendiente de un ciclo)",
                    tpl.parent_column,
                )
            )
        order = ordered
    return Built(order, errors, canonical)


def _key_part(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def batch_hash(job: ImportJob, built: Built) -> str:
    payload = json.dumps({"entity": job.entity, "mode": job.mode, "rows": built.canonical}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# --------------------------------------------------------------------------- applying


def _column_permissions(ctx: TenantContext, tpl: Template, rec: Record) -> None:
    for name in rec.present | {k for ln in rec.lines for k in ln}:
        perm = tpl.column(name).permission
        if perm and not allowed(ctx, perm):
            raise RowError("field_forbidden", f"sin permiso para la columna {name} ({perm})", name)


def apply_record(uow: UnitOfWork, tpl: Template, company_id: uuid.UUID, mode: str, rec: Record) -> str:
    """Apply one record through the owner service; returns ``created``/``updated``/``unchanged``."""
    ctx = uow.ctx
    _column_permissions(ctx, tpl, rec)
    existing = tpl.find(ctx, company_id, rec)
    if existing is None:
        if mode == ImportMode.UPDATE_ONLY:
            raise RowError("not_found", "no existe un registro con esa clave en esta empresa")
        if not allowed(ctx, tpl.create_permission):
            raise RowError("policy_denied", f"sin permiso {tpl.create_permission}")
        tpl.create(uow, company_id, rec)
        return "created"
    if mode == ImportMode.CREATE_ONLY:
        raise RowError("duplicate", "ya existe un registro con esa clave (modo solo crear)")
    current = tpl.current(existing)
    changed = {k for k in rec.present if not same(current.get(k), rec.values.get(k))}
    if tpl.line_columns():
        cur_lines = tpl.current_lines(existing)
        line_names = {k for ln in rec.lines for k in ln}
        trimmed = [{k: ln.get(k) for k in line_names} for ln in cur_lines]
        if len(trimmed) != len(rec.lines) or not all(
            all(same(a.get(k), b.get(k)) for k in line_names) for a, b in zip(trimmed, rec.lines, strict=False)
        ):
            changed.add("__lines__")
    if not changed:
        return "unchanged"
    if not tpl.updatable:
        raise RowError(
            "not_updatable", f"{tpl.entity} no admite actualización por importación: {', '.join(sorted(changed))}"
        )
    if not allowed(ctx, tpl.update_permission):
        raise RowError("policy_denied", f"sin permiso {tpl.update_permission}")
    tpl.update(uow, existing, rec, changed)
    return "updated"


def _row_error(rec: Record, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, RowError):
        return _err(rec.rows[0], exc.code, exc.message, exc.column)
    if isinstance(exc, DomainError):
        pointer = (getattr(exc, "extra", {}) or {}).get("pointer")
        column = pointer.lstrip("/").split("/")[0] or None if isinstance(pointer, str) else None
        return _err(rec.rows[0], exc.code, exc.detail, column)
    return _err(rec.rows[0], "conflict", "conflicto de integridad en la base de datos")


def _apply_all(
    uow: UnitOfWork, tpl: Template, job: ImportJob, records: list[Record], collect: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Apply ``records``; each in a savepoint. ``collect`` keeps going after an error (dry-run)."""
    preview: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    for rec in records:
        try:
            with transaction.atomic(using=uow.using):
                action = apply_record(uow, tpl, job.company_id, job.mode, rec)
        except (RowError, DomainError, IntegrityError) as exc:
            errors.append(_row_error(rec, exc))
            counts["failed"] += 1
            if not collect:
                break
            continue
        counts[action] += 1
        if len(preview) < MAX_PREVIEW:
            preview.append({"rows": rec.rows, "key": [format_value(k) for k in rec.key], "action": action})
    return preview, errors, counts


class _Rollback(Exception):
    def __init__(self, result: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]) -> None:
        self.result = result


def dry_run(ctx: TenantContext, company_id: uuid.UUID, job_id: Any) -> ImportJob:
    """Validate: types + the real service calls in a rolled-back transaction. Stores preview/errors."""
    require(ctx, "dataio.import.update")
    job = run(lambda u: get_job(u.ctx, company_id, job_id), ctx)
    _expect(job, ImportStatus.UPLOADED, ImportStatus.MAPPED, ImportStatus.VALIDATED)
    if not job.headers:
        raise InvalidStateTransition("upload a file first")
    tpl = registry.get(job.entity)
    built = build_records(tpl, job)
    result: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]] = ([], [], {})
    if built.records:

        def simulate(uow: UnitOfWork) -> None:
            raise _Rollback(_apply_all(uow, tpl, job, built.records, collect=True))

        try:
            run(simulate, ctx)
        except _Rollback as rb:
            result = rb.result
    preview, apply_errors, counts = result
    errors = sorted(built.errors + apply_errors, key=lambda e: (e["row"], e.get("column") or ""))
    digest = batch_hash(job, built) if not errors else ""

    def store(uow: UnitOfWork) -> ImportJob:
        j = get_job(uow.ctx, company_id, job.id, lock=True)
        _expect(j, ImportStatus.UPLOADED, ImportStatus.MAPPED, ImportStatus.VALIDATED)
        j.errors = errors
        j.error_count = len(errors)
        j.preview = preview
        j.counts = counts
        j.batch_hash = digest
        fields = ["errors", "error_count", "preview", "counts", "batch_hash", "import_status"]
        if errors:
            j.import_status = ImportStatus.FAILED
            j.failed_at = clock.now()
            fields.append("failed_at")
        else:
            j.import_status = ImportStatus.VALIDATED
        _save(j, uow.ctx, fields)
        _audit(uow, "validate", j, {"errors": len(errors), "counts": counts})
        return j

    stored: ImportJob = run(store, ctx)
    return stored


def commit(ctx: TenantContext, company_id: uuid.UUID, job_id: Any) -> ImportJob:
    """Validate (if needed) and apply in batches; resumable. Raises ``import_validation_failed`` on row errors."""
    require(ctx, "dataio.import.post")
    job = run(lambda u: get_job(u.ctx, company_id, job_id), ctx)
    if job.import_status == ImportStatus.COMMITTED:
        return job
    if job.import_status in (ImportStatus.UPLOADED, ImportStatus.MAPPED):
        job = dry_run(ctx, company_id, job.id)
    if job.import_status == ImportStatus.FAILED:
        raise ImportValidationFailed(f"{job.error_count} error(es) por fila; no se aplicó ninguna fila", job.errors)
    _expect(job, ImportStatus.VALIDATED)
    duplicate = run(
        lambda u: (
            ImportJob.objects.filter(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                entity=job.entity,
                batch_hash=job.batch_hash,
                import_status=ImportStatus.COMMITTED,
                effective=True,
            )
            .exclude(id=job.id)
            .values_list("id", flat=True)
            .first()
        ),
        ctx,
    )
    would_change = int(job.counts.get("created", 0)) + int(job.counts.get("updated", 0)) > 0
    if duplicate is not None and would_change:
        raise ImportDuplicateBatch(f"this batch was already imported (import {duplicate})", import_id=str(duplicate))
    tpl = registry.get(job.entity)
    built = build_records(tpl, job)
    size = job.batch_size or max(len(built.records), 1)
    batches = [built.records[i : i + size] for i in range(0, len(built.records), size)] or [[]]
    for batch_no, records in enumerate(batches, start=1):
        if batch_no <= job.batches_done:
            continue
        _commit_batch(ctx, company_id, job.id, tpl, batch_no, len(batches), records)
        job = run(lambda u: get_job(u.ctx, company_id, job_id), ctx)
        if job.import_status == ImportStatus.FAILED:
            raise ImportValidationFailed(f"batch {batch_no} failed; earlier batches stay applied", job.errors)
    return job


#: test hook: called after a batch's rows were applied, inside its transaction (failure injection)
after_batch_hook: Callable[[int], None] | None = None


def _commit_batch(
    ctx: TenantContext,
    company_id: uuid.UUID,
    job_id: uuid.UUID,
    tpl: Template,
    batch_no: int,
    total: int,
    records: list[Record],
) -> None:
    def fn(uow: UnitOfWork) -> None:
        job = get_job(uow.ctx, company_id, job_id, lock=True)
        if (
            job.import_status != ImportStatus.VALIDATED
            or ImportBatch.objects.filter(job=job, batch_no=batch_no).exists()
        ):
            return
        preview, errors, counts = _apply_all(uow, tpl, job, records, collect=False)
        if errors:
            raise _Rollback((preview, errors, counts))
        ImportBatch.objects.create(
            tenant_id=job.tenant_id,
            company_id=job.company_id,
            job=job,
            batch_no=batch_no,
            rows=sum(len(r.rows) for r in records),
            counts=counts,
            committed_at=clock.now(),
        )
        if after_batch_hook is not None:
            after_batch_hook(batch_no)
        merged = {
            k: int(job.counts.get(k, 0)) if batch_no > 1 else 0 for k in ("created", "updated", "unchanged", "failed")
        }
        for k, v in counts.items():
            merged[k] = merged.get(k, 0) + v
        job.counts = merged
        job.batches_done = batch_no
        job.batches_total = total
        job.applied_rows = (job.applied_rows if batch_no > 1 else 0) + sum(len(r.rows) for r in records)
        job.results = (job.results if batch_no > 1 else []) + preview[: max(0, MAX_PREVIEW - len(job.results))]
        fields = ["counts", "batches_done", "batches_total", "applied_rows", "results"]
        if batch_no == total:
            job.import_status = ImportStatus.COMMITTED
            job.committed_at = clock.now()
            job.effective = merged.get("created", 0) + merged.get("updated", 0) > 0
            fields += ["import_status", "committed_at", "effective"]
            uow.emit(
                EVENT_COMMITTED,
                {
                    "import_id": str(job.id),
                    "entity": job.entity,
                    "rows": job.applied_rows,
                    "batch_hash": job.batch_hash,
                },
                company_id=job.company_id,
            )
        _save(job, uow.ctx, fields)
        _audit(uow, "commit_batch", job, {"batch_no": batch_no, "counts": counts})

    try:
        run(fn, ctx)
    except _Rollback as rb:
        _, errors, _ = rb.result

        def fail(uow: UnitOfWork) -> None:
            job = get_job(uow.ctx, company_id, job_id, lock=True)
            job.errors = errors
            job.error_count = len(errors)
            job.import_status = ImportStatus.FAILED
            job.failed_at = clock.now()
            _save(job, uow.ctx, ["errors", "error_count", "import_status", "failed_at"])
            _audit(uow, "commit_failed", job, {"batch_no": batch_no, "errors": len(errors)})

        run(fail, ctx)


def cancel(uow: UnitOfWork, job: ImportJob) -> ImportJob:
    require(uow.ctx, "dataio.import.void")
    if job.import_status == ImportStatus.CANCELLED:
        return job
    _expect(job, ImportStatus.UPLOADED, ImportStatus.MAPPED, ImportStatus.VALIDATED)
    if job.batches_done:
        raise InvalidStateTransition("batches were already committed; the import can only be resumed")
    job.import_status = ImportStatus.CANCELLED
    _save(job, uow.ctx, ["import_status"])
    _audit(uow, "cancel", job, {})
    return job


def report_rows(job: ImportJob) -> tuple[list[str], list[list[str]]]:
    """Result report: the uploaded rows + ``_resultado`` / ``_error`` columns (downloadable as XLSX/CSV)."""
    by_row: dict[int, list[str]] = {}
    for e in job.errors:
        by_row.setdefault(int(e["row"]), []).append(f"[{e['code']}] {e.get('column') or ''} {e['message']}".strip())
    action: dict[int, str] = {}
    if job.import_status == ImportStatus.COMMITTED:
        source, label = job.results, "{}"
    elif job.import_status == ImportStatus.FAILED:
        source, label = [], "{}"  # nothing applied: every non-error row is "omitido"
    else:
        source, label = job.preview, "previsto:{}"
    for p in source:
        for r in p["rows"]:
            action.setdefault(int(r), label.format(p["action"]))
    headers = [*job.headers, "_resultado", "_error"]
    rows = []
    for n, raw in enumerate(job.rows, start=2):
        default = "omitido" if job.import_status in (ImportStatus.FAILED, ImportStatus.CANCELLED) else "pendiente"
        status = "error" if n in by_row else action.get(n, default)
        rows.append([*raw, status, " | ".join(by_row.get(n, []))])
    header_errors = by_row.get(1)
    if header_errors:
        rows.insert(0, [""] * len(job.headers) + ["error", " | ".join(header_errors)])
    return headers, rows
