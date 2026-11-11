"""Intercompany automation: S10 atomic both sides (T-P16-04), IC reconciliation mirrors (T-P16-05), system principal."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from consolidation import intercompany
from consolidation.interface import IcSettlementRequest
from consolidation.models import IntercompanyDocument, IntercompanyLink
from consolidation.tests.conftest import World, bootstrap_ledger, s10_world
from kernel import ids, services, tenancy
from kernel.errors import DomainError, PermissionDenied
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.posting import PostingService
from ledger.models import JournalEntry, JournalLine

pytestmark = pytest.mark.django_db
D = Decimal


def _lines(world: World, company: Any, source_type: str) -> dict[str, tuple[Decimal, Decimal]]:
    with tenancy.scope(world.ctx):
        out: dict[str, tuple[Decimal, Decimal]] = {}
        book_primary = JournalLine.objects.filter(
            company_id=company, entry__source_type=source_type, entry__book__primary=True
        )
        for ln in book_primary.select_related("account"):
            d, c = out.get(ln.account.code, (D(0), D(0)))
            out[ln.account.code] = (d + ln.debit, c + ln.credit)
        return out


def test_T_P16_04_s10_sale_posts_both_companies_in_one_transaction(world: World) -> None:
    world.agreement()
    world.post(
        world.a, [("INVENTORY", "DEBIT", "200000", None), ("SHARE_CAPITAL", "CREDIT", "200000", None)], "2026-11-02"
    )
    res = world.run(lambda u: intercompany.post_sale(u, world.s10_request()))
    a = _lines(world, world.a, "sales.invoice")
    assert a["130505"] == (D("178500"), D(0))  # Dr IC_RECEIVABLE
    assert a["413505"] == (D(0), D("150000"))  # Cr REVENUE_IC
    assert a["240805"] == (D(0), D("28500"))  # Cr VAT_PAYABLE
    assert a["613505"] == (D("115000"), D(0))  # Dr COGS
    assert a["143505"] == (D(0), D("115000"))  # Cr INVENTORY
    b = _lines(world, world.b, intercompany.MIRROR_SOURCE)
    assert b["143505"] == (D("150000"), D(0))  # Dr INVENTORY
    assert b["240810"] == (D("28500"), D(0))  # Dr VAT_RECOVERABLE
    assert b["233505"] == (D(0), D("178500"))  # Cr IC_PAYABLE
    with tenancy.scope(world.ctx):
        link = IntercompanyLink.objects.get(id=res.link_id)
        doc = IntercompanyDocument.objects.get(id=res.b_document_id)
        assert link.a_entry_ids and link.b_entry_ids and str(res.b_entry_ids[0]) == link.b_entry_ids[0]
        assert doc.number.startswith("FPM-") and doc.total == D("178500")
        assert (link.amount, link.cost_amount, link.goods_amount) == (D("150000"), D("115000"), D("150000"))
        # IC party on both sides (pairing for reconciliation and eliminations)
        b_line = JournalLine.objects.get(company_id=world.b, account__code="233505", entry__book__primary=True)
        assert (b_line.party_id, b_line.party_kind) == (world.a, "company")


def test_T_P16_04_failure_on_b_rolls_back_a(world: World, ic_services: dict[str, Any]) -> None:
    world.agreement()
    real = ic_services["ledger"]

    class FailingOnB:
        def post(self, uow: Any, request: Any) -> Any:
            if request.company_id == world.b:
                raise DomainError("injected failure on company B")
            return real.post(uow, request)

        def simulate(self, uow: Any, request: Any) -> Any:
            return real.simulate(uow, request)

        def reverse(self, uow: Any, entry_id: Any, reversal_date: Any, reason: str) -> Any:
            return real.reverse(uow, entry_id, reversal_date, reason)

    req = world.s10_request()
    with services.override(PostingService, FailingOnB()), pytest.raises(DomainError, match="injected"):
        world.run(lambda u: intercompany.post_sale(u, req))
    assert world.entries(world.a, "sales.invoice") == []
    assert world.entries(world.b, intercompany.MIRROR_SOURCE) == []
    with tenancy.scope(world.ctx):
        assert not IntercompanyLink.objects.exists()
        assert not IntercompanyDocument.objects.exists()
    # the same request succeeds afterwards (nothing half-written blocks it)
    world.run(lambda u: intercompany.post_sale(u, req))
    assert len(world.entries(world.a, "sales.invoice")) >= 1


def test_T_P16_04_missing_account_in_b_rolls_back_a(world: World) -> None:
    c = ids.new_id()
    ctx = world.user([world.a, c])
    bootstrap_ledger(ctx, c, skip_roles=("IC_PAYABLE",))
    data = {"code": "AC", "name": "A → C", "company_a_id": str(world.a), "company_b_id": str(c), "accounts": {}}
    world.run(lambda u: intercompany.create_agreement(u, data), ctx)
    req = world.s10_request()
    req = type(req)(world.a, c, req.source, req.document_date, req.lines)
    with pytest.raises(DomainError):
        world.run(lambda u: intercompany.post_sale(u, req), ctx)
    assert world.entries(world.a, "sales.invoice") == []


def test_T_P16_04_replay_posts_once(world: World) -> None:
    world.agreement()
    req = world.s10_request()
    first = world.run(lambda u: intercompany.post_sale(u, req))
    mirrored = len(world.entries(world.b, intercompany.MIRROR_SOURCE))
    second = world.run(lambda u: intercompany.post_sale(u, req))
    assert second.replayed and second.link_id == first.link_id
    assert len(world.entries(world.b, intercompany.MIRROR_SOURCE)) == mirrored
    with tenancy.scope(world.ctx):
        assert IntercompanyLink.objects.count() == 1
        assert IntercompanyDocument.objects.count() == 1


def test_T_P16_04_b_side_written_by_system_principal_for_a_only_user(world: World) -> None:
    world.agreement()
    seller = world.user([world.a])  # no access to company B
    res = world.run(lambda u: intercompany.post_sale(u, world.s10_request()), seller)
    assert res.b_document_number == ""  # B's document is not disclosed to an A-only user
    with tenancy.scope(world.ctx):
        doc = IntercompanyDocument.objects.get(id=res.b_document_id)
        entry = JournalEntry.objects.filter(company_id=world.b, source_type=intercompany.MIRROR_SOURCE).first()
    assert doc.created_by == intercompany.SYSTEM_PRINCIPAL and doc.on_behalf_of == seller.principal_id
    assert entry is not None and entry.created_by == intercompany.SYSTEM_PRINCIPAL
    with tenancy.scope(seller):  # RLS: the A-only user cannot read B's mirror document
        assert not IntercompanyDocument.objects.filter(id=res.b_document_id).exists()
    actions = [(e.action, e.resource_type) for _r, _t, e, _h in world.audit.records]
    assert ("create", "intercompany.document") in actions


def test_T_P16_04_requires_ic_permission_in_a(world: World) -> None:
    world.agreement()
    nobody = world.user([world.a, world.b], perms=["intercompany.agreement.read"])
    with pytest.raises(PermissionDenied):
        world.run(lambda u: intercompany.post_sale(u, world.s10_request()), nobody)
    assert world.entries(world.a, "sales.invoice") == []


def test_T_P16_05_reconciliation_mirrors_after_sale_settlement_and_netting(world: World) -> None:
    s10_world(world)
    rows = intercompany_rows(world)
    assert rows[0]["status"] == "MIRRORED" and rows[0]["a_receivable_net"] == "178500.0000"
    assert rows[0]["b_payable_net"] == "178500.0000"
    settle = IcSettlementRequest(
        world.a, world.b, DocumentRef("treasury.receipt", ids.new_id(), "RC-1"), dt.date(2026, 11, 15), D("100000")
    )
    world.run(lambda u: intercompany.settle(u, settle))
    net = IcSettlementRequest(
        world.a, world.b, DocumentRef("intercompany.netting", ids.new_id(), "N-1"), dt.date(2026, 11, 16), D("8500")
    )
    world.run(lambda u: intercompany.net(u, net))
    rows = intercompany_rows(world)
    # netting moves A's receivable into its payable account (and B's the other way): net positions unchanged
    assert rows[0]["status"] == "MIRRORED" and rows[0]["a_receivable_net"] == "78500.0000"
    assert rows[0]["difference"] == "0.0000"


def test_T_P16_05_reconciliation_detects_mismatch(world: World) -> None:
    s10_world(world)
    # B books an extra IC payable to A that A never invoiced
    world.post(
        world.b, [("EXPENSE_IC", "DEBIT", "1000", None), ("IC_PAYABLE", "CREDIT", "1000", world.a)], "2026-11-18"
    )
    rows = intercompany_rows(world)
    assert rows[0]["status"] == "MISMATCH" and rows[0]["difference"] == "-1000.0000"


def test_T_P16_05_reconciliation_hides_pairs_with_an_inaccessible_company(world: World) -> None:
    s10_world(world)
    only_a = world.user([world.a])
    assert intercompany.reconciliation_rows(only_a, dt.date.max) == []


def test_consumption_reduces_goods_and_is_bounded(world: World) -> None:
    s10_world(world)
    with tenancy.scope(world.ctx):
        link = IntercompanyLink.objects.get()
    world.run(lambda u: intercompany.record_consumption(u, link.id, D("60000")))
    with pytest.raises(DomainError):
        world.run(lambda u: intercompany.record_consumption(u, link.id, D("90001")))
    with tenancy.scope(world.ctx):
        assert IntercompanyLink.objects.get().consumed_amount == D("60000")


def test_ic_link_is_append_only_in_the_database(world: World) -> None:
    s10_world(world)
    from django.db import DatabaseError

    def tamper(u: Any) -> None:
        IntercompanyLink.objects.update(amount=D(1))

    with pytest.raises(DatabaseError):
        world.run(tamper)


def intercompany_rows(world: World) -> list[dict[str, Any]]:
    with tenancy.scope(world.ctx):
        return intercompany.reconciliation_rows(world.ctx, dt.date(2026, 11, 30))
