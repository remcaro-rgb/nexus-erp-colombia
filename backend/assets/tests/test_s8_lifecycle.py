"""S8 — fixed asset life cycle in two books (shared/11 S8) and the P14 posting rules (T-P14-01/04/06/09/10).

Synthetic data (NOT LEGALLY VERIFIED): laptop 4,800,000 + VAT 912,000 bought 2026-03-10 by vendor invoice;
NIIF straight line 36 months, salvage 0, start the following month; fiscal 5 years (synthetic 20 %/year cap).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from assets import reports, service
from assets.interface import AssetService, InvoiceLineCapitalization
from assets.models import Asset, Disposal, RegisterEntry
from assets.tests.conftest import World, close_period
from kernel import ids, services, tenancy
from kernel.errors import DomainError
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService

pytestmark = pytest.mark.django_db
D = Decimal


def buy_laptop(world: World, *, fail_after: bool = False) -> Any:
    """What P11 does when a vendor invoice line has an asset category: GL + register in ONE unit of work."""
    invoice = DocumentRef("purchasing.vendor_invoice", ids.new_id(), "FC-SYN-0001")

    def fn(u: Any) -> Any:
        asset_svc = services.get(AssetService)
        cap = asset_svc.capitalize_from_invoice_line(
            u,
            InvoiceLineCapitalization(
                company_id=world.company,
                category_id=world.category.id,
                invoice=invoice,
                line_no=1,
                description="Portátil 14 pulgadas (SINTÉTICO)",
                cost=D("4800000"),
                acquisition_date=dt.date(2026, 3, 10),
                supplier=PartyRef(ids.new_id(), "vendor"),
                in_service_date=dt.date(2026, 3, 10),
                code="LAP-S8",
            ),
        )
        services.get(PostingService).post(
            u,
            PostingRequest(
                company_id=world.company,
                posting_date=dt.date(2026, 3, 10),
                source=invoice,
                purpose="issue",
                journal_code="PURCHASES",
                lines=(
                    PostingLine(cap.cost_account_role, "DEBIT", D("4800000")),
                    PostingLine("VAT_RECOVERABLE", "DEBIT", D("912000")),
                    PostingLine("AP", "CREDIT", D("5712000"), party=PartyRef(ids.new_id(), "vendor")),
                ),
            ),
        )
        if fail_after:
            raise RuntimeError("synthetic failure after posting (failure injection)")
        return cap

    return world.run(fn)


def s8_through_december(world: World) -> Asset:
    cap = buy_laptop(world)
    world.months("2026-04", "2026-12", world.niif)
    world.months("2026-04", "2026-12", world.fiscal)
    with tenancy.scope(world.ctx):
        return Asset.objects.get(id=cap.asset_id)


def test_T_P14_01_s8_exact_values_both_books(world: World) -> None:
    cap = buy_laptop(world)
    niif_runs = world.months("2026-04", "2026-11", world.niif)
    fiscal_runs = world.months("2026-04", "2026-11", world.fiscal)
    assert [r.total for r in niif_runs] == [D("133333.33")] * 8
    assert [r.total for r in fiscal_runs] == [D("80000.00")] * 8
    with tenancy.scope(world.ctx):
        asset = Asset.objects.get(id=cap.asset_id)
    assert world.book_value(asset, world.niif).accumulated == D("1066666.64")
    assert world.book_value(asset, world.fiscal).accumulated == D("640000.00")
    # the depreciation started the month after the in-service date (default convention)
    assert world.book_value(asset, world.niif).depreciation_start == dt.date(2026, 4, 1)
    # tax-difference register: fiscal NBV − NIIF NBV = 426,666.64 (temporary difference)
    with tenancy.scope(world.ctx):
        base = {"tenant_id": world.ctx.tenant_id, "company_id": world.company}
        diffs = reports.temporary_differences(RegisterEntry.objects.filter(**base), dt.date(2026, 11, 30))
    assert diffs == {asset.id: D("426666.64")}
    # separate entries per book, in the respective book ledger
    assert world.balance("159215", "NIIF") == D("-1066666.64")
    assert world.balance("159215", "FISCAL") == D("-640000.00")
    assert world.balance("516015", "NIIF") == D("1066666.64")
    assert world.balance("516015", "FISCAL") == D("640000.00")
    # December: 9 months NIIF = 1,199,999.97, NBV 3,600,000.03
    world.depreciate("2026-12", world.niif)
    bv = world.book_value(asset, world.niif)
    assert bv.accumulated == D("1199999.97")
    assert bv.nbv == D("3600000.03")


def test_T_P14_06_disposal_gain_loss(world: World, asset_services: dict[str, Any]) -> None:
    asset = s8_through_december(world)
    customer = ids.new_id()

    def fn(u: Any) -> Disposal:
        obj = service.get_asset(u, world.company, asset.id, lock=True)
        return service.dispose(
            u,
            obj,
            {"kind": "SALE", "proceeds": "4000000", "vat": "760000", "customer_id": str(customer)},
            dt.date(2026, 12, 31),
            "Venta a empleado (SINTÉTICO)",
        )

    disposal = world.run(fn)
    assert disposal.results["NIIF"]["nbv"] == "3600000.0300"
    assert disposal.results["NIIF"]["gain"] == "399999.9700"
    assert disposal.results["FISCAL"]["nbv"] == "4080000.0000"
    assert disposal.results["FISCAL"]["gain"] == "-80000.0000"
    assert world.balance("424540", "NIIF") == D("-399999.97")  # credit gain
    assert world.balance("531515", "FISCAL") == D("80000.00")  # debit loss
    for book in ("NIIF", "FISCAL"):  # derecognized: cost and accumulated depreciation back to zero
        assert world.balance("152805", book) == 0
        assert world.balance("159215", book) == 0
        assert world.balance("138095", book) == D("4000000.00")  # proceeds pending the P10 invoice
    with tenancy.scope(world.ctx):
        asset.refresh_from_db()
    assert asset.lifecycle == "DISPOSED"
    # disposing twice → asset_already_disposed
    with pytest.raises(DomainError) as err:
        world.run(fn)
    assert err.value.code == "asset_already_disposed"


def test_T_P14_04_capitalization_from_ap_invoice_rollback(world: World) -> None:
    with pytest.raises(RuntimeError):
        buy_laptop(world, fail_after=True)
    with tenancy.scope(world.ctx):
        assert not Asset.objects.filter(company_id=world.company).exists()
        assert not RegisterEntry.objects.filter(company_id=world.company).exists()
    assert world.entries("purchasing.vendor_invoice") == []
    assert world.balance("152805", "NIIF") == 0
    # the same invoice without the failure commits GL + register together
    cap = buy_laptop(world)
    assert world.balance("152805", "NIIF") == D("4800000.00")
    assert world.balance("152805", "FISCAL") == D("4800000.00")
    with tenancy.scope(world.ctx):
        assert RegisterEntry.objects.filter(asset_id=cap.asset_id, kind="ACQUISITION").count() == 2


def test_T_P14_04_below_threshold_is_not_capitalized(world: World) -> None:
    def fn(u: Any) -> None:
        cat = service.AssetCategory.objects.get(id=world.category.id)
        cat.capitalization_threshold_uvt = D("50")  # 50 × synthetic UVT 52,374 = 2,618,700
        cat.save()

    world.run(fn)
    svc = services.get(AssetService)
    assert (
        world.run(
            lambda u: svc.should_capitalize(u, world.company, world.category.id, D("2000000"), dt.date(2026, 3, 10))
        )
        is False
    )
    with pytest.raises(DomainError) as err:
        world.run(
            lambda u: svc.capitalize_from_invoice_line(
                u,
                InvoiceLineCapitalization(
                    world.company,
                    world.category.id,
                    DocumentRef("purchasing.vendor_invoice", ids.new_id()),
                    1,
                    "Mouse (SINTÉTICO)",
                    D("2000000"),
                    dt.date(2026, 3, 10),
                ),
            )
        )
    assert err.value.extra.get("reason") == "below_capitalization_threshold"


def test_T_P14_09_gl_equals_register_per_book(world: World) -> None:
    asset = s8_through_december(world)
    second = world.asset("LAP-002", cost="2400000", acquired="2026-06-15")
    world.activate(second, "2026-06-15")
    world.depreciate("2027-01", world.niif)
    world.depreciate("2027-01", world.fiscal)
    for book in (world.niif, world.fiscal):
        with tenancy.scope(world.ctx):
            rec = reports.gl_vs_register(world.ctx, world.company, book, dt.date(2027, 1, 31))
        assert rec, book.code
        for role_code, row in rec.items():
            assert D(row["difference"]) == 0, (book.code, role_code, row)
    with tenancy.scope(world.ctx):
        rec = reports.gl_vs_register(world.ctx, world.company, world.niif, dt.date(2027, 1, 31))
    # cost of both laptops; LAP-002 started 2026-07 under the default convention
    assert D(rec["FIXED_ASSET_COST"]["gl"]) == D("7200000.0000")
    bv = world.book_value(asset, world.niif)
    assert bv.accumulated == D("1333333.30")


def test_T_P14_10_closed_period_blocks_the_run(world: World) -> None:
    buy_laptop(world)
    close_period(world, "2026-04")
    with pytest.raises(DomainError) as err:
        world.depreciate("2026-04", world.niif)
    assert err.value.code == "period_closed"
    with tenancy.scope(world.ctx):
        assert not service.DepreciationRun.objects.filter(company_id=world.company).exists()
    assert world.entries("assets.depreciation_run") == []
