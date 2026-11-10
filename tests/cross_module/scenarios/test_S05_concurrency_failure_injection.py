"""S5 - Concurrency and failure injection [P09, P10, P22] (shared/11 S5) + P22 §3 concurrency suite.

Threaded tests commit for real (``transactional_db``): two barrier-synchronized invoices race for the
same stock, concurrent retries share one Idempotency-Key, parallel postings keep numbering gap-free and
the database reports no deadlock. Real: ledger, sequences, idempotency, outbox, audit, PostgreSQL row
locks. Double: stock valuation (``refstock``, same locking contract as P09).
"""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import pytest
from qa_harness import defects, refdocs, steps
from qa_harness.andina import item, party

from kernel.errors import Conflict, DomainError
from kernel.idempotency import StoredResponse

D = Decimal
DAY = dt.date(2026, 4, 6)
CUSTOMER = party("Cliente concurrente")


def _deadlocks() -> int:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
        return int(cur.fetchone()[0])


def _parallel(n, target):
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def worker(i):
        from django.db import connections

        try:
            barrier.wait(timeout=30)
            results[i] = target(i)
        except Exception as exc:  # collected and asserted by the caller
            results[i] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "worker thread hung (lock wait)"
    return results


def _ten_on_hand(world, code="S5-ITEM"):
    a, wh, it = world.a, world.warehouse["BOG-01"], item(code)
    steps.define_item(world, a, it, wh)
    steps.goods_receipt(world, a, it, wh, D(10), D(5000), DAY)
    return a, wh, it


@pytest.mark.req("UR-14")
def test_p22b_S05_two_parallel_invoices_exactly_one_posts(andina_tx):
    w = andina_tx
    a, wh, it = _ten_on_hand(w)
    before, deadlocks = w.counts(), _deadlocks()
    results = _parallel(2, lambda i: steps.sales_invoice(w, a, CUSTOMER, it, wh, D(8), D(9000), DAY,
                                                          idempotency_key=f"s5-race-{i:04d}"))  # fmt: skip
    ok = [r for r in results if isinstance(r, refdocs.Posted)]
    failed = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1 and len(failed) == 1, results
    assert isinstance(failed[0], Conflict) and failed[0].code == "insufficient_stock" and failed[0].status == 409
    after = w.counts()
    # Exactly one document's effects: 1 doc, 1 stock move, 2 entries × 2 books, nothing from the failed one.
    assert after["documents"] - before["documents"] == 1
    assert after["stock_moves"] - before["stock_moves"] == 1
    assert after["journal_entries"] - before["journal_entries"] == 4
    assert refdocs.numbers(w.ctx, a, "sales.invoice") == ["FV-1"]  # the failed one consumed no number
    with w.scope():
        st = w.services["stock"].state(w.ctx, a, it, wh)
    assert st["qty"] == D(2) and st["qty"] == st["moves_qty"]  # invariant 7
    assert w.balance("INVENTORY", a) == st["value"]  # invariant 3
    assert _deadlocks() == deadlocks


@pytest.mark.req("UR-14")
def test_p22b_S05_concurrent_retries_with_one_idempotency_key_post_once(andina_tx):
    w = andina_tx
    a, wh, it = _ten_on_hand(w, "S5-IDEM")
    before = w.counts()
    results = _parallel(4, lambda i: steps.sales_invoice(w, a, CUSTOMER, it, wh, D(3), D(9000), DAY,
                                                          idempotency_key="s5-same-key-0001"))  # fmt: skip
    assert not [r for r in results if isinstance(r, Exception)], results
    bodies = [r.body if isinstance(r, StoredResponse) else {"number": r.ref.number} for r in results]
    assert {b["number"] for b in bodies} == {"FV-1"}  # same response for every retry
    assert sum(isinstance(r, refdocs.Posted) for r in results) == 1  # executed once
    after = w.counts()
    assert after["documents"] - before["documents"] == 1 and after["stock_moves"] - before["stock_moves"] == 1


