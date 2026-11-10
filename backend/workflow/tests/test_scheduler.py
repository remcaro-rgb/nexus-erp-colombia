"""T-P07-06 single leader under 3 replicas · T-P07-07 at-least-once + idempotent execution ·
T-P07-08 revoked owner fails safely · T-P07-12 AI-created jobs carry the grant, listed/cancelled by the user."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from django.db import connection, connections

from kernel import clock, ids
from kernel.errors import PermissionDenied
from kernel.tenancy import PrincipalType
from kernel.testing.tenancy import bound
from workflow.models import JobRun, Notification, ScheduledJob
from workflow.scheduler.cron import Cron
from workflow.scheduler.registry import JOB_TYPES, register_job_type
from workflow.scheduler.service import LEADER_LOCK, DbSchedulerService, idempotency_key

CREATE = "workflow.scheduled_job.create"
CALLS: list[str] = []


def _handler(ctx, job, params, key):  # type: ignore[no-untyped-def]
    CALLS.append(key)
    return {"ok": True, "key": key}


@pytest.fixture(autouse=True)
def job_type():  # type: ignore[no-untyped-def]
    CALLS.clear()
    register_job_type("test_report", "reporting.report.read", "Informe de prueba", _handler, replace=True)
    yield
    JOB_TYPES.pop("test_report", None)


def _job(ctx, company, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("cron", "0 7 * * *")
    return DbSchedulerService().create(
        ctx, company, code=kw.pop("code", "J-" + ids.new_id().hex[-10:]), job_type="test_report", **kw
    )


# --------------------------------------------------------------------------- cron


@pytest.mark.parametrize(
    ("expr", "after", "tz", "expected"),
    [
        ("0 7 * * *", "2026-11-09T11:00:00+00:00", "America/Bogota", "2026-11-09T12:00:00+00:00"),
        ("0 7 * * *", "2026-11-09T12:00:00+00:00", "America/Bogota", "2026-11-10T12:00:00+00:00"),
        ("*/15 * * * *", "2026-11-09T12:07:00+00:00", "UTC", "2026-11-09T12:15:00+00:00"),
        ("0 9 * * 1-5", "2026-11-13T15:00:00+00:00", "America/Bogota", "2026-11-16T14:00:00+00:00"),
        ("30 2 * * *", "2026-03-08T00:00:00+00:00", "America/New_York", "2026-03-08T07:30:00+00:00"),  # DST gap
        ("30 1 * * *", "2026-11-01T00:00:00+00:00", "America/New_York", "2026-11-01T05:30:00+00:00"),  # ambiguous: once
        ("0 0 1 * *", "2026-12-15T00:00:00+00:00", "UTC", "2027-01-01T00:00:00+00:00"),
    ],
)
def test_T_P07_07_cron_in_company_timezone_dst_safe(expr, after, tz, expected) -> None:  # type: ignore[no-untyped-def]
    got = Cron.parse(expr).next_after(dt.datetime.fromisoformat(after), tz)
    assert got == dt.datetime.fromisoformat(expected)


# --------------------------------------------------------------------------- leader


@pytest.mark.django_db
def test_T_P07_06_replica_without_the_lock_does_nothing(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(CREATE)
    job = _job(ctx, company)
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=clock.now() - dt.timedelta(minutes=1))
    other = connections.create_connection("default")
    try:
        with other.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SELECT pg_try_advisory_xact_lock(%s)", [LEADER_LOCK])
            assert cur.fetchone()[0] is True  # another replica is the leader
            assert DbSchedulerService().tick() is None
            cur.execute("ROLLBACK")
    finally:
        other.close()
    assert DbSchedulerService().tick() == 1


@pytest.mark.django_db(transaction=True)
def test_T_P07_06_single_leader_under_three_replicas(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(CREATE)
    jobs = [_job(ctx, company) for _ in range(5)]
    past = clock.now() - dt.timedelta(minutes=1)
    with bound(ctx):  # forced RLS: direct test writes run bound to the tenant
        ScheduledJob.objects.filter(id__in=[j.id for j in jobs]).update(next_run_at=past)
    barrier = threading.Barrier(3)
    results: list[int | None] = []
    lock = threading.Lock()

    def replica() -> None:
        try:
            barrier.wait()
            for _ in range(5):
                r = DbSchedulerService().tick()
                with lock:
                    results.append(r)
        finally:
            connection.close()

    threads = [threading.Thread(target=replica) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r or 0 for r in results) == 5
    with bound(ctx):
        assert JobRun.objects.filter(job__in=jobs).count() == 5
        keys = list(JobRun.objects.filter(job__in=jobs).values_list("idempotency_key", flat=True))
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- execution


@pytest.mark.django_db
def test_T_P07_07_at_least_once_and_idempotent(user_factory, company, grants) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(CREATE, "reporting.report.read")
    job = _job(ctx, company)
    when = clock.now() - dt.timedelta(minutes=1)
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=when)
    svc = DbSchedulerService()
    assert svc.tick() == 1
    run = JobRun.objects.get(job=job)
    assert run.idempotency_key == idempotency_key(job.id, when)
    # The broker delivers the same task twice (at-least-once): the handler runs once.
    assert svc.execute(run.id).state == "SUCCEEDED"
    assert svc.execute(run.id).state == "SUCCEEDED"
    assert [run.idempotency_key] == CALLS
    # A replayed tick for the same occurrence does not create a second run.
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=when)
    assert svc.tick() == 0
    assert JobRun.objects.filter(job=job).count() == 1
    job.refresh_from_db()
    assert job.next_run_at > clock.now()


@pytest.mark.django_db
def test_T_P07_07_handler_error_is_recorded(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.errors import ValidationFailed

    def broken(ctx, job, params, key):  # type: ignore[no-untyped-def]
        raise ValidationFailed("datos incompletos")

    register_job_type("test_report", "reporting.report.read", "x", broken, replace=True)
    ctx = user_factory(CREATE, "reporting.report.read")
    job = _job(ctx, company)
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=clock.now() - dt.timedelta(minutes=1))
    DbSchedulerService().tick()
    run = JobRun.objects.get(job=job)
    assert DbSchedulerService().execute(run.id).state == "FAILED"
    assert "validation_failed" in JobRun.objects.get(id=run.id).error


@pytest.mark.django_db
def test_T_P07_08_revoked_owner_job_fails_safely(user_factory, company, grants) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(CREATE, "reporting.report.read", "workflow.scheduled_job.read")
    job = _job(ctx, company)
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=clock.now() - dt.timedelta(minutes=1))
    DbSchedulerService().tick()
    run = JobRun.objects.get(job=job)
    grants.revoke(ctx.principal_id, "reporting.report.read")  # permissions are re-evaluated at run time
    result = DbSchedulerService().execute(run.id)
    assert result.state == "FAILED" and "no longer holds" in result.error
    assert CALLS == []  # the handler never ran
    job.refresh_from_db()
    assert job.state == "FAILED"
    assert Notification.objects.filter(recipient_id=ctx.principal_id, template_code="tarea_fallida").exists()


@pytest.mark.django_db
def test_T_P07_08_unknown_job_type_fails_closed(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.errors import ValidationFailed

    ctx = user_factory(CREATE)
    with pytest.raises(ValidationFailed):
        DbSchedulerService().create(ctx, company, code="X", job_type="rm_rf", cron="* * * * *")
    with pytest.raises(PermissionDenied):
        DbSchedulerService().create(user_factory(), company, code="Y", job_type="test_report", cron="* * * * *")


# --------------------------------------------------------------------------- AI-created jobs


@pytest.mark.django_db
def test_T_P07_12_ai_created_jobs_carry_grant_and_user_controls_them(user_factory, company, grants) -> None:  # type: ignore[no-untyped-def]
    user = user_factory("workflow.scheduled_job.read", "workflow.scheduled_job.update")
    agent = user_factory(CREATE, principal_type=PrincipalType.AI_AGENT, on_behalf_of=user.principal_id)
    with pytest.raises(PermissionDenied) as exc:
        DbSchedulerService().create(agent, company, code="AI-0", job_type="test_report", cron="0 8 * * 1")
    assert exc.value.code == "delegation_required"
    job = DbSchedulerService().create(
        agent, company, code="AI-1", job_type="test_report", cron="0 8 * * 1", grant_ref="grant-123"
    )
    assert job.created_by_agent and job.grant_ref == "grant-123" and job.owner_id == user.principal_id
    listed = DbSchedulerService().list_jobs(user, created_by_agent=True)
    assert [j.id for j in listed] == [job.id]
    other = user_factory("workflow.scheduled_job.read", "workflow.scheduled_job.update")
    assert DbSchedulerService().list_jobs(other, created_by_agent=True) == []
    from kernel.errors import NotFound

    with pytest.raises(NotFound):
        DbSchedulerService().cancel(other, job.id)
    DbSchedulerService().cancel(user, job.id)
    job.refresh_from_db()
    assert job.state == "CANCELLED"


@pytest.mark.django_db
def test_T_P07_12_ai_job_runs_with_agent_context_and_grant(user_factory, company, grants) -> None:  # type: ignore[no-untyped-def]
    seen = []

    def handler(ctx, job, params, key):  # type: ignore[no-untyped-def]
        seen.append((ctx.principal_type, ctx.on_behalf_of, job.grant_ref))
        return {}

    register_job_type("test_report", "reporting.report.read", "x", handler, replace=True)
    user = user_factory("reporting.report.read")
    agent = user_factory(CREATE, principal_type=PrincipalType.AI_AGENT, on_behalf_of=user.principal_id)
    job = DbSchedulerService().create(
        agent, company, code="AI-2", job_type="test_report", cron="0 8 * * *", grant_ref="g-9"
    )
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=clock.now() - dt.timedelta(minutes=1))
    DbSchedulerService().tick()
    assert DbSchedulerService().execute(JobRun.objects.get(job=job).id).state == "SUCCEEDED"
    assert seen == [(PrincipalType.AI_AGENT, user.principal_id, "g-9")]


@pytest.mark.django_db
def test_T_P07_07_pause_resume_and_one_off(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(CREATE, "workflow.scheduled_job.update")
    job = _job(ctx, company)
    DbSchedulerService().pause(ctx, job.id)
    ScheduledJob.objects.filter(id=job.id).update(next_run_at=clock.now() - dt.timedelta(minutes=1))
    assert DbSchedulerService().tick() == 0
    DbSchedulerService().resume(ctx, job.id)
    once = DbSchedulerService().create(
        ctx, company, code="ONCE", job_type="test_report", cron="", run_at=clock.now() - dt.timedelta(seconds=5)
    )
    assert DbSchedulerService().tick() == 1
    once.refresh_from_db()
    assert once.state == "DONE" and once.next_run_at is None
