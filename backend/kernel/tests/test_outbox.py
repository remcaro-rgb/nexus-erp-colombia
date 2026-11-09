import datetime as dt

import pytest

from kernel import clock, outbox, tasks, uow
from kernel.models import DeadLetter, InboxRecord, OutboxMessage
from kernel.testing.tenancy import bound


class RecordingPublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def publish(self, queue, envelope):
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((queue, envelope["event_id"]))


def _emit(ctx, company, event_type, n=1):
    with uow.unit_of_work(ctx) as u:
        for i in range(n):
            u.emit(event_type, {"i": i}, company_id=company)


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_relay_publishes_in_order_with_routing(ctx, company, settings):
    _emit(ctx, company, "einvoice.document.issued.v1")
    _emit(ctx, company, "sales.invoice.posted.v2", n=2)
    _emit(ctx, company, "nosuchmodule.x.y.v1")
    settings.ERP_OUTBOX_ROUTES = {**settings.ERP_OUTBOX_ROUTES, "nosuchmodule": "not-a-queue"}
    pub = RecordingPublisher()
    result = outbox.relay_batch(pub, batch_size=10)
    assert result.published == 4 and result.failed == 0
    assert [q for q, _ in pub.sent] == ["einvoice", "default", "default", "default"]
    with bound(ctx):
        assert OutboxMessage.objects.filter(status="PUBLISHED").count() == 4
    assert outbox.relay_batch(pub).published == 0  # nothing pending
    m = outbox.metrics()
    assert m["pending"] == 0 and m["published"] >= 4


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_relay_backoff_then_dead_letter(ctx, company, settings):
    settings.ERP_OUTBOX_MAX_ATTEMPTS = 2
    _emit(ctx, company, "kernel.test.created.v1")
    failing = RecordingPublisher(fail=True)
    assert outbox.relay_batch(failing).failed == 1
    with bound(ctx):
        msg = OutboxMessage.objects.get()
    assert msg.attempts == 1 and msg.available_at > clock.now() and "broker down" in msg.last_error
    assert outbox.relay_batch(failing).failed == 0  # not yet available
    with clock.frozen(clock.now() + dt.timedelta(hours=2)):
        assert outbox.relay_batch(failing).dead == 1
    with bound(ctx):
        msg.refresh_from_db()
        dl = DeadLetter.objects.get()
    assert msg.status == "DEAD"
    assert dl.stage == "RELAY" and dl.event_id == msg.id
    pub = RecordingPublisher()
    outbox.replay_dead_letter(dl.id, pub)
    with bound(ctx):
        dl.refresh_from_db()
    assert dl.replayed_at is not None and pub.sent[0][1] == str(msg.id)


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_relay_skip_locked_batches(ctx, company):
    """Two relays never publish the same row (FOR UPDATE SKIP LOCKED)."""
    from .conftest import run_threads

    _emit(ctx, company, "kernel.test.created.v1", n=40)
    pubs = [RecordingPublisher(), RecordingPublisher()]
    run_threads(2, lambda i: outbox.relay_batch(pubs[i], batch_size=40))
    ids = [e for p in pubs for _, e in p.sent]
    remaining = outbox.relay_batch(RecordingPublisher(), batch_size=100).published
    assert len(ids) == len(set(ids))
    assert len(ids) + remaining == 40


@pytest.mark.django_db(transaction=True)
def test_T_P01_04_consumer_dedupe_and_dead_letters(ctx, company):
    seen = []
    outbox.register_consumer("test.ok", "kernel.test.*", lambda env: seen.append(env["event_id"]))

    def broken(env):
        raise ValueError("handler bug")

    outbox.register_consumer("test.broken", "kernel.test.created.v1", broken)
    try:
        _emit(ctx, company, "kernel.test.created.v1")
        with bound(ctx):
            env = OutboxMessage.objects.get().envelope
        assert tasks.dispatch_event(env) == {"test.ok": "processed", "test.broken": "dead"}
        assert outbox.dispatch(env)["test.ok"] == "duplicate"
        assert len(seen) == 1
        assert InboxRecord.objects.filter(consumer="test.ok").count() == 1
        assert InboxRecord.objects.filter(consumer="test.broken").count() == 0  # rolled back with the handler
        with bound(ctx):
            assert DeadLetter.objects.filter(stage="CONSUMER", consumer="test.broken").count() == 2
        with pytest.raises(ValueError):
            outbox.register_consumer("test.ok", "x.*", lambda env: None)
        outbox.register_consumer("test.ok", "kernel.test.*", outbox._consumers["test.ok"].handler)
        assert [c.name for c in outbox.consumers_for("other.thing.done.v1")] == []
    finally:
        outbox.unregister_consumer("test.ok")
        outbox.unregister_consumer("test.broken")
        outbox.reset_metrics()


def test_T_P01_04_celery_publisher_routes_to_dispatch_task(monkeypatch):
    from erp.celery import app

    calls = []
    monkeypatch.setattr(app, "send_task", lambda name, **kw: calls.append((name, kw)))
    outbox.CeleryPublisher().publish("einvoice", {"event_id": "e1"})
    assert calls == [("kernel.dispatch_event", {"args": [{"event_id": "e1"}], "queue": "einvoice", "task_id": "e1"})]


def test_T_P01_04_tenant_task_activates_context(ctx):
    from erp.celery import app
    from kernel import tenancy

    @app.task(base=tasks.TenantTask, name="kernel.test.probe")
    def probe():
        return tenancy.current()

    assert probe(**tasks.TenantTask.context_kwargs(ctx)) == ctx
    assert probe() is None
