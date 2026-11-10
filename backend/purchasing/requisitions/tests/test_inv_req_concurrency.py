"""T-INV-REQ-05 — two requisitions sourcing the same last units: reservations prevent double allocation.

Barrier threads, one database connection each, real P09 reservations under row locks. The loser of the race
re-reads availability (savepoint) and sends its quantity to procurement instead of failing."""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections

from inventory.models import Reservation
from purchasing.requisitions import service
from purchasing.requisitions.models import InternalRequisition, RequisitionLine
from purchasing.requisitions.tests.kit import P11bWorld

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


def submitted(w: P11bWorld, user: str, wh: Any, item: Any, qty: str) -> InternalRequisition:
    body = {
        "document_date": "2026-11-09",
        "requesting_warehouse_id": str(wh.id),
        "needed_by": "2026-11-12",
        "lines": [{"line_no": 1, "item_id": str(item.id), "quantity": {"value": qty, "uom": "UND"}}],
    }
    req = w.run(lambda u: service.create(u, w.company(), body), user=user)
    done: InternalRequisition = w.run(
        lambda u: service.submit(u, InternalRequisition.objects.get(pk=req.pk)), user=user
    )
    return done


@pytest.mark.django_db(transaction=True)
def test_T_INV_REQ_05_two_requisitions_for_the_last_units_never_double_allocate(w: P11bWorld) -> None:
    tienda, sur, central = w.warehouse("TIENDA"), w.warehouse("SUR"), w.warehouse("CENTRAL")
    item = w.item("ULTIMAS")
    w.receive(item, central, "5")
    reqs = [submitted(w, "sol", tienda, item, "5"), submitted(w, "sol2", sur, item, "5")]

    def target(i: int) -> Any:
        return w.run(lambda u: service.approve(u, InternalRequisition.objects.get(pk=reqs[i].pk), "ok"), user="jefe")

    results, errors = run_threads(2, target)
    assert errors == [], errors
    assert all(r["status"] == "APPLIED" for r in results)
    with w.scope():
        lines = [RequisitionLine.objects.get(requisition_id=r.pk) for r in reqs]
        reserved = sum((x.quantity for x in Reservation.objects.filter(item_id=item.id, state="ACTIVE")), D(0))
    # exactly the 5 units on hand are reserved once; the other requisition goes to procurement
    assert reserved == D(5)
    assert sorted(ln.qty_transfer for ln in lines) == [D(0), D(5)]
    assert sorted(ln.qty_procure for ln in lines) == [D(0), D(5)]
    assert w.available(item, central) == D(0)
    for ln in lines:
        assert ln.qty_transfer + ln.qty_procure == ln.base_qty == D(5)


@pytest.mark.django_db(transaction=True)
def test_T_INV_REQ_05_many_requisitions_split_scarce_stock_without_overallocation(w: P11bWorld) -> None:
    tienda, central = w.warehouse("TIENDA"), w.warehouse("CENTRAL")
    item = w.item("ESCASO")
    w.receive(item, central, "7")
    reqs = [submitted(w, "sol" if i % 2 else "sol2", tienda, item, "3") for i in range(4)]

    def target(i: int) -> Any:
        return w.run(lambda u: service.approve(u, InternalRequisition.objects.get(pk=reqs[i].pk), "ok"), user="jefe")

    _results, errors = run_threads(4, target)
    assert errors == [], errors
    with w.scope():
        lines = [RequisitionLine.objects.get(requisition_id=r.pk) for r in reqs]
    transferred = sum((ln.qty_transfer for ln in lines), D(0))
    assert D(0) < transferred <= D(7)  # never more than the 7 units on hand
    assert sum((ln.qty_procure for ln in lines), D(0)) == D(12) - transferred
    assert w.available(item, central) == D(7) - transferred
