"""Saved reports (custom report builder): CRUD, ``:run``, ``:schedule``, ``:publish``, ``:archive``; report runs.

* Visibility: the owner sees its reports; a *published* report is visible to every principal holding
  ``reporting.report.read`` within the policy's company row scope (``definition.share_roles`` is stored for the
  P04 role-sharing filter, CCR P17a-3). Running it still applies the runner's own row/field policy.
* A run always executes the stored semantic query through :func:`reporting.engine.run` with the **runner's current
  permissions** — a scheduled run uses the owner's current grants (P07 scheduler re-evaluates them), so a revoked
  field or row scope is never bypassed by an old schedule (T-P17-09).
* Runs are immutable snapshots carrying the data-version cut-off (``ledger_cutoff_seq``) and emit
  ``reporting.report_run.ready.v1``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from django.db import IntegrityError
from django.db.models import Q

from kernel import clock, services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.scheduler import SchedulerService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from reporting import engine
from reporting.compiler import freshness
from reporting.models import ReportRun, SavedReport
from reporting.semantic import registry

RESOURCE = "reporting.report"
FIELDS = ("code", "name", "definition", "schedule", "recipients")
VIEWS = ("table", "pivot", "bar", "line")
DEFINITION_KEYS = ("query", "view", "pivot", "formatting", "share_roles", "parameters", "title")
JOB_TYPE = "report_delivery"
CRON = re.compile(r"^[\d*/,\-]+( [\d*/,\-A-Za-z]+){4}$")


def _policy() -> PolicyService:
    return services.get(PolicyService)


def _audit(uow: UnitOfWork, action: str, r: SavedReport, **changes: Any) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, RESOURCE, r.id, r.company_id, dict(changes)))


def visible(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    _policy().require(ctx, f"{RESOURCE}.read")
    qs = _policy().scope_queryset(ctx, f"{RESOURCE}.read", SavedReport.objects.filter(company_id=company_id))
    me = ctx.on_behalf_of or ctx.principal_id
    return qs.filter(Q(owner_id=me) | Q(published_at__isnull=False))


def get(ctx: TenantContext, company_id: uuid.UUID, report_id: uuid.UUID) -> SavedReport:
    r: SavedReport | None = visible(ctx, company_id).filter(id=report_id).first()
    if r is None:
        raise NotFound("report not found")
    return r


def _owned(ctx: TenantContext, r: SavedReport) -> None:
    if (
        r.owner_id != (ctx.on_behalf_of or ctx.principal_id)
        and not _policy().authorize(ctx, "reporting.report.admin").allowed
    ):
        raise NotFound("report not found")  # someone else's report: no existence oracle on writes


def _definition(ctx: TenantContext, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed("definition must be an object", pointer="/definition")
    unknown = sorted(set(raw) - set(DEFINITION_KEYS))
    if unknown:
        raise ValidationFailed(f"unknown definition keys: {', '.join(unknown)}", pointer=f"/definition/{unknown[0]}")
    query = raw.get("query")
    if not isinstance(query, Mapping):
        raise ValidationFailed("definition.query is the semantic query object", pointer="/definition/query")
    engine.parse_spec(ctx, query)  # validates names, masked fields and the entity permission of the author
    view = raw.get("view", "table")
    if view not in VIEWS:
        raise ValidationFailed(f"definition.view is one of {', '.join(VIEWS)}", pointer="/definition/view")
    roles = raw.get("share_roles", [])
    if not isinstance(roles, list) or not all(isinstance(x, str) for x in roles):
        raise ValidationFailed("share_roles is a list of role keys", pointer="/definition/share_roles")
    return {**dict(raw), "view": view, "share_roles": roles}


def _schedule(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 255 or not CRON.match(value.strip()):
        raise ValidationFailed("schedule is a 5-field cron expression", pointer="/schedule")
    return value.strip()


def _recipients(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValidationFailed("recipients is a list of user ids (≤ 100)", pointer="/recipients")
    try:
        return [str(uuid.UUID(str(v))) for v in value]
    except ValueError as exc:
        raise ValidationFailed("recipients are user ids (UUID)", pointer="/recipients") from exc


def _values(ctx: TenantContext, data: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", pointer=f"/{unknown[0]}")
    out: dict[str, Any] = {}
    for key, limit in (("code", 40), ("name", 200)):
        if key in data or not partial:
            v = data.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > limit:
                raise ValidationFailed(f"{key} is a text of 1–{limit} characters", pointer=f"/{key}")
            out[key] = v.strip()
    if "definition" in data or not partial:
        out["definition"] = _definition(ctx, data.get("definition"))
    if "schedule" in data:
        out["schedule"] = _schedule(data.get("schedule"))
    if "recipients" in data:
        out["recipients"] = _recipients(data.get("recipients"))
    return out


def create(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> SavedReport:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _policy().require(ctx, f"{RESOURCE}.create")
    values = _values(ctx, data, partial=False)
    if SavedReport.objects.filter(company_id=company_id, code=values["code"]).exists():
        raise Conflict(f"a report with code {values['code']} already exists", code="validation_failed")
    try:
        r = SavedReport.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, owner_id=ctx.on_behalf_of or ctx.principal_id,
            created_by=ctx.principal_id, updated_by=ctx.principal_id, **values,
        )  # fmt: skip
    except IntegrityError as exc:
        raise Conflict("a report with this code already exists", code="validation_failed") from exc
    _audit(uow, "create", r, code=r.code)
    return r


def update(uow: UnitOfWork, r: SavedReport, data: Mapping[str, Any]) -> SavedReport:
    _policy().require(uow.ctx, f"{RESOURCE}.update")
    _owned(uow.ctx, r)
    if r.status == SavedReport.Status.ARCHIVED:
        raise Conflict("an archived report cannot change", code="invalid_state_transition")
    if not data:
        raise ValidationFailed("at least one property is required")
    values = _values(uow.ctx, data, partial=True)
    changed = [k for k, v in values.items() if getattr(r, k) != v]
    for k in changed:
        setattr(r, k, values[k])
    if changed:
        r.save_versioned(uow.ctx.principal_id, changed)
        _audit(uow, "update", r, fields=changed)
    return r


def delete(uow: UnitOfWork, r: SavedReport) -> None:
    """Only unused reports are deleted (contract: "solo sin uso"); a report with runs is archived instead."""
    _policy().require(uow.ctx, f"{RESOURCE}.delete")
    _owned(uow.ctx, r)
    if r.runs.exists() or r.scheduled_job_id:
        raise Conflict("the report has runs or a schedule: archive it", code="invalid_state_transition")
    _audit(uow, "delete", r, code=r.code)
    r.delete()


def publish(uow: UnitOfWork, r: SavedReport) -> SavedReport:
    _policy().require(uow.ctx, f"{RESOURCE}.update")
    _owned(uow.ctx, r)
    if r.status != SavedReport.Status.ACTIVE:
        raise Conflict("only active reports are published", code="invalid_state_transition")
    if r.published_at is None:
        r.published_at = clock.now()
        r.save_versioned(uow.ctx.principal_id, ["published_at"])
        _audit(uow, "publish", r)
    return r


def archive(uow: UnitOfWork, r: SavedReport) -> SavedReport:
    _policy().require(uow.ctx, f"{RESOURCE}.update")
    _owned(uow.ctx, r)
    if r.status == SavedReport.Status.ARCHIVED:
        return r
    if r.scheduled_job_id:
        sched = services.optional(SchedulerService)
        if sched is not None:
            sched.cancel(uow.ctx, r.scheduled_job_id)
        r.scheduled_job_id = None
    r.status = SavedReport.Status.ARCHIVED
    r.save_versioned(uow.ctx.principal_id, ["status", "scheduled_job_id"])
    _audit(uow, "archive", r)
    return r


def schedule(uow: UnitOfWork, r: SavedReport, params: Mapping[str, Any]) -> SavedReport:
    """Schedule delivery through P07 (job type ``report_delivery``; runs with the owner's current permissions)."""
    _policy().require(uow.ctx, f"{RESOURCE}.update")
    _owned(uow.ctx, r)
    if r.status != SavedReport.Status.ACTIVE:
        raise Conflict("only active reports are scheduled", code="invalid_state_transition")
    cron = _schedule(params.get("cron") or r.schedule)
    if cron is None:
        raise ValidationFailed("parameters.cron (5-field cron) is required", pointer="/parameters/cron")
    fmt = params.get("format", "pdf")
    if fmt not in ("pdf", "csv", "xlsx", "json"):
        raise ValidationFailed("format is pdf, csv, xlsx or json", pointer="/parameters/format")
    sched = services.get(SchedulerService)
    job = sched.schedule(uow.ctx, r.company_id, f"reporting.report.{r.code}", cron, JOB_TYPE,
                         {"report_id": str(r.id), "format": fmt})  # fmt: skip
    r.schedule = cron
    if "recipients" in params:
        r.recipients = _recipients(params.get("recipients"))
    r.scheduled_job_id = job.id
    r.save_versioned(uow.ctx.principal_id, ["schedule", "recipients", "scheduled_job_id"])
    _audit(uow, "schedule", r, cron=cron, job_id=str(job.id))
    return r


def run(
    uow: UnitOfWork, r: SavedReport, *, trigger: str = "api", parameters: Mapping[str, Any] | None = None
) -> tuple[ReportRun, dict[str, Any]]:
    """Execute the stored query as the caller (current permissions); store the immutable run; emit the event."""
    ctx = uow.ctx
    if r.status != SavedReport.Status.ACTIVE:
        raise Conflict("archived reports do not run", code="invalid_state_transition")
    query = dict(r.definition.get("query") or {})
    overrides = dict(parameters or {})
    for key in ("filters", "as_of", "limit", "compare_to"):
        if key in overrides:
            query[key] = overrides[key]
    result = engine.run(ctx, [r.company_id], query, engine.QueryOptions(interactive=trigger != "scheduler"))
    ent = registry.entity(str(query.get("entity")))
    version = freshness(ent, [r.company_id]) if ent is not None else ""
    seq = int(version.split("|")[0]) if version and version.split("|")[0].isdigit() else 0
    snapshot = {k: v for k, v in result.items() if k != "cached"}
    row = ReportRun.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=r.company_id,
        report=r,
        report_key=r.code,
        state=ReportRun.State.READY,
        trigger=trigger,
        principal_id=ctx.principal_id,
        query=query,
        result=snapshot,
        row_count=len(result["rows"]),
        data_version=version[:120],
        ledger_cutoff_seq=seq,
    )
    uow.emit(
        "reporting.report_run.ready.v1",
        {"run_id": str(row.id), "report": r.code, "ledger_cutoff_seq": seq},
        company_id=r.company_id,
    )
    return row, {**result, "run_id": str(row.id), "ledger_cutoff_seq": seq}


