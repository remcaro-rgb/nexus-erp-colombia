"""T-INV-ISSUE-03 — two concurrent issues of the last units: exactly one posts, the other fails stock_insufficient;
stock never goes negative and the failed document leaves no moves, reservations or journal entry behind."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, cast

import pytest

from inventory.issues import service as issues
from inventory.issues.models import StockIssue
from inventory.issues.tests.threads import run_threads
from inventory.models import ItemValuation, Reservation, StockMove, StockQuant
from kernel.uow import run_in_unit_of_work
from ledger.models import JournalEntry

D = Decimal
TODAY = dt.date(2026, 11, 9)  # = inventory.tests.conftest.TODAY (frozen clock)


@pytest.mark.django_db(transaction=True)
def test_T_INV_ISSUE_03_concurrent_issues_of_last_units_one_succeeds_one_stock_insufficient(iw: Any) -> None:
    inv = iw.inv
    wh = inv.warehouse("BOG-CONC", branch="BOG")
    alm = inv.bin(wh, "ALMACEN")
    item = inv.item("P-LAST")
    inv.receive(inv.line(item, wh, "5", location=alm, cost="1000"))
    concept = iw.concept("MUESTRA")
    ctx, company = inv.ctx(), inv.company()

    def draft(n: int) -> StockIssue:
        data = issues.IssueInput(
            concept_id=concept.id,
            warehouse_id=wh.id,
            document_date=TODAY,
            lines=(issues.IssueLineInput(item_id=item.id, quantity=D(5), location_id=alm.id if n == 0 else None),),
        )
        return cast(StockIssue, inv.run(lambda u: issues.create(u, company, data)))

    docs = [draft(0), draft(1)]  # one names the bin, the other uses FEFO picking

    def target(i: int) -> Any:
        return run_in_unit_of_work(lambda u: issues.post(u, docs[i], TODAY), ctx)

    results, errors = run_threads(2, target)
    ok = [r for r in results if r is not None]
    assert len(ok) == 1 and ok[0].status == "POSTED", (results, errors)
    assert len(errors) == 1 and getattr(errors[0], "code", None) == "stock_insufficient", errors
    with inv.scope():
        quants = list(StockQuant.objects.filter(item_id=item.id))
        statuses = sorted(StockIssue.objects.values_list("status", flat=True))
        moved = list(StockMove.objects.filter(item_id=item.id, kind="ISSUE"))
        active = Reservation.objects.filter(item_id=item.id, state="ACTIVE").count()
        entries = JournalEntry.objects.filter(source_type=issues.SOURCE_TYPE, book__code="NIIF").count()
        v = ItemValuation.objects.get(item_id=item.id)
    assert all(q.on_hand >= 0 and q.reserved >= 0 for q in quants)
    assert sum(q.on_hand for q in quants) == D(0)
    assert statuses == ["DRAFT", "POSTED"]  # the loser rolled back completely (still a draft)
    assert [m.quantity for m in moved] == [D(5)] and active == 0 and entries == 1
    assert (v.qty, v.value) == (D(0), D(0))
