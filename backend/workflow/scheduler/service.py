"""SchedulerService (owner P07).

* One leader per tick: ``pg_try_advisory_xact_lock`` — replicas that do not
  get the lock do nothing.
* At-least-once with idempotency: every due occurrence becomes one ``JobRun``
  with the unique key ``job_id:scheduled_time``; the Celery task may run a
  run twice, the handler sees the same key and a SUCCEEDED run is a no-op.
* Runs use the owner's *current* permissions; a revoked owner makes the run
  fail safely (job FAILED, owner notified) without calling the handler.
* AI-created jobs carry the delegated grant reference and are listed and
  cancellable by the owning user.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from kernel import clock, tenancy
from kernel.errors import DomainError, NotFound, PermissionDenied, ValidationFailed
from kernel.interfaces.scheduler import ScheduledJob as ScheduledJobDTO
from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from workflow.audit.service import record_change
from workflow.notifications.helpers import notify
from workflow.policy import policy
from workflow.scheduler.cron import Cron, zone
from workflow.scheduler.registry import JOB_TYPES

LEADER_LOCK = int.from_bytes(hashlib.sha256(b"workflow.scheduler.leader").digest()[:8], "big", signed=True)
MAX_BATCH = 500


def to_dto(job: Any) -> ScheduledJobDTO:
    return ScheduledJobDTO(
        job.id, job.code, job.cron, job.timezone, job.job_type, dict(job.params), job.state == "ACTIVE"
    )


def owner_context(job: Any) -> TenantContext:
    if job.created_by_agent:
        return TenantContext(
            tenant_id=job.tenant_id, company_ids=frozenset({job.company_id}), principal_id=job.owner_id,
            principal_type=PrincipalType.AI_AGENT, on_behalf_of=job.owner_id, channel=Channel.JOB,
        )  # fmt: skip
    return TenantContext(
        tenant_id=job.tenant_id, company_ids=frozenset({job.company_id}), principal_id=job.owner_id,
        principal_type=PrincipalType.USER, channel=Channel.JOB,
    )  # fmt: skip


def idempotency_key(job_id: uuid.UUID, scheduled_for: dt.datetime) -> str:
    return f"{job_id}:{scheduled_for.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"


class DbSchedulerService:
    # ------------------------------------------------------------------ definition
    def create(self, ctx: TenantContext, company_id: uuid.UUID, *, idempotency_key: str | None = None,
               **kwargs: Any) -> Any:  # fmt: skip
        return run_in_unit_of_work(lambda uow: self.create_in(uow, company_id, **kwargs), ctx,
                                   idempotency_key=idempotency_key)  # fmt: skip

    def create_in(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        *,
        code: str,
        job_type: str,
        cron: str = "",
        run_at: dt.datetime | None = None,
        timezone: str = "America/Bogota",
        params: dict[str, Any] | None = None,
        grant_ref: str = "",
    ) -> Any:
        from workflow.models import ScheduledJob

        ctx = uow.ctx
        ctx.require_company(company_id)
        policy().require(ctx, "workflow.scheduled_job.create", {"company_id": company_id, "job_type": job_type})
        if job_type not in JOB_TYPES:
            raise ValidationFailed(f"unknown job type {job_type!r}")
        if bool(cron) == bool(run_at):
            raise ValidationFailed("give either a cron schedule or a one-off run_at")
        if run_at is not None and run_at.tzinfo is None:
            raise ValidationFailed("run_at must include a timezone")
        zone(timezone)
        is_agent = ctx.principal_type in (PrincipalType.AI_AGENT, PrincipalType.MCP_CLIENT)
        if is_agent and (not grant_ref or ctx.on_behalf_of is None):
            raise PermissionDenied("AI-created jobs need a delegated grant and a user", code="delegation_required")
        owner = ctx.on_behalf_of if is_agent and ctx.on_behalf_of else ctx.principal_id
        next_run = Cron.parse(cron).next_after(clock.now(), timezone) if cron else run_at
        job = ScheduledJob.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, code=code[:60], job_type=job_type, owner_id=owner,
            owner_type="user", cron=cron, run_at=run_at, timezone=timezone, params=dict(params or {}),
            grant_ref=grant_ref, created_by_agent=is_agent, next_run_at=next_run,
            created_by=ctx.principal_id, updated_by=ctx.principal_id,
        )  # fmt: skip
        record_change("workflow.scheduled_job.create", "workflow.scheduled_job", job.id, company_id=company_id,
                      after={"code": job.code, "job_type": job_type, "cron": cron, "grant_ref": grant_ref,
                             "created_by_agent": is_agent}, uow=uow)  # fmt: skip
        return job

    def schedule(
        self, ctx: TenantContext, company_id: uuid.UUID, code: str, cron: str, task_name: str,
        args: dict[str, Any] | None = None,
    ) -> ScheduledJobDTO:  # fmt: skip
        return to_dto(self.create(ctx, company_id, code=code, job_type=task_name, cron=cron, params=args))

    def next_run(self, job: ScheduledJobDTO, after: dt.datetime) -> dt.datetime:
        return Cron.parse(job.cron).next_after(after, job.timezone)

    def _owned(self, ctx: TenantContext, job_id: uuid.UUID) -> Any:
        from workflow.models import ScheduledJob

        job = ScheduledJob.objects.filter(
            tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=job_id
        ).first()
        if job is None:
            raise NotFound("scheduled job not found")
        acting_user = ctx.on_behalf_of or ctx.principal_id
        if job.owner_id != acting_user and not policy().authorize(ctx, "workflow.scheduled_job.admin", job).allowed:
            raise NotFound("scheduled job not found")
        return job

    def _set_state(self, ctx: TenantContext, job_id: uuid.UUID, state: str, action: str) -> Any:
        job = self._owned(ctx, job_id)
        policy().require(ctx, "workflow.scheduled_job.update", job)

        def work(uow: UnitOfWork) -> Any:
            before = job.state
            job.state = state
            if state == "ACTIVE" and job.cron:
                job.next_run_at = Cron.parse(job.cron).next_after(clock.now(), job.timezone)
            job.save_versioned(ctx.principal_id, ["state", "next_run_at"])
            record_change(
                f"workflow.scheduled_job.{action}",
                "workflow.scheduled_job",
                job.id,
                company_id=job.company_id,
                before={"state": before},
                after={"state": state},
                uow=uow,
            )
            return job

        return run_in_unit_of_work(work, ctx)

    def pause(self, ctx: TenantContext, job_id: uuid.UUID) -> Any:
        return self._set_state(ctx, job_id, "PAUSED", "pause")

    def resume(self, ctx: TenantContext, job_id: uuid.UUID) -> Any:
        return self._set_state(ctx, job_id, "ACTIVE", "resume")

    def cancel(self, ctx: TenantContext, job_id: uuid.UUID) -> None:
        self._set_state(ctx, job_id, "CANCELLED", "cancel")

    def list_jobs(self, ctx: TenantContext, *, created_by_agent: bool | None = None) -> list[Any]:
        from workflow.models import ScheduledJob

        acting_user = ctx.on_behalf_of or ctx.principal_id
        qs = ScheduledJob.objects.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))
        if not policy().authorize(ctx, "workflow.scheduled_job.admin").allowed:
            qs = qs.filter(owner_id=acting_user)
        if created_by_agent is not None:
            qs = qs.filter(created_by_agent=created_by_agent)
        return list(qs.order_by("code"))

    def run_now(self, ctx: TenantContext, job_id: uuid.UUID) -> Any:
        from workflow.models import JobRun

        job = self._owned(ctx, job_id)
        policy().require(ctx, "workflow.scheduled_job.update", job)
        at = clock.now().replace(microsecond=0)

        def work(uow: UnitOfWork) -> Any:
            run, _ = JobRun.objects.get_or_create(
                idempotency_key=idempotency_key(job.id, at),
                defaults={"tenant_id": job.tenant_id, "job": job, "scheduled_for": at},
            )
            uow.on_commit(lambda: enqueue(run.id))
            return run

        return run_in_unit_of_work(work, ctx)

    # ------------------------------------------------------------------ leader loop
    def tick(self, now: dt.datetime | None = None, using: str = "default") -> int | None:
        """One scheduler pass. Returns runs created, or ``None`` when this replica is not the leader."""
        from django.db import transaction

        from workflow.models import JobRun, ScheduledJob

        now = now or clock.now()
        with transaction.atomic(using=using):
            if not tenancy.try_advisory_xact_lock(using, LEADER_LOCK):
                return None
            due = list(
                ScheduledJob.objects.using(using)
                .select_for_update(skip_locked=True)
                .filter(state="ACTIVE", next_run_at__lte=now)
                .order_by("next_run_at")[:MAX_BATCH]
            )
            created = 0
            for job in due:
                scheduled_for = job.next_run_at
                run, was_created = JobRun.objects.using(using).get_or_create(
                    idempotency_key=idempotency_key(job.id, scheduled_for),
                    defaults={"tenant_id": job.tenant_id, "job": job, "scheduled_for": scheduled_for},
                )
                created += int(was_created)
                if job.cron:
                    job.next_run_at = Cron.parse(job.cron).next_after(
                        max(scheduled_for, now - dt.timedelta(seconds=1)), job.timezone
                    )
                else:
                    job.next_run_at = None
                    job.state = "DONE"
                job.save(update_fields=["next_run_at", "state"])
                if was_created:
                    run_id = run.id
                    transaction.on_commit(lambda rid=run_id: enqueue(rid), using=using)
            return created

    # ------------------------------------------------------------------ execution
    def execute(self, run_id: uuid.UUID) -> Any:
        """Celery task body. Idempotent: a SUCCEEDED/FAILED run is returned unchanged."""
        from workflow.models import JobRun

        run = JobRun.objects.select_related("job").filter(id=run_id).first()
        if run is None:
            raise NotFound("job run not found")
        if run.state in ("SUCCEEDED", "FAILED"):
            return run
        job = run.job
        ctx = owner_context(job)
        jt = JOB_TYPES.get(job.job_type)

        def fail(uow: UnitOfWork, reason: str, *, disable: bool) -> Any:
            r = JobRun.objects.select_for_update().get(id=run.id)
            r.state, r.error, r.finished_at, r.attempts = "FAILED", reason[:2000], clock.now(), r.attempts + 1
            r.save(update_fields=["state", "error", "finished_at", "attempts"])
            j = type(job).objects.select_for_update().get(id=job.id)
            j.last_run_at, j.last_result = clock.now(), {"state": "FAILED", "error": reason[:500]}
            if disable:
                j.state = "FAILED"
            j.save(update_fields=["last_run_at", "last_result", "state"])
            notify(uow, job.owner_id, job.company_id, "tarea_fallida", {"tarea": job.code, "motivo": reason[:200]})
            record_change(
                "workflow.scheduled_job.run_failed",
                "workflow.scheduled_job",
                job.id,
                company_id=job.company_id,
                after={"run": run.idempotency_key, "error": reason[:200]},
                uow=uow,
            )
            return r

        allowed = (
            jt is not None and policy().authorize(ctx, jt.permission, {"grant_ref": job.grant_ref, "job": job}).allowed
        )
        if jt is None or jt.handler is None:
            return run_in_unit_of_work(
                lambda uow: fail(uow, f"no handler registered for {job.job_type}", disable=False), ctx
            )
        if not allowed:
            return run_in_unit_of_work(
                lambda uow: fail(uow, f"the owner no longer holds {jt.permission}", disable=True), ctx
            )

        handler = jt.handler

        def work(uow: UnitOfWork) -> Any:
            r = JobRun.objects.select_for_update().get(id=run.id)
            if r.state == "SUCCEEDED":
                return r
            r.state, r.started_at, r.attempts = "RUNNING", clock.now(), r.attempts + 1
            result = handler(ctx, job, dict(job.params), r.idempotency_key)
            r.state, r.finished_at, r.result = "SUCCEEDED", clock.now(), dict(result or {})
            r.save(update_fields=["state", "started_at", "attempts", "finished_at", "result"])
            type(job).objects.filter(id=job.id).update(last_run_at=r.finished_at, last_result={"state": "SUCCEEDED"})
            return r

        try:
            return run_in_unit_of_work(work, ctx)
        except DomainError as exc:
            message = f"{exc.code}: {exc.detail}"
        return run_in_unit_of_work(lambda uow: fail(uow, message, disable=False), ctx)


def enqueue(run_id: uuid.UUID) -> None:
    from workflow.tasks import run_scheduled_job

    run_scheduled_job.delay(str(run_id))
