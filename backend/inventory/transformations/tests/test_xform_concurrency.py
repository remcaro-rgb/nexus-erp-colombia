"""T-INV-XFORM-06 — a transformation and a POS sale race for the same lot: reservations prevent overconsumption and
exactly one of two competing consumers fails cleanly (barrier threads, one DB connection each, real row locks)."""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections
from django.db.models import Sum

from inventory.models import Item, Lot, StockMove, StockQuant
from inventory.stock.service import SERVICE
from inventory.tests.conftest import TODAY
from inventory.transformations import service as xf
from inventory.transformations.models import TransformationOrder
from kernel import ids
from kernel.errors import DomainError
from kernel.interfaces.common import DocumentRef
from kernel.uow import run_in_unit_of_work

D = Decimal
L = xf.LineInput


def run_threads(targets: list[Any]) -> tuple[list[Any], list[BaseException]]:
    results: list[Any] = [None] * len(targets)
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def wrapper(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = targets[i]()
        except BaseException as exc:  # collected and asserted by the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(len(targets))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results, errors


@pytest.mark.django_db(transaction=True)
def test_T_INV_XFORM_06_concurrent_transformation_and_pos_sale_on_same_lot_one_fails_cleanly(xw, inv):
    kg = {"base_uom": "KG", "tracking": Item.Tracking.LOT}
    queso = inv.item("QUESO-BLOQUE", **kg)
    tajado = inv.item("QUESO-TAJADO", base_uom="KG")
    alm = xw.bin()
    with inv.scope():
        lot = Lot.objects.create(
            tenant_id=inv.world.tenant_id, company_id=inv.company(), item_id=queso.id, code="L-QUESO-1"
        )
    xw.stock(queso, "10", "15000", lot=lot)
    ctx, company = inv.ctx(), inv.company()

    def pos_sale(qty: str) -> Any:
        doc = DocumentRef("pos.sale", ids.new_id(), "POS-SYN")
        return run_in_unit_of_work(
            lambda u: SERVICE.issue(u, company, doc, [inv.line(queso, xw.wh, qty, lot=lot)], TODAY), ctx
        )

    def order(qty: str) -> Any:
        return xw.order("PORTIONING", [L("INPUT", queso.id, D(qty), lot_id=lot.id), L("OUTPUT", tajado.id, D(qty))])

    # 1) the transformation reserves 8 kg of the lot; a POS sale of 5 kg can only see 2 kg → fails cleanly
    first = order("8")
    reserved = xw.run(lambda u: xf.reserve(u, first))
    with pytest.raises(DomainError) as exc:
        pos_sale("5")
    assert exc.value.code == "stock_insufficient"
    assert pos_sale("2") is not None  # the unreserved rest is sellable
    posted = xw.post(reserved)
    assert posted.status == "POSTED"
    with inv.scope():
        q = StockQuant.objects.get(item_id=queso.id, location_id=alm.id, lot_id=lot.id)
        assert (q.on_hand, q.reserved) == (D(0), D(0))

    # 2) race without reservation: 10 kg on hand, a transformation of 7 kg and a POS sale of 7 kg start together
    xw.stock(queso, "10", "15500", lot=lot)
    racing = order("7")
    results, errors = run_threads(
        [lambda: run_in_unit_of_work(lambda u: xf.post(u, racing), ctx), lambda: pos_sale("7")]
    )
    assert len([r for r in results if r is not None]) == 1, (results, errors)
    assert len(errors) == 1 and getattr(errors[0], "code", None) == "stock_insufficient", errors
    with inv.scope():
        q = StockQuant.objects.get(item_id=queso.id, location_id=alm.id, lot_id=lot.id)
        assert (q.on_hand, q.reserved) == (D(3), D(0))  # never negative, never overconsumed
        o = TransformationOrder.objects.get(id=racing.id)
        moves = StockMove.objects.filter(source_id=racing.id)
        if o.status == "POSTED":  # the transformation won: its moves exist, the sale left nothing behind
            assert moves.filter(kind="ISSUE").aggregate(t=Sum("quantity"))["t"] == D(7)
            assert StockQuant.objects.get(item_id=tajado.id).on_hand == D(15)
        else:  # the sale won: the transformation rolled back completely and stays a draft
            assert o.status == "DRAFT" and not moves.exists()
            assert StockQuant.objects.get(item_id=tajado.id).on_hand == D(8)

    # 3) reservation vs sale race: both try to claim the last 3 kg; exactly one gets them
    last = order("3")
    results, errors = run_threads(
        [lambda: run_in_unit_of_work(lambda u: xf.reserve(u, last), ctx), lambda: pos_sale("3")]
    )
    assert len([r for r in results if r is not None]) == 1 and len(errors) == 1, (results, errors)
    assert getattr(errors[0], "code", None) == "stock_insufficient"
    with inv.scope():
        q = StockQuant.objects.get(item_id=queso.id, location_id=alm.id, lot_id=lot.id)
        assert q.on_hand - q.reserved == D(0) and q.on_hand >= 0
