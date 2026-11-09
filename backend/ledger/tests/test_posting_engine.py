"""Posting engine core (P05 T-P05 series, supporting the F-ACC-DIM tests)."""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, connections
from django.db.models import Sum
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kernel import ids, services
from kernel.errors import DomainError, UnitOfWorkRequired
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.models import OutboxMessage
from kernel.uow import run_in_unit_of_work
from ledger import periods
from ledger.models import ExchangeRate, JournalEntry, JournalLine, LedgerBalance, Period
from ledger.tests.conftest import TODAY, line

pytestmark = pytest.mark.django_db


def _code(fn):
    with pytest.raises(DomainError) as exc:
        fn()
    return exc.value.code


def test_T_P05_01_random_requests_balanced_or_rejected(world):
    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["DEBIT", "CREDIT"]),
                st.decimals(min_value=Decimal(0), max_value=Decimal(10**9), places=4),
            ),
            min_size=1,
            max_size=6,
        )
    )
    def run(spec):
        lines = [line("EXPENSE" if side == "DEBIT" else "BANK", side, str(a)) for side, a in spec]
        try:
            result = world.post(lines)
        except DomainError as exc:
            assert exc.code in {"entry_unbalanced", "validation_failed"}
            return
        with world.scope():
            for e in JournalEntry.objects.filter(source_id=JournalEntry.objects.get(id=result.entry_id).source_id):
                agg = JournalLine.objects.filter(entry=e).aggregate(d=Sum("debit"), c=Sum("credit"))
                assert agg["d"] == agg["c"]
                rounding = JournalLine.objects.filter(entry=e, is_rounding=True).aggregate(
                    v=Sum("debit") + Sum("credit")
                )["v"]
                assert rounding is None or rounding <= Decimal("1.0000")  # T-P05-02 tolerance

    run()


def test_T_P05_02_rounding_line_generated_within_tolerance(world):
    r = world.post([line("EXPENSE", "DEBIT", "100.50"), line("BANK", "CREDIT", "100.00")])
    with world.scope():
        rounding = JournalLine.objects.get(entry_id=r.entry_id, is_rounding=True)
        assert rounding.credit == Decimal("0.5000") and rounding.account.code == "429581"
    assert (
        _code(lambda: world.post([line("EXPENSE", "DEBIT", "102"), line("BANK", "CREDIT", "100")]))
        == "entry_unbalanced"
    )


