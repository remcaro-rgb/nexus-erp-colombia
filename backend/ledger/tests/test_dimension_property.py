"""T-ACC-DIM-07 (property): dimension values never change amounts, balances or invariants 1/2."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest
from django.db.models import Sum
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger.models import DIMENSIONS, SLOT_BY_DIMENSION, JournalEntry, JournalLine, LedgerBalance
from ledger.tests.conftest import line

pytestmark = pytest.mark.django_db

ROLES = ("CASH", "BANK", "EXPENSE", "SELLING_EXPENSE", "PAYROLL_EXPENSE")
amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999999.99"), places=2)


@st.composite
def entries(draw):
    debits = draw(st.lists(amounts, min_size=1, max_size=4))
    total = sum(debits)
    n_credit = draw(st.integers(min_value=1, max_value=3))
    cuts = sorted(draw(st.lists(st.integers(min_value=1, max_value=99), min_size=n_credit - 1, max_size=n_credit - 1)))
    shares, prev = [], 0
    for c in [*cuts, 100]:
        shares.append(c - prev)
        prev = c
    credits = [(total * s / 100).quantize(Decimal("0.01")) for s in shares]
    credits[-1] += total - sum(credits)
    credits = [c for c in credits if c > 0] or [total]
    spec = [("DEBIT", draw(st.sampled_from(ROLES)), a) for a in debits]
    spec += [("CREDIT", draw(st.sampled_from(ROLES)), a) for a in credits]
    picks = [{d: draw(st.one_of(st.none(), st.integers(min_value=0, max_value=2))) for d in DIMENSIONS} for _ in spec]
    return spec, picks


@pytest.fixture
def leaves(world):
    out = {}
    for dim in DIMENSIONS:
        group = world.node(dim, f"G-{dim}", "AGRUPADOR")
        out[dim] = [world.node(dim, f"{dim}-{i}", "DETALLE", parent=group) for i in range(3)]
    return out


def _per_account(world, entry_ids):
    with world.scope():
        rows = (
            JournalLine.objects.filter(entry_id__in=entry_ids)
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: (r["d"], r["c"]) for r in rows}


def _balances(world):
    with world.scope():
        out = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for acc, d, c in LedgerBalance.objects.values_list("account_id", "debit", "credit"):
            out[acc][0] += d
            out[acc][1] += c
        return {k: tuple(v) for k, v in out.items()}


@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(data=entries())
def test_T_ACC_DIM_07_dimension_assignment_never_changes_amounts(world, leaves, data):
    spec, picks = data
    plain = [line(role, side, str(amount)) for side, role, amount in spec]
    with_dims = [
        line(
            role,
            side,
            str(amount),
            **{SLOT_BY_DIMENSION[d]: leaves[d][i] for d, i in pick.items() if i is not None},
        )
        for (side, role, amount), pick in zip(spec, picks, strict=True)
    ]

    b0 = _balances(world)
    r_plain = world.post(plain)
    b1 = _balances(world)
    r_dims = world.post(with_dims)
    b2 = _balances(world)

    # Same totals, same per-account amounts, same balance movement per account.
    assert (r_plain.total_debit_cop, r_plain.total_credit_cop) == (r_dims.total_debit_cop, r_dims.total_credit_cop)
    assert _per_account(world, [r_plain.entry_id]) == _per_account(world, [r_dims.entry_id])

    def delta(after, before):
        keys = set(after) | set(before)
        zero = (Decimal(0), Decimal(0))
        return {
            k: (after.get(k, zero)[0] - before.get(k, zero)[0], after.get(k, zero)[1] - before.get(k, zero)[1])
            for k in keys
            if after.get(k, zero) != before.get(k, zero)
        }

    assert delta(b1, b0) == delta(b2, b1)

    with world.scope():
        for result in (r_plain, r_dims):
            primary = JournalEntry.objects.get(id=result.entry_id)
            siblings = JournalEntry.objects.filter(
                source_type=primary.source_type, source_id=primary.source_id, purpose=primary.purpose, status="posted"
            )
            # Invariant 2: exactly one active entry per source and book (NIIF + FISCAL mirrors).
            assert siblings.count() == 2
            assert siblings.values("book_id").distinct().count() == 2
            for e in siblings:
                # Invariant 1: balanced in COP and in the transaction currency (all COP here).
                agg = JournalLine.objects.filter(entry=e).aggregate(d=Sum("debit"), c=Sum("credit"))
                assert agg["d"] == agg["c"] == e.total_debit == e.total_credit
            assert sum((v[0] - v[1] for v in delta(b2, b1).values()), Decimal(0)) == 0
        # Balance table equals the lines (no drift).
        lines = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for acc, d, c in JournalLine.objects.values_list("account_id", "debit", "credit"):
            lines[acc][0] += d
            lines[acc][1] += c
        assert {k: tuple(v) for k, v in lines.items()} == _balances(world)
