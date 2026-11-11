"""Returns at the original cost (T-P12-08), cash difference posting (T-P12-09), cash movements and the Z report."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kernel.testing.tenancy import bound
from pos.models import CashSession
from pos.services import checkout, returns, sessions
from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import checkout_params, gl, stock
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_T_P12_08_return_references_the_original_ticket_at_the_original_cost(pos_env: PosEnv) -> None:
    env = pos_env
    session = open_counter(env)
    soda, box = env.items["soda"], env.presentations["box"]
    params = checkout_params(
        session,
        [{"barcode": "2001234567893"}, {"item_id": str(soda.id), "quantity": "3"}],
        [{"type": "CASH", "amount": "44625"}],
    )
    ticket = env.run("cashier", lambda u: checkout.checkout(u, env.company, params))
    assert stock(env, soda, box) == (Decimal(15), Decimal(1))
    # the average cost changes after the sale (new receipt at 1,600): the return must use the ORIGINAL 1,000
    from inventory.stock.service import SERVICE
    from kernel import ids
    from kernel.interfaces.common import DocumentRef
    from kernel.interfaces.stock import StockLine

    env.run(
        "admin",
        lambda u: SERVICE.receive(
            u,
            env.company,
            DocumentRef("purchasing.receipt", ids.new_id(), "REC-2"),
            [StockLine("1", soda.id, env.warehouse.id, Decimal(15), unit_cost=Decimal("1600"))],
            ticket.document_date,
        ),
    )
    data = {"original": str(ticket.id), "lines": [{"line_no": 1, "quantity": "1"}, {"line_no": 2, "quantity": "1"}]}
    ret = env.run("cashier", lambda u: returns.create_return(u, env.company, data))
    assert ret.total == Decimal("35700.0000") + Decimal("2975.0000")
    with pytest.raises(Exception) as denied:
        env.run("cashier", lambda u: returns.post_return(u, ret))
    assert getattr(denied.value, "code", "") == "policy_denied"
    posted = env.run("supervisor", lambda u: returns.post_return(u, ret))
    assert posted.status == "POSTED" and posted.cost_total == Decimal("13000.0000")  # 13 units × original 1,000
    assert stock(env, soda, box) == (Decimal(15 + 15 + 13), Decimal(2))  # packaged box restored
    books = gl(env, posted.id)
    assert books["143505"] == Decimal("13000.00") and books["613595"] == Decimal("-13000.00")  # Dr INVENTORY / Cr COGS
    assert books["413595"] == Decimal("32500.00") and books["240805"] == Decimal("6175.00")  # Dr REVENUE / Dr VAT
    assert books["110510"] == Decimal("-38675.00")  # Cr CASH_POS (refund)
    # quantities beyond what was sold (sold − returned) are refused
    with pytest.raises(Exception) as exc:
        env.run(
            "cashier",
            lambda u: returns.create_return(
                u, env.company, {"original": str(ticket.id), "lines": [{"line_no": 2, "quantity": "3"}]}
            ),
        )
    assert getattr(exc.value, "code", "") == "pos_return_exceeds_sold"
    rest = env.run(
        "cashier",
        lambda u: returns.create_return(
            u, env.company, {"original": str(ticket.id), "lines": [{"line_no": 2, "quantity": "2"}]}
        ),
    )
    assert rest.total == Decimal("5950.0000")  # remaining 2 units: 8,925 − 2,975 (no rounding residue)


def test_T_P12_09_cash_difference_posting_overage_and_deposit(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env, opening="100000")
    api.as_("cashier")
    p = checkout_params(session, [{"item_id": str(env.items["snack"].id)}], [{"type": "CASH", "amount": "119000"}])
    assert api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"]).status_code == 200
    # pay-out with a reason and a pay-in, both posted in-line (Dr/Cr CASH_POS vs CASH_TRANSFER)
    r = api.post(
        "cash-movements",
        {
            "session_id": str(session.id),
            "movement_type": "OUT",
            "amount": {"amount": "20000", "currency": "COP"},
            "reason": "Compra de bolsas",
        },
    )
    assert r.status_code == 201, r.content
    out_id = r.json()["id"]
    assert "amount" not in r.json()  # confidential for the cashier
    assert (
        api.post(
            "cash-movements",
            {"session_id": str(session.id), "movement_type": "IN", "amount": "5000", "reason": "Base adicional"},
        ).status_code
        == 201
    )
    assert (
        api.post(
            "cash-movements", {"session_id": str(session.id), "movement_type": "OUT", "amount": "999999", "reason": "x"}
        ).json()["code"]
        == "validation_failed"
    )
    assert gl(env, __import__("uuid").UUID(out_id)) == {"110505": Decimal("20000.00"), "110510": Decimal("-20000.00")}
    # blind count: the open session does not show the expected cash
    detail = api.get(f"sessions/{session.id}").json()
    assert "expected_cash" not in detail and "opening_cash" not in detail
    # expected 100,000 + 119,000 − 20,000 + 5,000 = 204,000; counted by denomination 204,300 → overage 300
    den = {"100000": 1, "50000": 2, "2000": 2, "200": 1, "100": 1}
    r = api.post(f"sessions/{session.id}:close", {"parameters": {"denominations": den, "deposit_amount": "150000"}})
    assert r.status_code == 200, r.content
    with bound(env.world.ctx("admin")):
        s = CashSession.objects.get(id=session.id)
    assert (s.expected_cash, s.counted_cash, s.difference) == (
        Decimal("204000.0000"),
        Decimal("204300.0000"),
        Decimal("300.0000"),
    )
    books = gl(env, session.id)
    # over: Dr CASH_POS 300 / Cr CASH_DIFFERENCE 300; deposit: Dr BANK_TRANSIT 150,000 / Cr CASH_POS 150,000
    assert books == {"110510": Decimal("-149700.00"), "530595": Decimal("-300.00"), "111020": Decimal("150000.00")}
    # the Z report (own session) and the closed-session event
    z = api.get("reports/z", session_id=str(session.id)).json()
    assert z["report"] == "pos.z" and z["rows"] == [{"tender": "CASH", "amount": "119000.0000"}]
    assert z["totals"]["tickets"] == 1 and z["totals"]["cash_out"] == "20000.0000"
    from kernel.models import OutboxMessage

    with bound(env.world.ctx("admin")):
        ev = OutboxMessage.objects.get(event_type="pos.session.closed.v1")
    assert ev.envelope["payload"] == {
        "session_id": str(session.id),
        "difference": {"amount": "300.0000", "currency": "COP"},
    }
    # a closed session accepts nothing more
    r = api.post("cash-movements", {"session_id": str(session.id), "movement_type": "IN", "amount": "1", "reason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "pos_session_not_open"


def test_session_lifecycle_one_open_session_per_terminal(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    api.as_("cashier2")
    r = api.post("sessions:open", {"parameters": {"terminal_id": str(env.terminal.id), "opening_cash": "0"}})
    assert r.status_code == 409 and r.json()["code"] == "pos_session_already_open"
    env.run("cashier", lambda u: sessions.close_session(u, session, "", {"counted_cash": "200000"}))
    r = api.post("sessions:open", {"parameters": {"terminal_id": str(env.terminal.id), "opening_cash": "50000"}})
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "OPEN" and r.json()["number"] == "TUR-000002"
    # terminal device registration stores only the fingerprint of the device key
    api.as_("pos_admin")
    r = api.post(f"terminals/{env.terminal.id}:register", {"parameters": {"device_key": "dispositivo-sintetico-0001"}})
    assert r.status_code == 200 and len(r.json()["device_fingerprint"]) == 64
    assert "dispositivo-sintetico-0001" not in r.content.decode()
