"""S4 - POS session: online checkout, disconnected draft blocked, reconnect atomic commit, return
[P12, P15, P09, P05] (shared/11 S4, ADR-015).

Real: ledger, sequences, idempotency, outbox, audit, P06 tax engine, ``kernel.flags`` and the fail-closed
``kernel.sequences.allocate_terminal_block``. Doubles until P12/P09 merge: the terminal/checkout
(``refpos``) and the stock valuation. The DIAN-down contingency and the T2 → FE conversion are P15 legs.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, refpos, steps
from qa_harness.andina import item

from kernel.errors import DomainError
from kernel.idempotency import StoredResponse

D = Decimal
DAY = dt.date(2026, 6, 12)
P1, P2, P3 = item("POS-T1"), item("POS-T2"), item("POS-T3")
pytestmark = [pytest.mark.django_db, pytest.mark.req("UR-14")]


@pytest.fixture
def pos(andina):
    a, wh = andina.a, andina.warehouse["BOG-01"]
    for it in (P1, P2, P3):
        steps.define_item(andina, a, it, wh)
        steps.goods_receipt(andina, a, it, wh, D(10), D(8000), dt.date(2026, 6, 1))
    server = refpos.Server({P1: D(100000), P2: D(50000), P3: D(20000)})
    terminal = refpos.Terminal(andina, a, wh)
    server.open_session(terminal, D(200000), DAY)
    return andina, server, terminal


def _online_tickets(server, terminal):
    t1 = terminal.checkout(server, terminal.build("T1", [(P1, D(1), D(100000))], "CASH"), DAY)
    t2 = terminal.checkout(server, terminal.build("T2", [(P2, D(1), D(50000))], "CARD"), DAY)
    return t1, t2


def test_p22b_S04_online_checkout_each_ticket_one_transaction(pos):
    w, server, terminal = pos
    before = w.counts()
    t1, t2 = _online_tickets(server, terminal)
    assert w.lines(t1.ref, "sale") == [("CASH_POS", "Dr", D(119000)), ("REVENUE", "Cr", D(100000)),
                                       ("VAT_PAYABLE", "Cr", D(19000))]  # fmt: skip
    assert w.lines(t2.ref, "sale") == [("CARD_CLEARING", "Dr", D(59500)), ("REVENUE", "Cr", D(50000)),
                                       ("VAT_PAYABLE", "Cr", D(9500))]  # fmt: skip
    after = w.counts()
    assert after["documents"] - before["documents"] == 2 and after["stock_moves"] - before["stock_moves"] == 2
    assert (t1.ref.number, t2.ref.number) == ("POS-1", "POS-2")


def test_p22b_S04_disconnected_draft_is_blocked_and_writes_nothing(pos):
    from kernel import flags, sequences
    from kernel.errors import FeatureDisabled

    w, server, terminal = pos
    _online_tickets(server, terminal)
    before = w.counts()
    terminal.online = False
    t3 = terminal.build("T3", [(P3, D(1), D(20000))], "CASH")
    assert terminal.ui() == {"checkout_enabled": False, "tender_enabled": False, "message": "Sin conexión"}
    assert t3.watermark == "BORRADOR – sin valor fiscal" and not t3.printable
    with pytest.raises(refpos.Offline, match="Sin conexión"):
        terminal.checkout(server, t3, DAY)
    after = w.counts()
    # 0 journal entries, 0 stock moves, 0 numbers consumed, 0 fiscal documents (nothing reached the server).
    assert after == before
    assert terminal.drafts["T3"] is t3 and t3.posted is None
    assert not flags.is_enabled(flags.POS_OFFLINE_FISCAL_SELLING, w.ctx.tenant_id, w.a)
    with pytest.raises(FeatureDisabled):
        w.run(lambda u: sequences.allocate_terminal_block(w.a, "POS", 50))


def test_p22b_S04_reconnect_commits_atomically_and_failure_keeps_the_draft(pos):
    w, server, terminal = pos
    _online_tickets(server, terminal)
    terminal.online = False
    t3 = terminal.build("T3", [(P3, D(1), D(20000))], "CASH")
    terminal.online = True
    before = w.counts()

    def boom(uow):
        raise RuntimeError("inyección S4: falla en el paso de inventario")

    with pytest.raises(RuntimeError, match="inyección S4"):
        terminal.checkout(server, t3, DAY, hooks={"after_stock": boom})
    assert w.counts() == before and t3.posted is None and t3.watermark  # nothing persisted, draft kept
    posted = terminal.checkout(server, t3, DAY)
    replay = terminal.checkout(server, t3, DAY)  # same Idempotency-Key → exactly one ticket
    assert isinstance(replay, StoredResponse) and replay.body["number"] == posted.ref.number == "POS-3"
    after = w.counts()
    assert after["documents"] - before["documents"] == 1 and after["stock_moves"] - before["stock_moves"] == 1
    assert w.lines(posted.ref, "sale") == [("CASH_POS", "Dr", D(23800)), ("REVENUE", "Cr", D(20000)),
                                           ("VAT_PAYABLE", "Cr", D(3800))]  # fmt: skip


def test_p22b_S04_price_change_needs_confirmation_before_commit(pos):
    w, server, terminal = pos
    terminal.online = False
    t3 = terminal.build("T3", [(P3, D(1), D(18000))], "CASH")  # stale local price
    terminal.online = True
    before = w.counts()
    diffs = terminal.checkout(server, t3, DAY)
    assert diffs == [refpos.PriceDiff(str(P3), D(18000), D(20000))] and w.counts() == before
    posted = terminal.checkout(server, t3, DAY, confirm_diff=True)
    assert w.lines(posted.ref, "sale")[0] == ("CASH_POS", "Dr", D(23800))  # server price wins


def test_p22b_S04_return_and_session_close_cash_difference(pos):
    w, server, terminal = pos
    t1, t2 = _online_tickets(server, terminal)
    t3 = terminal.checkout(server, terminal.build("T3", [(P3, D(1), D(20000))], "CASH"), DAY)
    refund = server.refund(terminal, t3, D(20000), DAY)
    assert w.lines(refund.ref, "refund") == [("REVENUE", "Dr", D(20000)), ("VAT_PAYABLE", "Dr", D(3800)),
                                             ("CASH_POS", "Cr", D(23800))]  # fmt: skip
    assert w.lines(refund.ref, "cogs") == [("INVENTORY", "Dr", D(8000)), ("COGS", "Cr", D(8000))]  # original cost
    expected, close = server.close_session(terminal, D(318500), DAY)
    assert expected == D(319000)
    assert w.lines(close.ref) == [("CASH_DIFFERENCE", "Dr", D(500)), ("CASH_POS", "Cr", D(500))]
    assert w.balance("CASH_POS", w.a) == D(318500)
    with w.scope():
        assert w.services["stock"].state(w.ctx, w.a, P3, w.warehouse["BOG-01"])["qty"] == D(10)


def test_p22b_S04_insufficient_stock_never_reaches_the_ledger(pos):
    w, server, terminal = pos
    before = w.counts()
    with pytest.raises(DomainError) as exc:
        terminal.checkout(server, terminal.build("BIG", [(P1, D(11), D(100000))], "CASH"), DAY)
    assert exc.value.code == "insufficient_stock" and w.counts() == before


def test_p22b_S04_module_leg_dian_down_contingency_and_fe_conversion(andina):
    """DIAN-down: checkout commits in-line, the DEE POS goes to P15's contingency queue (48 h timer);
    T2 converted to an FE on buyer request (buyer data per R-FE-03)."""
    defects.require("PEND-P12", "PEND-P15")