def test_T_P05_03_closed_and_soft_closed_periods(world, ledger_services):
    with world.scope():
        nov = Period.objects.get(company_id=world.company, code="2026-11")
    world.run(lambda u: periods.change_status(u, nov, "SOFT_CLOSED"))
    lines = [line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")]
    assert _code(lambda: world.post(lines)) == "period_closed"
    ledger_services["policy"].grant(world.ctx.principal_id, "ledger.period.post_soft_closed")
    world.post(lines)
    with world.scope():
        nov = Period.objects.get(id=nov.id)
    world.run(lambda u: periods.change_status(u, nov, "CLOSED"))
    assert _code(lambda: world.post(lines)) == "period_closed"
    assert _code(lambda: world.post(lines, day=dt.date(2030, 1, 1))) == "period_closed"  # no period defined


def test_T_P05_04_immutability_trigger(world):
    r = world.post([line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")])
    statements = [
        ("UPDATE ledger_journal_line SET debit = debit + 1 WHERE entry_id = %s", [r.entry_id]),
        ("DELETE FROM ledger_journal_line WHERE entry_id = %s", [r.entry_id]),
        ("UPDATE ledger_journal_entry SET memo = 'x' WHERE id = %s", [r.entry_id]),
        ("DELETE FROM ledger_journal_entry WHERE id = %s", [r.entry_id]),
    ]
    for sql, params in statements:
        with pytest.raises(DatabaseError), world.scope(), connection.cursor() as cur:
            cur.execute(sql, params)


def test_T_P05_05_reversal_correctness(world):
    r = world.post([line("EXPENSE", "DEBIT", "250"), line("BANK", "CREDIT", "250")])
    rev = world.run(lambda u: services.get(PostingService).reverse(u, r.entry_id, TODAY, "error de digitación"))
    with world.scope():
        original = JournalEntry.objects.get(id=r.entry_id)
        reversal = JournalEntry.objects.get(id=rev.entry_id)
        assert original.status == "reversed" and original.reversed_by_id == reversal.id
        assert reversal.reversal_of_id == original.id and reversal.total_debit == Decimal("250.0000")
        net = LedgerBalance.objects.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
        assert all(row["d"] == row["c"] for row in net)
    assert _code(lambda: world.run(lambda u: services.get(PostingService).reverse(u, r.entry_id, TODAY, "x"))) == (
        "entry_already_reversed"
    )
    assert _code(lambda: world.run(lambda u: services.get(PostingService).reverse(u, rev.entry_id, TODAY, ""))) == (
        "validation_failed"
    )


def test_T_P05_06_control_accounts_reject_manual_lines(world):
    lines = [line("AR", "DEBIT", "10"), line("SALES_REVENUE", "CREDIT", "10")]
    assert _code(lambda: world.post(lines)) == "account_not_postable"
    world.post(lines, source_type="sales.invoice", journal="SALES")


def test_T_P05_07_atomic_rollback_after_post(world):
    with world.scope():
        before = (JournalEntry.objects.count(), LedgerBalance.objects.count(), OutboxMessage.objects.count())

    def boom(_u, _result):
        raise RuntimeError("module failure after post()")

    with pytest.raises(RuntimeError):
        world.post([line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")], after=boom)
    with world.scope():
        assert (JournalEntry.objects.count(), LedgerBalance.objects.count(), OutboxMessage.objects.count()) == before


def test_post_requires_unit_of_work_and_is_idempotent(world):
    req = world.request([line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")])
    from kernel.uow import UnitOfWork

    with pytest.raises(UnitOfWorkRequired):
        services.get(PostingService).post(UnitOfWork(ctx=world.ctx), req)
    first = world.run(lambda u: services.get(PostingService).post(u, req))
    again = world.run(lambda u: services.get(PostingService).post(u, req))
    assert again.replayed and again.entry_id == first.entry_id


def test_T_P05_09_fx_conversion_and_missing_rate(world):
    usd = PostingLine("BANK", "DEBIT", Decimal("100.00"), currency="USD")
    cop = PostingLine("SELLING_EXPENSE", "CREDIT", Decimal("400000.00"))
    assert _code(lambda: world.post([usd, cop])) == "fx_rate_missing"
    with world.scope():
        ExchangeRate.objects.create(
            tenant_id=world.ctx.tenant_id,
            company_id=world.company,
            currency="USD",
            date=TODAY,
            rate=Decimal("4000.0012345678"),
        )
    r = world.post([usd, cop])
    with world.scope():
        fx = JournalLine.objects.get(entry_id=r.entry_id, is_rounding=True)
        assert fx.credit == Decimal("0.1235")  # 100 × 4000.0012345678 − 400000, FX_ROUNDING within tolerance
    assert r.total_debit_cop == r.total_credit_cop


def test_T_P05_15_simulation_persists_nothing(world):
    with world.scope():
        before = (JournalEntry.objects.count(), JournalLine.objects.count(), LedgerBalance.objects.count())
    res = world.simulate([line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")])
    assert res.entry_number == "SIMULATED" and res.total_debit_cop == Decimal("10.0000")
    with world.scope():
        assert (JournalEntry.objects.count(), JournalLine.objects.count(), LedgerBalance.objects.count()) == before


@pytest.mark.django_db(transaction=True)
def test_T_P05_08_parallel_postings_keep_balances(world):
    n = 24
    errors = []

    def worker():
        try:
            req = PostingRequest(
                company_id=world.company,
                posting_date=TODAY,
                source=DocumentRef("ledger.manual", ids.new_id()),
                purpose="manual",
                lines=(
                    PostingLine("EXPENSE", "DEBIT", Decimal("1.0000")),
                    PostingLine("BANK", "CREDIT", Decimal("1.0000")),
                ),
            )
            run_in_unit_of_work(lambda u: services.get(PostingService).post(u, req), world.ctx)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with world.scope():
        bal = LedgerBalance.objects.filter(account_id__in=JournalLine.objects.values("account_id")).aggregate(
            d=Sum("debit"), c=Sum("credit")
        )
        assert bal["d"] == bal["c"] == Decimal(n * 2)  # two mirror books
