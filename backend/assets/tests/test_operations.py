"""Runs, revaluation, impairment, estimates, units, transfers, components and counts (T-P14-03/05/07/08)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from assets import service
from assets.models import (
    Asset,
    AssetBookValue,
    DepreciationLine,
    DepreciationRun,
    Impairment,
    RegisterEntry,
    Revaluation,
)
from assets.tests.conftest import World
from kernel import ids, tenancy
from kernel.errors import DomainError, ParameterUnknown
from kernel.models import OutboxMessage
from kernel.testing.tenancy import make_context
from ledger.dimensions import service as dims

pytestmark = pytest.mark.django_db
D = Decimal


def active_laptop(world: World, code: str = "LAP-001", cost: str = "4800000", **kw: Any) -> Asset:
    asset = world.asset(code, cost=cost, acquired="2026-03-10", **kw)
    return world.activate(asset, "2026-03-10")


def test_T_P14_03_run_idempotency_posting_twice_one_entry(world: World) -> None:
    active_laptop(world)
    period = world.period("2026-04")
    data = {"document_date": "2026-04-30", "period_id": str(period.id), "book_id": str(world.niif.id)}
    run = world.run(lambda u: service.create_run(u, world.company, data))
    first, replay1 = world.run(lambda u: service.post_run(u, run.id, world.company))
    second, replay2 = world.run(lambda u: service.post_run(u, run.id, world.company))
    assert (replay1, replay2) == (False, True)
    assert first.journal_entry_id == second.journal_entry_id
    entries = world.entries("assets.depreciation_run")
    assert len(entries) == 1 and entries[0].book.code == "NIIF"
    # a second run for the same company + book + period is refused
    with pytest.raises(DomainError) as err:
        world.run(lambda u: service.create_run(u, world.company, data))
    assert err.value.code == "depreciation_run_exists"
    # the other book is independent
    world.depreciate("2026-04", world.fiscal)
    assert len(world.entries("assets.depreciation_run")) == 2
    with tenancy.scope(world.ctx):
        events = OutboxMessage.objects.filter(event_type="assets.depreciation_run.posted.v1").count()
        assert DepreciationLine.objects.filter(run_id=run.id).count() == 1
    assert events == 2
    # a posted run is immutable
    with pytest.raises(DomainError) as err:
        world.run(lambda u: service.update_run(u, DepreciationRun.objects.get(id=run.id), {"notes": "x"}))
    assert err.value.code == "document_immutable"


def test_T_P14_03_run_failure_rolls_back_everything(world: World) -> None:
    """Failure injection: an error after the GL entry rolls back the run, its detail and the register."""
    asset = active_laptop(world)
    period = world.period("2026-04")
    data = {"document_date": "2026-04-30", "period_id": str(period.id), "book_id": str(world.niif.id)}

    def fn(u: Any) -> None:
        run = service.create_run(u, world.company, data)
        service.post_run(u, run.id, world.company)
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        world.run(fn)
    assert world.entries("assets.depreciation_run") == []
    with tenancy.scope(world.ctx):
        assert not DepreciationRun.objects.exists()
        assert not RegisterEntry.objects.filter(kind="DEPRECIATION").exists()
    assert world.book_value(asset, world.niif).accumulated == 0


def test_T_P14_05_revaluation_and_impairment_entries(world: World) -> None:
    asset = active_laptop(world)
    world.months("2026-04", "2026-06", world.niif)  # 3 × 133,333.33 = 399,999.99 → NBV 4,400,000.01
    rev = world.run(
        lambda u: service.create_valuation(
            u,
            Revaluation,
            world.company,
            {
                "asset_id": str(asset.id),
                "document_date": "2026-06-30",
                "lines": [
                    {
                        "line_no": 1,
                        "description": "Avalúo (SINTÉTICO)",
                        "amount": {"amount": "5000000", "currency": "COP"},
                    }
                ],
            },
        )
    )
    sim = world.run(lambda u: service.simulate_valuation(u, rev))
    assert sim["persisted"] is False and len(sim["journal_entries"]) == 1
    assert world.entries("assets.revaluation") == []  # simulation writes nothing
    posted = world.run(lambda u: service.post_valuation(u, Revaluation, world.company, rev.id))
    assert posted.status == "POSTED" and posted.total == D("599999.99")
    # NIIF only: elimination of accumulated depreciation + surplus in OCI; the fiscal book is untouched
    assert world.balance("381005", "NIIF") == D("-599999.99")
    assert world.balance("381005", "FISCAL") == 0
    assert world.balance("159215", "NIIF") == 0
    assert world.balance("152805", "NIIF") == D("5000000.00")
    assert world.balance("152805", "FISCAL") == D("4800000.00")
    bv = world.book_value(asset, world.niif)
    assert (bv.cost, bv.accumulated, bv.surplus) == (D("5000000"), D(0), D("599999.99"))
    # prospective: 33 remaining months over the revalued amount
    run = world.depreciate("2026-07", world.niif)
    assert run.total == D("151515.15")  # 5,000,000 / 33

    imp = world.run(
        lambda u: service.create_valuation(
            u,
            Impairment,
            world.company,
            {
                "asset_id": str(asset.id),
                "document_date": "2026-07-31",
                "lines": [
                    {
                        "line_no": 1,
                        "description": "Importe recuperable (SINTÉTICO)",
                        "amount": {"amount": "4000000", "currency": "COP"},
                    }
                ],
            },
        )
    )
    posted_imp = world.run(lambda u: service.post_valuation(u, Impairment, world.company, imp.id))
    assert posted_imp.total == D("848484.85")  # NBV 4,848,484.85 − 4,000,000
    assert world.balance("519910", "NIIF") == D("848484.85")
    assert world.balance("159905", "NIIF") == D("-848484.85")
    assert world.balance("519910", "FISCAL") == 0
    bv = world.book_value(asset, world.niif)
    assert bv.nbv == D("4000000.00")
    assert world.depreciate("2026-08", world.niif).total == D("125000.00")  # 4,000,000 / 32


def test_revaluation_decrease_uses_surplus_then_loss(world: World) -> None:
    asset = active_laptop(world, cost="1000000")

    def post(model: Any, amount: str, day: str) -> Any:
        doc = world.run(
            lambda u: service.create_valuation(
                u,
                model,
                world.company,
                {
                    "asset_id": str(asset.id),
                    "document_date": day,
                    "lines": [{"line_no": 1, "amount": {"amount": amount}}],
                },
            )
        )
        return world.run(lambda u: service.post_valuation(u, model, world.company, doc.id))

    post(Revaluation, "1200000", "2026-03-31")  # +200,000 surplus
    post(Revaluation, "900000", "2026-03-31")  # −300,000: 200,000 from surplus, 100,000 loss
    assert world.balance("381005", "NIIF") == 0
    assert world.balance("539595", "NIIF") == D("100000.00")
    assert world.book_value(asset, world.niif).surplus == 0


def test_T_P14_07_units_of_production_run(world: World) -> None:
    asset = world.asset("MAQ-001", cost="1000000", acquired="2026-03-01")
    world.activate(asset, "2026-03-01", books=[{"book_code": "NIIF", "method": "UNITS", "units_total": "100000"}])
    lines = [{"line_no": 1, "item_id": str(asset.id), "quantity": {"value": "12500", "uom": "H"}}]
    run = world.depreciate("2026-04", world.niif, lines)
    assert run.total == D("125000.00")
    with tenancy.scope(world.ctx):
        detail = DepreciationLine.objects.get(run_id=run.id)
    assert detail.units == D("12500") and detail.nbv_after == D("875000.00")
    assert world.depreciate("2026-05", world.niif, []).total == 0  # no units → no charge
    bv = world.book_value(asset, world.niif)
    assert bv.units_used == D("12500")


def test_T_P14_08_change_of_estimate_is_prospective(world: World, asset_services: dict[str, Any]) -> None:
    asset = active_laptop(world)
    world.months("2026-04", "2027-03", world.niif)  # 12 months
    bv = world.book_value(asset, world.niif)
    assert bv.accumulated == D("1599999.96")
    with tenancy.scope(world.ctx):
        posted_before = list(DepreciationLine.objects.filter(asset=asset).values_list("amount", flat=True))

    def change(u: Any) -> AssetBookValue:
        obj = service.get_asset(u, world.company, asset.id, lock=True)
        return service.change_estimate(
            u,
            obj,
            "NIIF",
            useful_life_months=24,
            reason="Obsolescencia (SINTÉTICO)",
            effective_date=dt.date(2027, 4, 1),
        )

    world.run(change)
    run = world.depreciate("2027-04", world.niif)
    assert run.total == D("266666.67")  # 3,200,000.04 / 12 remaining months
    with tenancy.scope(world.ctx):
        after = list(
            DepreciationLine.objects.filter(asset=asset).order_by("run__period_start").values_list("amount", flat=True)
        )
    assert after[:12] == posted_before  # past charges unchanged
    actions = [rec[2].action for rec in asset_services["audit"].records]
    assert "assets.asset.change_estimate" in actions
    with pytest.raises(DomainError) as err:
        world.run(
            lambda u: service.change_estimate(
                u,
                service.get_asset(u, world.company, asset.id),
                "NIIF",
                useful_life_months=30,
                reason="",
                effective_date=dt.date(2027, 5, 1),
            )
        )
    assert err.value.code == "reason_required"
    world.months("2027-05", "2028-03", world.niif)
    assert world.book_value(asset, world.niif).nbv == 0
    with tenancy.scope(world.ctx):
        asset.refresh_from_db()
    assert world.book_value(asset, world.niif).status == "FULLY_DEPRECIATED"


def test_fiscal_life_above_parameter_rate_is_refused(world: World) -> None:
    asset = world.asset("LAP-FAST")
    with pytest.raises(DomainError) as err:
        world.activate(asset, books=[{"book_code": "FISCAL", "useful_life_months": 24}])  # 50 %/year > 20 %
    assert err.value.extra.get("reason") == "fiscal_rate_exceeded"
    world.activate(asset, books=[{"book_code": "FISCAL", "useful_life_months": 72}])
    snap = world.book_value(asset, world.fiscal).parameter_snapshot
    assert snap["code"] == "assets.fiscal.max_annual_rate.computo" and snap["synthetic"] is True


def test_unknown_fiscal_parameter_fails_closed(world: World, asset_services: dict[str, Any]) -> None:
    asset_services["params"].values.clear()
    with pytest.raises(DomainError) as err:
        world.activate(world.asset("LAP-UNK"))
    # fails closed with ParameterUnknown (HTTP 422); kernel.parameters.enforce puts the parameter code in ``code``
    # (reported to P01 in CCR P14-6), so the class is asserted, not the code string
    assert isinstance(err.value, ParameterUnknown) and err.value.status == 422


def test_transfer_reclassifies_by_dimension(world: World) -> None:
    cc1 = world.run(
        lambda u: dims.create_node(
            u,
            world.company,
            "cost_center",
            dims.NodeInput(
                code="CC-ADM", description="Administración (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
            ),
        )
    )
    cc2 = world.run(
        lambda u: dims.create_node(
            u,
            world.company,
            "cost_center",
            dims.NodeInput(
                code="CC-VEN", description="Ventas (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
            ),
        )
    )
    asset = active_laptop(world, dimensions={"cost_center_id": str(cc1.id)})
    world.months("2026-04", "2026-05", world.niif)

    def move(u: Any) -> Asset:
        obj = service.get_asset(u, world.company, asset.id, lock=True)
        return service.transfer(
            u,
            obj,
            {"dimensions": {"cost_center_id": str(cc2.id)}, "location": "Sede norte"},
            dt.date(2026, 6, 1),
            "Traslado a ventas",
        )

    world.run(move)
    with tenancy.scope(world.ctx):
        from ledger.models import JournalLine

        by_cc: dict[Any, Decimal] = {}
        for ln in JournalLine.objects.filter(
            company_id=world.company, entry__book__code="NIIF", account__code="152805"
        ):
            by_cc[ln.cost_center_id] = by_cc.get(ln.cost_center_id, D(0)) + ln.debit - ln.credit
    assert by_cc[cc1.id] == 0 and by_cc[cc2.id] == D("4800000.00")
    run = world.depreciate("2026-06", world.niif)
    with tenancy.scope(world.ctx):
        detail = DepreciationLine.objects.get(run_id=run.id)
    assert detail.cost_center_id == cc2.id  # depreciation carries the asset's default slots
    with tenancy.scope(world.ctx):
        from ledger.models import JournalLine

        dep_lines = JournalLine.objects.filter(entry_id=run.journal_entry_id, account__code="516015")
        assert {ln.cost_center_id for ln in dep_lines} == {cc2.id}


def test_component_partial_disposal(world: World) -> None:
    parent = active_laptop(world, code="SRV-001", cost="10000000")
    comp = world.asset("SRV-001-DSK", cost="1000000", parent_id=str(parent.id))
    world.activate(comp, "2026-03-10")
    with pytest.raises(DomainError) as err:
        world.run(
            lambda u: service.dispose(
                u,
                service.get_asset(u, world.company, parent.id, lock=True),
                {"kind": "SCRAP"},
                dt.date(2026, 4, 30),
                "Baja",
            )
        )
    assert err.value.extra.get("reason") == "components_active"
    world.run(
        lambda u: service.dispose(
            u,
            service.get_asset(u, world.company, comp.id, lock=True),
            {"kind": "SCRAP"},
            dt.date(2026, 4, 30),
            "Disco dañado",
        )
    )
    assert world.balance("531515", "NIIF") == D("1000000.00")  # scrap: NBV to loss
    with tenancy.scope(world.ctx):
        assert Asset.objects.get(id=parent.id).lifecycle == "ACTIVE"
    with pytest.raises(DomainError) as err:
        world.run(
            lambda u: service.dispose(
                u,
                service.get_asset(u, world.company, parent.id, lock=True),
                {"kind": "SCRAP"},
                dt.date(2026, 4, 30),
                "",
            )
        )
    assert err.value.code == "reason_required"


def test_activation_with_opening_balances(world: World) -> None:
    asset = world.asset("OLD-001", cost="3600000", acquired="2024-01-15")
    world.activate(
        asset,
        "2026-01-01",
        counterpart_role="OPENING_BALANCE_EQUITY",
        books=[
            {"book_code": "NIIF", "opening_accumulated": "2400000", "opening_periods": 24},
            {"book_code": "FISCAL", "opening_accumulated": "1440000", "opening_periods": 24},
        ],
    )
    assert world.balance("159215", "NIIF") == D("-2400000.00")
    assert world.balance("159215", "FISCAL") == D("-1440000.00")
    run = world.depreciate("2026-02", world.niif)
    assert run.total == D("100000.00")  # 1,200,000 remaining over 12 months


def test_physical_count_reconciles_and_requires_another_approver(world: World, asset_services: dict[str, Any]) -> None:
    active_laptop(world, code="LAP-A")
    active_laptop(world, code="LAP-B")
    count = world.run(
        lambda u: service.create_count(
            u,
            world.company,
            {
                "document_date": "2026-11-09",
                "lines": [{"line_no": 1, "description": "LAP-A"}, {"line_no": 2, "description": "ZZZ-999"}],
            },
        )
    )
    submitted = world.run(lambda u: service.submit_count(u, count))
    assert submitted.discrepancies == {
        "found": ["LAP-A"],
        "missing": ["LAP-B"],
        "unknown": ["ZZZ-999"],
        "duplicates": [],
    }
    with pytest.raises(DomainError) as err:
        world.run(lambda u: service.approve_count(u, submitted))
    assert err.value.code == "sod_violation"
    other = make_context(tenant_id=world.ctx.tenant_id, company_ids=[world.company, world.other_company])
    approved = world.run(lambda u: service.approve_count(u, submitted), ctx=other)
    assert approved.status == "APPROVED"


def test_register_rows_are_append_only(world: World) -> None:
    from django.db import DatabaseError

    active_laptop(world)

    def tamper(u: Any) -> None:
        RegisterEntry.objects.filter(company_id=world.company).update(cost_delta=D(1))

    with pytest.raises(DatabaseError):
        world.run(tamper)


def test_other_company_never_sees_assets(world: World) -> None:
    active_laptop(world)
    ctx_b = make_context(
        tenant_id=world.ctx.tenant_id, company_ids=[world.other_company], principal_id=world.ctx.principal_id
    )
    with tenancy.scope(ctx_b):
        assert not Asset.objects.exists()  # forced RLS: company A rows are invisible
        assert not RegisterEntry.objects.exists()
    with pytest.raises(DomainError):
        world.run(lambda u: service.get_asset(u, world.company, ids.new_id()), ctx=ctx_b)