def record_failure(uow: UnitOfWork, r: SavedReport, trigger: str, code: str, detail: str) -> ReportRun:
    return ReportRun.objects.create(
        tenant_id=uow.ctx.tenant_id, company_id=r.company_id, report=r, report_key=r.code,
        state=ReportRun.State.FAILED, trigger=trigger, principal_id=uow.ctx.principal_id,
        query=dict(r.definition.get("query") or {}), error={"code": code, "detail": detail[:500]},
    )  # fmt: skip


def scheduled_handler(ctx: TenantContext, job: Any, params: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """P07 ``report_delivery`` handler: runs inside the scheduler's UoW with the owner's current context."""
    from kernel.uow import require_uow

    uow = require_uow()
    rid = uuid.UUID(str(params.get("report_id")))
    r = SavedReport.objects.filter(id=rid, company_id=job.company_id).first()
    if r is None or r.status != SavedReport.Status.ACTIVE:
        return {"state": "skipped", "reason": "report not active"}
    row, _ = run(uow, r, trigger="scheduler")
    uow.emit("reporting.report.scheduled_run_completed.v1", {"report_id": str(r.id), "operation_id": str(row.id)},
             company_id=r.company_id)  # fmt: skip
    return {"run_id": str(row.id), "rows": row.row_count}


def report_json(r: SavedReport) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "tenant_id": str(r.tenant_id),
        "company_id": str(r.company_id),
        "code": r.code,
        "name": r.name,
        "status": r.status,
        "definition": {**r.definition, "published": r.published_at is not None},
        "schedule": r.schedule,
        "recipients": list(r.recipients or []),
        "version": r.version,
        "audit": {
            "created_at": r.created_at.isoformat(),
            "created_by": str(r.created_by) if r.created_by else None,
            "updated_at": r.updated_at.isoformat(),
            "updated_by": str(r.updated_by) if r.updated_by else None,
        },
    }
