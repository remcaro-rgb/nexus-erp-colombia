"""Consolidation on a two-company fixture: translation, eliminations, NCI, consolidation book (T-P16-08 [A])."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.db import DatabaseError

from consolidation import intercompany, reports, service
from consolidation.errors import FxRateMissing, InvalidStateTransition, PeriodClosed, SodViolation
from consolidation.models import (
    ConsolidationEntry,
    ConsolidationEntryLine,
    ConsolidationRun,
    IntercompanyLink,
    RunStatus,
)
from consolidation.tests.conftest import World, s10_world
from kernel import tenancy
from kernel.errors import DomainError, FeatureDisabled, NotFound
from kernel.models import FeatureFlagOverride, OutboxMessage
from ledger.models import JournalEntry

pytestmark = pytest.mark.django_db
D = Decimal


def _group(world: World, currency: str = "COP", hist: str | None = None) -> Any:
    g = world.group(currency=currency)

    def fn(u: Any) -> None:
        service.configure_member(
            u,
            g,
            world.b,
            ownership_pct=D(80),
            investment_account_code="120505",
            equity_historical_rate=D(hist) if hist else None,
        )
        if hist:
            service.configure_member(u, g, world.a, equity_historical_rate=D(hist))

    world.run(fn)
    return g


def _run(
    world: World, group: Any, period: str = "2026-11", lines: list[dict[str, Any]] | None = None
) -> ConsolidationRun:
    data = {"group_id": str(group.id), "period": period, "document_date": "2026-11-30", "lines": lines or []}
    return world.run(lambda u: service.create_run(u, data))


def _post(world: World, run: ConsolidationRun, ctx: Any = None) -> ConsolidationRun:
    ctx = ctx or world.poster

    def fn(u: Any) -> ConsolidationRun:
        r = service.get_run(ctx, run.id, "consolidation.run.post", lock=True)
        return service.post_run(u, r)

    return world.run(fn, ctx)


def _rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["account"]: r for r in report["rows"] if r["account"]}


def test_T_P16_08_eliminations_on_s10_two_company_fixture(world: World) -> None:
    s10_world(world)
    group = _group(world)
    run = _post(world, _run(world, group))
    assert run.status == RunStatus.POSTED
    with tenancy.scope(world.ctx):
        kinds = {e.kind: e for e in ConsolidationEntry.objects.filter(run=run)}
        assert set(kinds) == {"IC_BALANCE", "IC_REVENUE_COGS", "UNREALIZED_PROFIT", "INVESTMENT_EQUITY"}
        for e in kinds.values():
            assert e.total_debit == e.total_credit
        assert kinds["UNREALIZED_PROFIT"].total_debit == D("35000")  # S10: 150,000 − 115,000 still in B's stock
        assert kinds["IC_REVENUE_COGS"].total_debit == D("150000")
        data = reports.RunData.from_posted(ConsolidationRun.objects.get(id=run.id))
    bal = reports.balance(data)
    rows = _rows(bal)
    assert rows["111005"]["consolidated"] == "1050000.0000"
    assert rows["143505"]["consolidated"] == "200000.0000"  # inventory back at the group's cost
    assert rows["130505"]["consolidated"] == "0.0000" and rows["233505"]["consolidated"] == "0.0000"
    assert rows["120505"]["consolidated"] == "0.0000"  # investment eliminated
    assert rows["160505"]["consolidated"] == "50000.0000"  # goodwill = 450,000 − 80 % × 500,000
    assert rows["390505"]["consolidated"] == "100000.0000"  # NCI = 20 % × 500,000
    assert rows["310505"]["consolidated"] == "1200000.0000"  # only the parent's capital remains
    assert bal["totals"]["assets"] == "1328500.0000" and bal["totals"]["difference"] == "0.0000"
    pnl = reports.income_statement(data, "599005")
    prow = _rows(pnl)
    assert prow["413505"]["consolidated"] == "0.0000" and prow["613505"]["consolidated"] == "0.0000"
    assert pnl["totals"]["net_income"] == "0.0000"
    elim = reports.eliminations(data)
    assert elim["totals"]["debit"] == elim["totals"]["credit"]
    # subsidiaries' books untouched: no journal entry references the run
    with tenancy.scope(world.ctx):
        assert not JournalEntry.objects.filter(source_id=run.id).exists()
        assert OutboxMessage.objects.filter(event_type="consolidation.run.posted.v1").count() == 1


def test_T_P16_08_partially_consumed_goods_reduce_unrealized_profit(world: World) -> None:
    s10_world(world)
    with tenancy.scope(world.ctx):
        link = IntercompanyLink.objects.get()
    world.run(lambda u: intercompany.record_consumption(u, link.id, D("60000")))
    group = _group(world)
    draft = _run(world, group)
    preview = world.run(lambda u: service.preview(u, service.get_run(world.ctx, draft.id, "consolidation.run.read")))
    up = [e for e in preview["journal_entries"] if e["journal"].endswith("UNREALIZED_PROFIT")]
    assert up[0]["lines"][0]["debit"]["amount"] == "21000.0000"  # 90,000 remaining × 35,000/150,000
    assert preview["persisted"] is False
    with tenancy.scope(world.ctx):
        assert not ConsolidationEntry.objects.exists()


def test_T_P16_08_translation_to_usd_with_cta_and_nci(world: World) -> None:
    world.post(
        world.a, [("BANK", "DEBIT", "1000000", None), ("SHARE_CAPITAL", "CREDIT", "1000000", None)], "2026-11-02"
    )
    world.post(world.a, [("INVESTMENT", "DEBIT", "450000", None), ("BANK", "CREDIT", "450000", None)], "2026-11-03")
    world.post(world.b, [("BANK", "DEBIT", "500000", None), ("SHARE_CAPITAL", "CREDIT", "500000", None)], "2026-11-02")
    world.post(world.b, [("BANK", "DEBIT", "41000", None), ("REVENUE", "CREDIT", "41000", None)], "2026-11-12")
    world.rate(world.a, "USD", "2026-11-01", "4000")
    world.rate(world.a, "USD", "2026-11-30", "4100")
    group = _group(world, "USD", hist="4000")
    run = _post(world, _run(world, group))
    assert run.rates["members"][str(world.b)] == {
        "closing": "4100.0000000000",
        "average": "4050.0000000000",
        "historical": "4000.0000000000",
    }
    with tenancy.scope(world.ctx):
        data = reports.RunData.from_posted(ConsolidationRun.objects.get(id=run.id))
        b_contrib = run.summary["by_company"][str(world.b)]
    # B: bank at closing, capital at historical, revenue at average, CTA closes the translated TB
    assert (
        b_contrib["111005"] == "131.9512" and b_contrib["310505"] == "-125.0000" and b_contrib["415505"] == "-10.1235"
    )
    assert b_contrib["381595"] == "3.1723"
    bal = reports.balance(data)
    rows = _rows(bal)
    assert bal["totals"]["difference"] == "0.0000"
    assert rows["120505"]["consolidated"] == "0.0000"
    assert rows["160505"]["consolidated"] == "12.1951"  # COP 50,000 goodwill at the closing rate
    assert rows["390505"]["consolidated"] == "26.3902"  # 20 % × 125 equity + 20 % × result − 20 % × CTA
    pnl = reports.income_statement(data, "599005")
    assert pnl["totals"]["net_income"] == "10.1235"
    assert pnl["totals"]["non_controlling_interest"] == "2.0247"
    assert pnl["totals"]["attributable_to_parent"] == "8.0988"


def test_missing_rate_fails_closed(world: World) -> None:
    world.post(world.b, [("BANK", "DEBIT", "500000", None), ("SHARE_CAPITAL", "CREDIT", "500000", None)], "2026-11-02")
    group = _group(world, "USD", hist="4000")
    run = _run(world, group)
    with pytest.raises(FxRateMissing):
        _post(world, run)
    with tenancy.scope(world.ctx):
        assert ConsolidationRun.objects.get(id=run.id).status == RunStatus.DRAFT


def test_proportional_and_equity_methods(world: World) -> None:
    s10_world(world)
    group = world.group()
    world.run(lambda u: service.configure_member(u, group, world.b, ownership_pct=D(50), method="PROPORTIONAL"))
    res, _ = service_compute(world, _run(world, group))
    agg = res.aggregated()
    assert agg["111005"] == D("550000") + D("250000")  # 50 % of B's bank
    ic = next(e for e in res.entries if e.kind == "IC_REVENUE_COGS")
    assert ic.total_debit == D("75000")  # eliminated to the extent of the interest
    world.run(lambda u: service.configure_member(u, group, world.b, ownership_pct=D(30), method="EQUITY"))
    world.post(world.b, [("BANK", "DEBIT", "10000", None), ("REVENUE", "CREDIT", "10000", None)], "2026-11-19")
    res, _ = service_compute(world, _run(world, group, period="2026-11"))
    assert "111005" not in {a for (c, a) in res.contributions if c == world.b}
    em = next(e for e in res.entries if e.kind == "EQUITY_METHOD")
    assert em.total_debit == D("3000")  # 30 % of B's result


def service_compute(world: World, run: ConsolidationRun) -> Any:
    with tenancy.scope(world.ctx):
        return service.compute(world.ctx, ConsolidationRun.objects.select_related("group").get(id=run.id))


def test_manual_adjustments_must_balance(world: World) -> None:
    s10_world(world)
    group = _group(world)
    line = {"line_no": 1, "account_code": "111005", "unit_price": {"amount": "100", "currency": "COP"}}
    with pytest.raises(DomainError):
        _run(world, group, lines=[line])
    lines = [
        line,
        {**line, "line_no": 2, "account_code": "143505", "unit_price": {"amount": "-100", "currency": "COP"}},
    ]
    run = _post(world, _run(world, group, lines=lines))
    with tenancy.scope(world.ctx):
        assert ConsolidationEntry.objects.filter(run=run, kind="ADJUSTMENT").get().total_debit == D("100")


def test_post_rules_sod_period_and_single_posting(world: World) -> None:
    s10_world(world)
    group = _group(world)
    run = _run(world, group)
    with pytest.raises(SodViolation):
        _post(world, run, world.ctx)  # the preparer cannot post
    posted = _post(world, run)
    with pytest.raises(InvalidStateTransition):
        _post(world, run)
    second = _run(world, group)
    with pytest.raises(InvalidStateTransition):
        _post(world, second)  # one posted run per group and period

    def rev(u: Any) -> Any:
        return service.reverse_run(
            u, service.get_run(world.ctx, posted.id, "consolidation.run.read", lock=True), "corrección"
        )

    world.run(rev)
    with tenancy.scope(world.ctx):
        reversed_run = ConsolidationRun.objects.get(id=posted.id)
        assert reversed_run.status == RunStatus.REVERSED
        lines = ConsolidationEntryLine.objects.filter(entry__run=posted)
        assert sum((ln.debit - ln.credit for ln in lines), D(0)) == D(0)
        assert ConsolidationEntry.objects.filter(run=posted, kind="REVERSAL").count() == 4
    _post(world, second)  # after the reversal a new run can be posted
    world.run(
        lambda u: service.close_period(
            u, service.get_group(world.ctx, group.id, "consolidation.group.update"), "2026-11"
        )
    )
    third = _run(world, group)
    with pytest.raises(PeriodClosed):
        _post(world, third)


def test_consolidation_book_is_append_only(world: World) -> None:
    s10_world(world)
    run = _post(world, _run(world, _group(world)))

    def tamper(u: Any) -> None:
        ConsolidationEntryLine.objects.filter(entry__run_id=run.id).update(debit=D(1))

    with pytest.raises(DatabaseError):
        world.run(tamper)


def test_feature_flag_off_blocks_consolidation(world: World) -> None:
    def off(u: Any) -> None:
        FeatureFlagOverride.objects.filter(tenant_id=world.ctx.tenant_id).delete()

    world.run(off)
    with pytest.raises(FeatureDisabled):
        world.group()


def test_ic_mismatch_is_eliminated_to_difference_account_and_evented(world: World) -> None:
    s10_world(world)
    world.post(
        world.b, [("EXPENSE_IC", "DEBIT", "1000", None), ("IC_PAYABLE", "CREDIT", "1000", world.a)], "2026-11-18"
    )
    run = _post(world, _run(world, _group(world)))
    assert run.summary["ic_differences"][0]["difference"] == "-1000.0000"
    with tenancy.scope(world.ctx):
        e = ConsolidationEntry.objects.get(run=run, kind="IC_BALANCE")
        diff = e.lines.get(group_account="199995")
        assert diff.credit == D("1000")
        ev = OutboxMessage.objects.get(event_type="intercompany.mismatch.detected.v1")
        assert ev.envelope["payload"]["difference"] == {"amount": "-1000.0000", "currency": "COP"}
        assert ev.company_id == world.a


def test_group_with_hidden_company_is_not_disclosed(world: World) -> None:
    s10_world(world)
    group = _group(world)
    only_a = world.user([world.a])
    with tenancy.scope(only_a):
        assert service.visible_groups(only_a) == []
        with pytest.raises(NotFound):
            service.get_group(only_a, group.id, "consolidation.group.read")


def test_period_bounds() -> None:
    from consolidation.ledgerdata import period_bounds

    assert period_bounds("2026-12") == (dt.date(2026, 1, 1), dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert period_bounds("2028-02")[2] == dt.date(2028, 2, 29)
