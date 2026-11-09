"""FIX-CI-W02: under forced RLS the cross-tenant workers (webhook deliverer, audit verifier) bind their
platform job; nothing leaks from a previous binding (separate connection, no outer test transaction)."""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import pytest
from django.db import connections

from kernel import clock, ids
from kernel.interfaces.audit import AuditEntry
from kernel.testing.tenancy import bound, make_context
from kernel.uow import run_in_unit_of_work
from workflow.audit.service import DbAuditService
from workflow.models import WebhookDelivery
from workflow.webhooks.service import WebhookService

pytestmark = pytest.mark.django_db(transaction=True)


class OkTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, url: str, headers: Any, body: bytes) -> int:
        self.sent.append(url)
        return 200


def _in_fresh_thread(fn: Any) -> Any:
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # pragma: no cover - re-raised below
            out["error"] = exc
        finally:
            connections.close_all()

    t = threading.Thread(target=run)
    t.start()
    t.join(60)
    if "error" in out:
        raise out["error"]
    return out["value"]


def test_FIX_CI_W02_deliverer_sees_every_tenant_only_through_its_platform_job(grants) -> None:  # type: ignore[no-untyped-def]
    transport = OkTransport()
    svc = WebhookService(transport)
    tenants = []
    for _ in range(2):
        company = ids.new_id()
        admin = make_context(company_ids=[company])
        grants.grant(admin.principal_id, "workflow.webhook.create")
        sa = ids.new_id()
        grants.grant(sa, "ledger.event.read")
        sub, _ = svc.subscribe(admin, company, "http://webhook-sink:9000/hook", ["ledger.*"], sa)
        env = {
            "event_id": str(ids.new_id()), "event_type": "ledger.journal_entry.posted.v1",
            "tenant_id": admin.tenant_id, "company_id": company, "occurred_at": clock.now().isoformat(),
            "payload": {"id": "x"},
        }  # fmt: skip
        assert run_in_unit_of_work(lambda uow, env=env: svc.fan_out(uow, env), admin) == 1
        tenants.append((admin, sub))
    assert WebhookDelivery.objects.count() == 0  # unbound session: RLS hides every tenant's rows
    later = clock.now() + dt.timedelta(seconds=1)
    assert _in_fresh_thread(lambda: svc.deliver_due(now=later)) == 2
    assert len(transport.sent) == 2
    for admin, sub in tenants:
        with bound(admin):
            assert WebhookDelivery.objects.get(subscription=sub).state == WebhookDelivery.DELIVERED


def test_FIX_CI_W02_audit_verifier_lists_and_verifies_each_tenant() -> None:
    svc = DbAuditService()
    ctxs = [make_context(), make_context()]
    for ctx in ctxs:
        for i in range(3):
            run_in_unit_of_work(
                lambda uow, i=i: svc.record(uow, AuditEntry(f"test.thing.update{i}", "test.thing", None, None, {})),
                ctx,
            )
    listed = _in_fresh_thread(svc.tenants)
    assert {c.tenant_id for c in ctxs} <= set(listed)
    for ctx in ctxs:
        report = _in_fresh_thread(lambda ctx=ctx: svc.verify(ctx.tenant_id))
        assert report.ok and report.checked == 3
