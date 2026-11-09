import contextlib
import threading

import pytest
from django.db import DatabaseError, connection

from kernel import outbox, uow
from kernel.errors import RetryExhausted, UnitOfWorkRequired, ValidationFailed
from kernel.models import OutboxMessage, Sequence
from kernel.testing.tenancy import bound, make_context

from .conftest import run_threads


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_exception_after_outbox_write_leaves_no_rows(ctx, company):
    class Boom(Exception):
        pass

    with pytest.raises(Boom), uow.unit_of_work(ctx) as u:
        Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, sequence_code="X")
        u.emit("kernel.test.created.v1", {"n": 1}, company_id=company)
        outbox.write(list(u.events))  # explicit outbox write before the failure
        assert OutboxMessage.objects.count() == 1
        raise Boom()
    with bound(ctx):  # read as the tenant: RLS would hide the rows from an unbound session anyway
        assert OutboxMessage.objects.count() == 0
        assert Sequence.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_commit_writes_events_with_envelope(ctx, company):
    with uow.unit_of_work(ctx) as u:
        event = u.emit("kernel.test.created.v1", {"amount": __import__("decimal").Decimal("1.50")}, company_id=company)
        assert uow.current_uow() is u
        with uow.unit_of_work(ctx) as inner:  # nested joins the outer
            assert inner is u
    assert uow.current_uow() is None
    with bound(ctx):
        msg = OutboxMessage.objects.get()
    assert msg.id == event.event_id
    env = msg.envelope
    assert env["payload"] == {"amount": "1.50"}
    assert env["tenant_id"] == str(ctx.tenant_id) and env["company_id"] == str(company)
    assert env["actor"]["type"] == "user" and env["schema_version"] == 1
    assert msg.queue == "default"


@pytest.mark.django_db
def test_T_P01_04_nested_rules_and_require_uow(ctx):
    with pytest.raises(UnitOfWorkRequired):
        uow.require_uow()
    with uow.unit_of_work(ctx):
        with pytest.raises(ValidationFailed), uow.unit_of_work(ctx, idempotency_key="nested-key-1"):
            pass
        with pytest.raises(ValidationFailed), uow.unit_of_work(make_context()):
            pass
        with pytest.raises(ValidationFailed):
            uow.run_in_unit_of_work(lambda u: None, ctx)


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_on_commit_runs_only_after_commit(ctx):
    calls = []
    with uow.unit_of_work(ctx) as u:
        u.on_commit(lambda: calls.append("done"))
        assert calls == []
    assert calls == ["done"]
    with pytest.raises(RuntimeError), uow.unit_of_work(ctx) as u:
        u.on_commit(lambda: calls.append("never"))
        raise RuntimeError()
    assert calls == ["done"]


_barrier = threading.Barrier(2)


@pytest.mark.django_db(transaction=True)
def test_T_P01_07_deadlock_is_retried(ctx, company, monkeypatch):
    with bound(ctx):
        for code in ("A", "B"):
            Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, sequence_code=code)
    retries = []
    real_backoff = uow._backoff

    def counting_backoff(attempt):
        retries.append(attempt)
        # the survivor releases its locks; break the barrier so the retry does not wait
        _barrier.abort()
        real_backoff(attempt)

    monkeypatch.setattr(uow, "_backoff", counting_backoff)
    _barrier.reset()

    def target(i):
        order = ("A", "B") if i == 0 else ("B", "A")

        def work(u):
            with connection.cursor() as cur:
                cur.execute("SELECT id FROM kernel_sequence WHERE sequence_code = %s FOR UPDATE", [order[0]])
                with contextlib.suppress(threading.BrokenBarrierError):
                    _barrier.wait(timeout=10)
                cur.execute("SELECT id FROM kernel_sequence WHERE sequence_code = %s FOR UPDATE", [order[1]])
            return order

        return uow.run_in_unit_of_work(work, ctx)

    results, errors = run_threads(2, target)
    assert errors == []
    assert sorted(results) == [("A", "B"), ("B", "A")]
    assert len(retries) >= 1  # PostgreSQL broke the deadlock; the victim retried and succeeded


class _FakeSqlError(DatabaseError):
    def __init__(self, sqlstate):
        super().__init__("simulated")
        self.sqlstate = sqlstate


@pytest.mark.django_db(transaction=True)
def test_T_P01_07_retry_exhaustion_and_non_retryable(ctx, monkeypatch):
    monkeypatch.setattr(uow, "_backoff", lambda attempt: None)
    attempts = []

    def always_serialization_failure(u):
        attempts.append(1)
        raise _FakeSqlError("40001")

    with pytest.raises(RetryExhausted):
        uow.run_in_unit_of_work(always_serialization_failure, ctx)
    assert len(attempts) == 4  # first try + 3 retries

    def other_error(u):
        raise _FakeSqlError("23505")

    with pytest.raises(DatabaseError):
        uow.run_in_unit_of_work(other_error, ctx)

    flaky = iter([_FakeSqlError("40P01"), None])

    def flaky_once(u):
        exc = next(flaky)
        if exc:
            raise exc
        return "ok"

    assert uow.run_in_unit_of_work(flaky_once, ctx) == "ok"


def test_T_P01_07_sqlstate_detection_through_cause_chain():
    inner = _FakeSqlError("40P01")
    outer = DatabaseError("wrapped")
    outer.__cause__ = inner
    assert uow.is_retryable(outer)
    assert not uow.is_retryable(ValueError())
    assert uow._sqlstate(DatabaseError("x")) is None


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_transactional_decorator(ctx, company):
    @uow.transactional(scope="svc.create")
    def create(u, code):
        """doc"""
        Sequence.objects.create(tenant_id=u.ctx.tenant_id, company_id=company, sequence_code=code)
        u.set_response({"code": code}, 201)
        return code

    assert create(ctx, "D1") == "D1"
    assert create.__name__ == "create" and create.__doc__ == "doc"
    replay = create(ctx, "D2", idempotency_key="decorator-key-1", request={"code": "D2"})
    assert replay == "D2"
    again = create(ctx, "D2", idempotency_key="decorator-key-1", request={"code": "D2"})
    assert again.body == {"code": "D2"} and again.status_code == 201
    with bound(ctx):
        assert Sequence.objects.filter(sequence_code="D2").count() == 1
