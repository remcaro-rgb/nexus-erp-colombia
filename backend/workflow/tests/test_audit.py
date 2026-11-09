"""T-P07-04 audit hash chain detects tampering; T-P07-05 audit writes roll back with the business transaction."""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import DatabaseError, connection

from kernel.interfaces.audit import AuditEntry
from kernel.uow import run_in_unit_of_work, unit_of_work
from workflow.audit.service import DbAuditService, diff, record_change
from workflow.models import AuditEvent

pytestmark = pytest.mark.django_db


def _record(ctx, n: int = 3) -> None:  # type: ignore[no-untyped-def]
    svc = DbAuditService()
    for i in range(n):
        run_in_unit_of_work(
            lambda uow, i=i: svc.record(uow, AuditEntry(f"test.thing.update{i}", "test.thing", None, None, {"n": i})), ctx
        )


def test_T_P07_04_chain_verifies_and_is_sequential(user_factory) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory()
    _record(ctx, 5)
    rows = list(AuditEvent.objects.filter(tenant_id=ctx.tenant_id).order_by("seq"))
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0].prev_hash == "0" * 64 and all(b.prev_hash == a.hash for a, b in zip(rows, rows[1:], strict=False))
    report = DbAuditService().verify(ctx.tenant_id)
    assert report.ok and report.checked == 5
    assert DbAuditService().verify_chain(ctx.tenant_id)


def test_T_P07_04_owner_role_tampering_is_detected(user_factory) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory()
    _record(ctx, 4)
    with connection.cursor() as cur:
        # The table owner can disable the trigger: the chain still catches the edit.
        cur.execute("ALTER TABLE workflow_audit_event DISABLE TRIGGER workflow_audit_event_immutable_trg")
        cur.execute(
            "UPDATE workflow_audit_event SET reason = 'edited' WHERE tenant_id = %s AND seq = 2", [str(ctx.tenant_id)]
        )
        cur.execute("ALTER TABLE workflow_audit_event ENABLE TRIGGER workflow_audit_event_immutable_trg")
    report = DbAuditService().verify(ctx.tenant_id)
    assert not report.ok and report.first_bad_seq == 2 and report.reason == "hash mismatch"


def test_T_P07_04_deleting_a_row_is_detected(user_factory) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory()
    _record(ctx, 3)
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE workflow_audit_event DISABLE TRIGGER workflow_audit_event_immutable_trg")
        cur.execute("DELETE FROM workflow_audit_event WHERE tenant_id = %s AND seq = 2", [str(ctx.tenant_id)])
        cur.execute("ALTER TABLE workflow_audit_event ENABLE TRIGGER workflow_audit_event_immutable_trg")
    assert DbAuditService().verify(ctx.tenant_id).first_bad_seq == 3


def test_T_P07_04_trigger_blocks_update_and_delete(user_factory) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory()
    _record(ctx, 1)
    with pytest.raises(DatabaseError), connection.cursor() as cur:
        cur.execute("UPDATE workflow_audit_event SET reason = 'x' WHERE tenant_id = %s", [str(ctx.tenant_id)])


def test_T_P07_04_tenants_have_independent_chains(user_factory) -> None:  # type: ignore[no-untyped-def]
    from kernel.testing.tenancy import make_context

    a, b = user_factory(), make_context()
    _record(a, 2)
    _record(b, 2)
    assert DbAuditService().verify(a.tenant_id).checked == 2 and DbAuditService().verify(b.tenant_id).checked == 2


def test_T_P07_05_audit_rolls_back_with_business_transaction(user_factory) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory()

    class Boom(Exception):
        pass

    with pytest.raises(Boom), unit_of_work(ctx):
        record_change("test.doc.post", "test.doc", None, after={"total": "10"})
        raise Boom
    assert not AuditEvent.objects.filter(tenant_id=ctx.tenant_id).exists()
    with unit_of_work(ctx):
        record_change("test.doc.post", "test.doc", None, after={"total": "10"})
    assert AuditEvent.objects.filter(tenant_id=ctx.tenant_id).count() == 1
    assert DbAuditService().verify(ctx.tenant_id).ok


def test_T_P07_05_record_outside_uow_is_refused(user_factory) -> None:  # type: ignore[no-untyped-def]
    from kernel.errors import UnitOfWorkRequired

    with pytest.raises(UnitOfWorkRequired):
        record_change("x.y.z", "x.y", None)


def test_T_P07_04_sensitive_values_are_masked() -> None:
    changes = diff(
        {"email": "a@x.invalid", "password": "p1", "total": "1"},
        {"email": "b@x.invalid", "password": "p2", "total": "2"},
        sensitive={"total"},
    )
    assert changes["password"] == {"before": "***", "after": "***"}
    assert changes["email"]["after"].startswith("sha256:") and "b@x" not in str(changes)
    assert changes["total"]["before"].startswith("sha256:")
    assert diff({"a": 1}, {"a": 1}) == {}


def test_T_P07_04_anchor_written_with_retention_lock(user_factory, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from kernel.errors import Conflict
    from workflow.attachments.store import LocalFileStore

    ctx = user_factory()
    _record(ctx, 2)
    store = LocalFileStore(tmp_path, b"k" * 32)
    anchor = DbAuditService().anchor(ctx.tenant_id, dt.date.today(), store)
    assert anchor.last_seq == 2 and anchor.head_hash == DbAuditService().verify(ctx.tenant_id).head_hash
    assert b'"last_seq": 2' in store.get(ctx.tenant_id, anchor.object_key)
    with pytest.raises(Conflict):
        store.delete(ctx.tenant_id, anchor.object_key)


def test_T_P07_04_audit_verify_command(user_factory) -> None:  # type: ignore[no-untyped-def]
    from io import StringIO

    from django.core.management import call_command

    ctx = user_factory()
    _record(ctx, 2)
    out = StringIO()
    call_command("audit_verify", "--tenant", str(ctx.tenant_id), stdout=out)
    assert "OK" in out.getvalue()
