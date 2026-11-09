import threading
import time

import pytest

from kernel import idempotency, uow
from kernel.errors import IdempotencyKeyInvalid, IdempotencyKeyReused
from kernel.models import IdempotencyRecord, OutboxMessage
from kernel.testing.tenancy import bound

from .conftest import run_threads


def _command(executions, body):
    def fn(u):
        executions.append(1)
        u.emit("kernel.test.created.v1", body)
        result = {"created": body["n"]}
        u.set_response(result, 201)
        return result

    return fn


@pytest.mark.django_db(transaction=True)
def test_T_P01_05_same_key_same_body_replays(ctx):
    executions = []
    first = uow.run_in_unit_of_work(_command(executions, {"n": 1}), ctx, idempotency_key="key-0001", request={"n": 1})
    second = uow.run_in_unit_of_work(_command(executions, {"n": 1}), ctx, idempotency_key="key-0001", request={"n": 1})
    assert first == {"created": 1}
    assert second.body == {"created": 1} and second.status_code == 201
    assert len(executions) == 1
    with bound(ctx):
        assert OutboxMessage.objects.count() == 1
        assert IdempotencyRecord.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_T_P01_05_same_key_different_body_is_422(ctx):
    executions = []
    uow.run_in_unit_of_work(_command(executions, {"n": 1}), ctx, idempotency_key="key-0002", request={"n": 1})
    with pytest.raises(IdempotencyKeyReused) as info:
        uow.run_in_unit_of_work(_command(executions, {"n": 2}), ctx, idempotency_key="key-0002", request={"n": 2})
    assert info.value.status == 422
    assert len(executions) == 1


@pytest.mark.django_db(transaction=True)
def test_T_P01_05_keys_are_tenant_scoped_and_failures_release_the_key(ctx):
    from kernel.testing.tenancy import make_context

    other = make_context()
    executions = []
    uow.run_in_unit_of_work(_command(executions, {"n": 1}), ctx, idempotency_key="key-0003", request={"n": 1})
    uow.run_in_unit_of_work(_command(executions, {"n": 1}), other, idempotency_key="key-0003", request={"n": 1})
    assert len(executions) == 2

    def failing(u):
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        uow.run_in_unit_of_work(failing, ctx, idempotency_key="key-0004", request={})
    assert uow.run_in_unit_of_work(lambda u: "retried", ctx, idempotency_key="key-0004", request={}) == "retried"
    # no explicit response stored → 204 replay
    replay = uow.run_in_unit_of_work(lambda u: "x", ctx, idempotency_key="key-0004", request={})
    assert replay.status_code == 204 and replay.body is None


@pytest.mark.django_db(transaction=True)
def test_T_P01_05_concurrent_same_key_executes_once(ctx):
    executions = []
    lock = threading.Lock()

    def fn(u):
        with lock:
            executions.append(1)
        time.sleep(0.3)  # hold the claim while the others arrive
        u.set_response({"ok": True}, 200)
        return {"ok": True}

    def target(i):
        return uow.run_in_unit_of_work(fn, ctx, idempotency_key="key-concurrent", request={"same": True})

    results, errors = run_threads(8, target)
    assert errors == []
    assert len(executions) == 1
    bodies = [r if isinstance(r, dict) else r.body for r in results]
    assert bodies == [{"ok": True}] * 8


def test_T_P01_05_key_validation_and_hash():
    for bad in ["short", "has space here", "x" * 256, None]:
        with pytest.raises(IdempotencyKeyInvalid):
            idempotency.validate_key(bad)  # type: ignore[arg-type]
    assert idempotency.validate_key("abc-123_DEF:9.z") == "abc-123_DEF:9.z"
    assert idempotency.request_hash({"a": 1, "b": 2}) == idempotency.request_hash({"b": 2, "a": 1})
    assert idempotency.request_hash({"a": 1}, "s1") != idempotency.request_hash({"a": 1}, "s2")
