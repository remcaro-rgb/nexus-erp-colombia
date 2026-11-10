"""T-INV-LOC-04 — concurrent moves out of one bin never go negative (barrier threads, one connection each)."""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections

from inventory.models import StockMove, StockQuant
from inventory.stock.service import SERVICE
from kernel import ids
from kernel.interfaces.common import DocumentRef
from kernel.uow import run_in_unit_of_work

D = Decimal


def run_threads(n: int, target: Any) -> tuple[list[Any], list[BaseException]]:
    results: list[Any] = [None] * n
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def wrapper(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = target(i)
        except BaseException as exc:  # collected and asserted by the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results, errors


@pytest.mark.django_db(transaction=True)
def test_T_INV_LOC_04_concurrent_moves_out_of_one_bin_never_go_negative(inv):
    wh = inv.warehouse("WHC")
    item = inv.item("P-CONC")
    alm, des, rec = inv.bin(wh, "ALMACEN"), inv.bin(wh, "DESPACHO"), inv.bin(wh, "RECEPCION")
    inv.receive(inv.line(item, wh, "10", location=alm, cost="1000"))
    ctx, company = inv.ctx(), inv.company()

    def target(i: int) -> Any:
        doc = DocumentRef("test.concurrency", ids.new_id(), f"C-{i}")
        if i % 3 == 0:  # issues out of ALMACEN
            return run_in_unit_of_work(
                lambda u: SERVICE.issue(u, company, doc, [inv.line(item, wh, "3", location=alm)], inv_today()), ctx
            )
        dst = des if i % 3 == 1 else rec
        return run_in_unit_of_work(
            lambda u: SERVICE.internal_move(
                u,
                company,
                doc,
                line_ref="1",
                item_id=item.id,
                from_location_id=alm.id,
                to_location_id=dst.id,
                quantity=D(3),
                move_date=inv_today(),
            ),
            ctx,
        )

    results, errors = run_threads(12, target)
    ok = [r for r in results if r is not None]
    assert len(ok) == 3, (ok, errors)  # 10 units / 3 per move
    assert len(errors) == 9 and all(getattr(e, "code", None) == "stock_insufficient" for e in errors), errors
    with inv.scope():
        quants = {q.location_id: q.on_hand for q in StockQuant.objects.filter(item_id=item.id)}
        moved_out = sum(m.quantity for m in StockMove.objects.filter(item_id=item.id, from_location_id=alm.id))
        issued = sum(m.quantity for m in StockMove.objects.filter(item_id=item.id, kind="ISSUE"))
    assert quants[alm.id] == D(1) and all(v >= 0 for v in quants.values())
    # 9 units left the bin; issued units left the warehouse, moved units are in the other bins (never twice)
    assert moved_out == D(9) and sum(quants.values()) == D(10) - issued


def inv_today() -> Any:
    from inventory.tests.conftest import TODAY

    return TODAY