def test_p22b_S05_parallel_postings_numbering_gap_free_and_no_deadlock(andina_tx):
    """16 documents on two items, lines in opposite orders (sorted lock acquisition, shared/07 §7)."""
    w = andina_tx
    a, wh = w.a, w.warehouse["BOG-01"]
    x, y = item("S5-X"), item("S5-Y")
    for it in (x, y):
        steps.define_item(w, a, it, wh)
        steps.goods_receipt(w, a, it, wh, D(100), D(1000), DAY)
    deadlocks = _deadlocks()
    stock = w.services["stock"]

    def two_lines(i):
        order = [x, y] if i % 2 else [y, x]
        lines = [steps.stock_line(str(k), it, wh, D(1)) for k, it in enumerate(order)]

        def fn(u):
            return refdocs.post_document(
                u, doc_type="sales.invoice", company=a, day=DAY, total=D(2000),
                stock=[refdocs.StockOp("issue", lines)], stock_service=stock,
                entries=[refdocs.Entry("cogs", [("COGS", "DEBIT", D(2000)), ("INVENTORY", "CREDIT", D(2000))])],
            )  # fmt: skip

        return w.run(fn, idempotency_key=f"s5-par-{i:04d}", request={"i": i})

    results = _parallel(16, two_lines)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert refdocs.numbers(w.ctx, a, "sales.invoice") == [f"FV-{n}" for n in range(1, 17)]  # invariant 9
    assert _deadlocks() == deadlocks
    with w.scope():
        assert stock.state(w.ctx, a, x, wh)["qty"] == D(84) and stock.state(w.ctx, a, y, wh)["qty"] == D(84)


@pytest.mark.django_db
def test_p22b_S05_failure_after_stock_move_before_journal_rolls_back_everything(andina):
    a, wh, it = _ten_on_hand(andina, "S5-FAIL")
    before = andina.counts()

    class Boom(RuntimeError):
        pass

    def boom(uow):
        assert andina.services["stock"].move_count(andina.ctx, _last_ref(uow)) == 1  # the move IS written
        raise Boom("inyección S5: después del movimiento de inventario, antes del asiento")

    with pytest.raises(Boom):
        steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(8), D(9000), DAY, hooks={"after_stock": boom},
                            idempotency_key="s5-boom-0001")  # fmt: skip
    assert andina.counts() == before
    with andina.scope():
        assert andina.services["stock"].state(andina.ctx, a, it, wh)["qty"] == D(10)
    # The key was not consumed by the failed attempt: the retry executes once and posts.
    posted = steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(8), D(9000), DAY, idempotency_key="s5-boom-0001")
    replay = steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(8), D(9000), DAY, idempotency_key="s5-boom-0001")
    assert isinstance(posted, refdocs.Posted) and isinstance(replay, StoredResponse)
    assert replay.body["number"] == posted.ref.number == "FV-1"
    assert andina.counts()["documents"] == before["documents"] + 1


def _last_ref(uow):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT doc_type, id FROM qa_ref_document ORDER BY number DESC LIMIT 1")
        doc_type, doc_id = cur.fetchone()
    from kernel.interfaces.common import DocumentRef

    return DocumentRef(doc_type, doc_id)


@pytest.mark.django_db
def test_p22b_S05_same_key_different_request_is_refused(andina):
    a, wh, it = _ten_on_hand(andina, "S5-KEY")
    steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(1), D(9000), DAY, idempotency_key="s5-reuse-0001")
    with pytest.raises(DomainError) as exc:
        steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(2), D(9000), DAY, idempotency_key="s5-reuse-0001")
    assert exc.value.status == 422


@pytest.mark.django_db
def test_p22b_S05_module_leg_api_returns_409_insufficient_stock(andina):
    """S5 through the P10 API: ``409 insufficient_stock`` problem+json for the losing invoice."""
    defects.require("PEND-P10", "PEND-P09")
