"""T-ACC-DIM-02 / 05 / 06: dimension checks inside the posting transaction."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection

from kernel.errors import DomainError
from kernel.models import OutboxMessage
from ledger import rules as rule_matrix
from ledger.dimensions import service as dims
from ledger.errors import DimensionInUse, LedgerError
from ledger.models import Account, DimensionNode, JournalEntry, JournalLine, LedgerBalance
from ledger.tests.conftest import TODAY, line

pytestmark = pytest.mark.django_db


def counts(world):
    with world.scope():
        return (
            JournalEntry.objects.count(),
            JournalLine.objects.count(),
            LedgerBalance.objects.count(),
            OutboxMessage.objects.filter(tenant_id=world.ctx.tenant_id).count(),
            Account.objects.count(),
        )


def test_T_ACC_DIM_02_agrupador_not_postable_rolls_back_document(world):
    group = world.node("cost_center", "ADM", "AGRUPADOR")
    leaf = world.node("cost_center", "ADM-01", "DETALLE", parent=group)
    before = counts(world)

    def module_document(u, _result=None):
        # The module's own document row, written in the same UoW before post().
        Account.objects.create(
            tenant_id=u.ctx.tenant_id, company_id=world.company, code="999999", name="doc", type="memo", nature="debit"
        )

    lines = [line("EXPENSE", "DEBIT", "100000", cost_center_id=group), line("CASH", "CREDIT", "100000")]

    def fn(u):
        module_document(u)
        from kernel import services
        from kernel.interfaces.posting import PostingService

        return services.get(PostingService).post(u, world.request(lines))

    with pytest.raises(LedgerError) as exc:
        world.run(fn)
    assert exc.value.code == "dimension_node_not_postable"
    assert counts(world) == before  # document row, entry, lines, balances, outbox: all rolled back

    # simulate returns the identical error; a DETALLE posts fine.
    with pytest.raises(LedgerError) as sim:
        world.simulate(lines)
    assert (sim.value.code, sim.value.detail) == (exc.value.code, exc.value.detail)
    result = world.post([line("EXPENSE", "DEBIT", "100000", cost_center_id=leaf), line("CASH", "CREDIT", "100000")])
    assert result.total_debit_cop == Decimal("100000.0000")
    with world.scope():
        assert JournalLine.objects.filter(cost_center_id=leaf.id).count() == 2  # NIIF + FISCAL mirror books


def test_T_ACC_DIM_05_used_node_delete_and_type_change_rejected(world):
    leaf = world.node("business_unit", "BU-1", "DETALLE")
    unused = world.node("business_unit", "BU-2", "DETALLE")
    entry = world.post([line("EXPENSE", "DEBIT", "5000", business_unit_id=leaf), line("CASH", "CREDIT", "5000")])

    def load(u, node):
        return dims.get_node(u.ctx, world.company, "business_unit", node.id, lock=True)

    with pytest.raises(DimensionInUse):
        world.run(lambda u: dims.delete_node(u, load(u, leaf), load(u, leaf).etag))
    with pytest.raises(DimensionInUse):
        world.run(lambda u: dims.update_node(u, load(u, leaf), {"node_type": "AGRUPADOR"}, load(u, leaf).etag))

    # The DB refuses it too (trigger + composite FK), whatever the app does.
    with pytest.raises(DatabaseError), world.scope(), connection.cursor() as cur:
        cur.execute("UPDATE ledger_dimension_node SET node_type = 'AGRUPADOR' WHERE id = %s", [leaf.id])
    with pytest.raises(DatabaseError), world.scope(), connection.cursor() as cur:
        cur.execute("DELETE FROM ledger_dimension_node WHERE id = %s", [leaf.id])

    # Description edits stay allowed; unused nodes can be deleted.
    edited = world.run(lambda u: dims.update_node(u, load(u, leaf), {"description": "Nueva desc"}, load(u, leaf).etag))
    assert edited.description == "Nueva desc"
    world.run(lambda u: dims.delete_node(u, load(u, unused), load(u, unused).etag))

    # Archive blocks new references only.
    with world.scope():
        lines_before = list(JournalLine.objects.filter(business_unit_id=leaf.id).values_list("id", "debit", "credit"))
        balances_before = list(LedgerBalance.objects.order_by("id").values_list("id", "debit", "credit"))
    world.run(lambda u: dims.archive_node(u, load(u, leaf), "fin de la unidad"))
    with pytest.raises(LedgerError) as exc:
        world.post([line("EXPENSE", "DEBIT", "1", business_unit_id=leaf), line("CASH", "CREDIT", "1")])
    assert exc.value.code == "dimension_archived"
    with world.scope():
        assert (
            list(JournalLine.objects.filter(business_unit_id=leaf.id).values_list("id", "debit", "credit"))
            == lines_before
        )
        assert list(LedgerBalance.objects.order_by("id").values_list("id", "debit", "credit")) == balances_before
        assert DimensionNode.objects.get(id=leaf.id).status == "ARCHIVED"

    # Corrections of existing postings still work (reversal copies the archived slot).
    from kernel import services
    from kernel.interfaces.posting import PostingService

    rev = world.run(lambda u: services.get(PostingService).reverse(u, entry.entry_id, TODAY, "corrección"))
    with world.scope():
        assert JournalLine.objects.filter(entry__source_type="ledger.reversal", business_unit_id=leaf.id).count() == 2
        assert JournalEntry.objects.get(id=rev.entry_id).reversal_of_id == entry.entry_id
        assert JournalEntry.objects.get(id=entry.entry_id).status == "reversed"

    # Expired (valid_to in the past) behaves like archived for new references.
    old = world.node("business_unit", "BU-OLD", "DETALLE", valid_to="2026-06-30")
    with pytest.raises(LedgerError) as exc:
        world.post([line("EXPENSE", "DEBIT", "1", business_unit_id=old), line("CASH", "CREDIT", "1")])
    assert exc.value.code == "dimension_archived"
    world.post(
        [line("EXPENSE", "DEBIT", "1", business_unit_id=old), line("CASH", "CREDIT", "1")], day=dt.date(2026, 6, 30)
    )


def _set_rules(world, rules):
    def fn(u):
        rs = rule_matrix.rule_set(u.ctx, world.company, lock=True)
        return rule_matrix.replace_rules(u, world.company, rule_matrix.validate_rules(rules), rs.etag)

    world.run(fn)


def _error(fn):
    with pytest.raises(DomainError) as exc:
        fn()
    return exc.value.code, exc.value.detail


def test_T_ACC_DIM_06_rule_matrix_enforced_in_posting_and_simulate(world):
    cc = world.node("cost_center", "CC-1", "DETALLE")
    aux = world.node("aux_abierto", "PRY-1", "DETALLE")
    item = world.node("item_contable", "IT-1", "DETALLE")
    _set_rules(
        world,
        [
            # account: expenses 51* need a cost center
            {"dimension": "cost_center", "scope_type": "account_range", "scope_ref": "51*", "mode": "REQUIRED"},
            # account: cash must never carry a cost center
            {"dimension": "cost_center", "scope_type": "account", "scope_ref": "110505", "mode": "FORBIDDEN"},
            # document type: purchase invoices require an open auxiliary
            {
                "dimension": "aux_abierto",
                "scope_type": "document_type",
                "scope_ref": "purchasing.invoice",
                "mode": "REQUIRED",
            },
            # concept: payroll posting forbids item_contable, a more specific higher priority rule allows it on 510506
            {"dimension": "item_contable", "scope_type": "concept", "scope_ref": "payroll.run", "mode": "FORBIDDEN"},
            {
                "dimension": "item_contable",
                "scope_type": "account",
                "scope_ref": "510506",
                "mode": "ALLOWED",
                "priority": 10,
            },
        ],
    )

    cases = [
        # (lines, kwargs, expected code)
        ([line("EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")], {}, "dimension_required"),
        (
            [line("EXPENSE", "DEBIT", "10", cost_center_id=cc), line("CASH", "CREDIT", "10", cost_center_id=cc)],
            {},
            "dimension_forbidden",
        ),
        (
            [line("SELLING_EXPENSE", "DEBIT", "10"), line("BANK", "CREDIT", "10")],
            {"source_type": "purchasing.invoice"},
            "dimension_required",
        ),
        (
            [
                line("SELLING_EXPENSE", "DEBIT", "10", aux_abierto_id=aux, item_dimension_id=item),
                line("BANK", "CREDIT", "10", aux_abierto_id=aux),
            ],
            {"purpose": "payroll.run"},
            "dimension_forbidden",
        ),
    ]
    before = counts(world)
    for lines, kw, expected in cases:
        posted = _error(lambda lines=lines, kw=kw: world.post(lines, **kw))
        simulated = _error(lambda lines=lines, kw=kw: world.simulate(lines, **kw))
        assert posted[0] == expected
        assert simulated == posted  # identical code and message
    assert counts(world) == before  # nothing persisted by failed posts or by simulations

    # Valid combinations post; the higher-priority ALLOWED overrides the concept FORBIDDEN on 510506.
    world.post([line("EXPENSE", "DEBIT", "10", cost_center_id=cc), line("CASH", "CREDIT", "10")])
    world.post(
        [
            line("PAYROLL_EXPENSE", "DEBIT", "10", cost_center_id=cc, item_dimension_id=item),
            line("BANK", "CREDIT", "10"),
        ],
        purpose="payroll.run",
    )
    world.post(
        [line("SELLING_EXPENSE", "DEBIT", "10", aux_abierto_id=aux), line("BANK", "CREDIT", "10", aux_abierto_id=aux)],
        source_type="purchasing.invoice",
    )
    # Account.requires_dimensions takes part in the same check.
    with world.scope():
        Account.objects.filter(code="111005").update(requires_dimensions=["business_unit"])
    assert (
        _error(lambda: world.post([line("EXPENSE", "DEBIT", "1", cost_center_id=cc), line("BANK", "CREDIT", "1")]))[0]
        == "dimension_required"
    )


def test_T_ACC_DIM_06_rule_validation(world):
    with pytest.raises(DomainError):
        rule_matrix.validate_rules([{"dimension": "x", "scope_type": "account", "scope_ref": "1", "mode": "REQUIRED"}])
    with pytest.raises(DomainError):
        rule_matrix.validate_rules(
            [{"dimension": "cost_center", "scope_type": "account", "scope_ref": "1", "mode": "MAYBE"}]
        )
    assert (
        rule_matrix.resolve(
            [rule_matrix.Rule("cost_center", "account_range", "5105-5199", "REQUIRED")],
            rule_matrix.LineScope("510506", "x", "y", "GENERAL"),
            [],
        )["cost_center"]
        == "REQUIRED"
    )
