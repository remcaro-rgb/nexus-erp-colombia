"""P22a harness self-tests: failure injection + all-or-nothing probe on the real kernel UoW
(PostgreSQL). The same ``sweep`` drives T-P22-FIN-01 / T-P22-STK-01 once posting services exist."""

from __future__ import annotations

import pytest
from django.db import connection
from qa_harness.failure_injection import AtomicityProbe, InjectedFailure, fail_after, sweep

from kernel import sequences
from kernel.models import IdempotencyRecord, OutboxMessage, Sequence
from kernel.testing.tenancy import first_company, make_context
from kernel.uow import run_in_unit_of_work, unit_of_work

TABLES = [Sequence._meta.db_table, OutboxMessage._meta.db_table, IdempotencyRecord._meta.db_table]


@pytest.fixture
def setup(db):
    ctx = make_context()
    company = first_company(ctx)
    Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, sequence_code="QA", prefix="QA-")
    return ctx, company


def _reset(ctx):
    def reset():
        OutboxMessage.objects.all().delete()
        IdempotencyRecord.objects.all().delete()
        Sequence.objects.filter(tenant_id=ctx.tenant_id).update(next_number=1)

    return reset


def _atomic_command(ctx, company, counter=[0]):  # noqa: B006 - unique keys per call
    def command():
        counter[0] += 1

        def work(uow):
            n = sequences.allocate(company, "QA", prefix="QA-")
            uow.emit("kernel.qa.probe.v1", {"number": n.formatted})
            uow.set_response({"number": n.formatted}, 201)
            return n

        return run_in_unit_of_work(work, ctx, idempotency_key=f"qa-probe-{counter[0]:06d}", request={"k": counter[0]})

    return command


def test_p22a_injector_counts_and_fires_before_the_nth_statement(setup):
    ctx, company = setup
    with fail_after(None) as c:
        _atomic_command(ctx, company)()
    assert c.count >= 4 and not c.fired
    with fail_after(2) as c2, pytest.raises(InjectedFailure):
        _atomic_command(ctx, company)()
    assert c2.fired and c2.count == 2


def test_p22a_sweep_proves_kernel_uow_all_or_nothing(setup):
    ctx, company = setup
    probe = AtomicityProbe(TABLES)
    result = sweep(_atomic_command(ctx, company), probe, expect_changed=TABLES, reset=_reset(ctx))
    assert result.ok, result.violations
    assert result.points == list(range(1, result.statements + 1))
    assert set(TABLES) == result.clean_changes


def test_p22a_sweep_detects_a_non_atomic_command(setup):
    """Mutation test: two separate transactions (outbox written outside the numbering UoW)."""
    ctx, company = setup

    def broken():
        with unit_of_work(ctx):
            sequences.allocate(company, "QA", prefix="QA-")
        with unit_of_work(ctx) as uow:
            uow.emit("kernel.qa.probe.v1", {"broken": True})

    result = sweep(broken, AtomicityProbe(TABLES), expect_changed=TABLES[:2], reset=_reset(ctx))
    assert not result.ok
    assert any("partial writes" in v for v in result.violations)


def test_p22a_sweep_reports_missing_expected_effects(setup):
    ctx, company = setup

    def no_outbox():
        with unit_of_work(ctx):
            sequences.allocate(company, "QA", prefix="QA-")

    result = sweep(no_outbox, AtomicityProbe(TABLES), expect_changed=TABLES[:2], reset=_reset(ctx))
    assert any("did not change expected tables" in v for v in result.violations)


def test_p22a_sweep_sampling_is_reproducible(setup):
    ctx, company = setup
    a = sweep(_atomic_command(ctx, company), AtomicityProbe(TABLES), reset=_reset(ctx), sample=3)
    b = sweep(_atomic_command(ctx, company), AtomicityProbe(TABLES), reset=_reset(ctx), sample=3)
    assert a.ok and b.ok and a.points == b.points and len(a.points) == 3


def test_p22a_probe_for_all_tables_sees_kernel_tables(db):
    probe = AtomicityProbe.for_all_tables()
    assert Sequence._meta.db_table in probe.tables
    assert not any(t.startswith("django_") for t in probe.tables)
    before = probe.fingerprint()
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    assert probe.fingerprint() == before
