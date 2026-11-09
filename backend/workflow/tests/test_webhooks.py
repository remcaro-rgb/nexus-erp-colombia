"""T-P07-09 webhook signature + retry + field masking."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from kernel import clock, ids
from kernel.errors import ValidationFailed
from kernel.uow import run_in_unit_of_work
from workflow.models import WebhookDelivery
from workflow.webhooks.service import (
    MAX_ATTEMPTS,
    SIGNATURE_HEADER,
    WebhookService,
    backoff,
    sign,
    validate_url,
    verify_signature,
)

pytestmark = pytest.mark.django_db


class FakeTransport:
    def __init__(self, statuses: list[int | Exception]) -> None:
        self.statuses = list(statuses)
        self.sent: list[tuple[str, dict[str, str], bytes]] = []

    def send(self, url, headers, body):  # type: ignore[no-untyped-def]
        self.sent.append((url, dict(headers), body))
        s = self.statuses.pop(0) if self.statuses else 200
        if isinstance(s, Exception):
            raise s
        return s


@pytest.fixture
def setup(user_factory, company, grants):  # type: ignore[no-untyped-def]
    admin = user_factory("workflow.webhook.create", "workflow.webhook.update")
    sa = ids.new_id()
    grants.grant(sa, "ledger.event.read")
    grants.readable[(sa, "ledger.journal_entry.posted")] = frozenset({"number", "total"})
    return admin, sa


def _envelope(ctx, company, **payload):  # type: ignore[no-untyped-def]
    return {
        "event_id": str(ids.new_id()), "event_type": "ledger.journal_entry.posted.v1", "tenant_id": ctx.tenant_id,
        "company_id": company, "occurred_at": clock.now().isoformat(), "payload": payload,
    }  # fmt: skip


def test_T_P07_09_signature_roundtrip_and_tamper() -> None:
    body = b'{"a":1}'
    header = sign("s3cr3t", body, 1_700_000_000)
    assert verify_signature("s3cr3t", body, header, now=1_700_000_100)
    assert not verify_signature("s3cr3t", b'{"a":2}', header, now=1_700_000_100)
    assert not verify_signature("other", body, header, now=1_700_000_100)
    assert not verify_signature("s3cr3t", body, header, now=1_700_000_000 + 301)  # replay window
    assert not verify_signature("s3cr3t", body, "garbage", now=1_700_000_000)


def test_T_P07_09_delivery_signed_masked_and_retried(setup, company) -> None:  # type: ignore[no-untyped-def]
    admin, sa = setup
    transport = FakeTransport([500, OSError("down"), 202])
    svc = WebhookService(transport)
    sub, secret = svc.subscribe(admin, company, "http://webhook-sink:9000/hook", ["ledger.*"], sa)
    env = _envelope(admin, company, number="CE-1", total="100.0000", salary_of_ceo="9", id="x")
    n = run_in_unit_of_work(lambda uow: svc.fan_out(uow, env), admin)
    assert n == 1
    assert run_in_unit_of_work(lambda uow: svc.fan_out(uow, env), admin) == 0  # idempotent per event
    d = WebhookDelivery.objects.get(subscription=sub)
    assert d.payload["payload"] == {"number": "CE-1", "total": "100.0000", "id": "x"}  # field mask applied
    t0 = clock.now()
    assert svc.attempt(d.id, now=t0).state == "RETRYING"
    d.refresh_from_db()
    assert d.next_attempt_at == t0 + backoff(1)
    assert svc.attempt(d.id, now=t0 + dt.timedelta(minutes=1)).state == "RETRYING"
    d.refresh_from_db()
    assert d.next_attempt_at == t0 + dt.timedelta(minutes=1) + backoff(2) and d.last_error == "OSError"
    assert svc.attempt(d.id, now=t0 + dt.timedelta(minutes=5)).state == "DELIVERED"
    url, headers, body = transport.sent[-1]
    assert url == "http://webhook-sink:9000/hook"
    assert verify_signature(
        secret, body, headers[SIGNATURE_HEADER], now=int((t0 + dt.timedelta(minutes=5)).timestamp())
    )
    assert "salary_of_ceo" not in json.loads(body)["payload"]


def test_T_P07_09_dead_letter_and_replay(setup, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.models import OutboxMessage

    admin, sa = setup
    svc = WebhookService(FakeTransport([500] * MAX_ATTEMPTS))
    sub, _ = svc.subscribe(admin, company, "http://localhost:9/hook", ["ledger.journal_entry.posted.v1"], sa)
    run_in_unit_of_work(lambda uow: svc.fan_out(uow, _envelope(admin, company, number="1")), admin)
    d = WebhookDelivery.objects.get(subscription=sub)
    now = clock.now()
    for i in range(MAX_ATTEMPTS):
        d = svc.attempt(d.id, now=now + dt.timedelta(hours=i * 7))
    assert d.state == "DEAD" and d.attempts == MAX_ATTEMPTS
    assert OutboxMessage.objects.filter(event_type="workflow.webhook.delivery_failed.v1").exists()
    replayed = svc.replay(admin, d.id)
    assert replayed.state == "PENDING" and replayed.attempts == 0
    svc.transport = FakeTransport([200])
    assert svc.deliver_due(now=clock.now() + dt.timedelta(seconds=1)) == 1


def test_T_P07_09_service_account_without_event_permission_gets_nothing(setup, company, grants) -> None:  # type: ignore[no-untyped-def]
    admin, sa = setup
    svc = WebhookService(FakeTransport([]))
    svc.subscribe(admin, company, "http://localhost:9/hook", ["*"], sa)
    env = _envelope(admin, company, number="1")
    env["event_type"] = "payroll.payroll_run.posted.v1"
    assert run_in_unit_of_work(lambda uow: svc.fan_out(uow, env), admin) == 0


@pytest.mark.parametrize(
    "url", ["https://hooks.example.com/x", "http://10.0.0.5/x", "ftp://localhost/x", "http://user:pw@localhost/x", "x"]
)
def test_T_P07_09_production_or_unsafe_urls_refused(url: str) -> None:
    if url == "https://hooks.example.com/x":
        url = "https://hooks.slack.com/x"
    with pytest.raises(ValidationFailed):
        validate_url(url)
    assert validate_url("https://sink.test/hook")
