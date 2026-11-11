"""T-P12-01 — seed scenario S4 (shared/11): online checkout, disconnected draft blocked, reconnect atomic commit,
return, cash close with a 500 shortage, FE conversion; ``pos_offline_fiscal_selling`` stays off throughout.

Synthetic data: PAQ-01 at 100,000 + IVA19 (synthetic 19 %), GAS-350 at 2,500 per unit; REAL stock and ledger.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from kernel import flags
from kernel.testing.tenancy import bound
from pos.models import CashSession, Ticket
from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import counts, gl
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def _checkout(api: ApiClient, params: dict, key: str | None = None):  # type: ignore[no-untyped-def,type-arg]
    return api.post("tickets:checkout", {"parameters": params}, key=key or params["client_uuid"])


def test_T_P12_01_s4_full_scenario(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env, opening="200000")
    api.as_("cashier")
    snack, soda = env.items["snack"], env.items["soda"]
    base = {"session_id": str(session.id)}

    # T1 cash 119,000 (net 100,000 + VAT 19,000) and T2 card 59,500 check out online
    t1 = {
        **base,
        "client_uuid": str(uuid.uuid4()),
        "lines": [{"item_id": str(snack.id), "quantity": "1"}],
        "payments": [{"type": "CASH", "amount": "119000"}],
    }
    r = _checkout(api, t1)
    assert r.status_code == 200, r.content
    assert r.json()["data"]["number"] == "SETP1" and r.json()["data"]["total"]["amount"] == "119000.0000"
    t2 = {
        **base,
        "client_uuid": str(uuid.uuid4()),
        "lines": [{"item_id": str(soda.id), "quantity": "20"}],
        "payments": [{"type": "CARD", "amount": "59500", "reference": "DATAFONO-SIM-0001"}],
    }
    r = _checkout(api, t2)
    assert r.status_code == 200, r.content
    t2_id = r.json()["data"]["id"]
    assert r.json()["data"]["number"] == "SETP2"

    # Disconnect: the terminal cannot reach the server. The draft T3 lives only on the client (no request). Any
    # attempt to finalize "offline" is refused and the server state does not change: 0 entries, 0 moves, 0 numbers.
    before = counts(env)
    draft_t3 = {
        **base,
        "client_uuid": str(uuid.uuid4()),
        "lines": [{"item_id": str(soda.id), "quantity": "8", "unit_price": "2500"}],
    }
    r = _checkout(api, {**draft_t3, "payments": [{"type": "CASH", "amount": "23800"}], "offline": True})
    assert r.status_code == 409 and r.json()["code"] == "pos_offline_fiscal_selling_refused"
    assert counts(env) == before
    assert not flags.is_enabled(flags.POS_OFFLINE_FISCAL_SELLING, env.world.tenant_id, env.company)

    # Reconnect: T3 is resubmitted through the normal checkout; same price → no diff → one atomic commit
    r = _checkout(api, {**draft_t3, "payments": [{"type": "CASH", "amount": "23800"}], "expected_total": "23800.00"})
    assert r.status_code == 200, r.content
    t3 = r.json()["data"]
    after = counts(env)
    assert after["posted_tickets"] == before["posted_tickets"] + 1 and after["next_number"] == before["next_number"] + 1
    assert after["entries"] == before["entries"] + 1 and after["moves"] > before["moves"]

    # Return of T3 (cash refund 23,800, online): the cashier drafts it, a supervisor posts it
    r = api.post(
        "returns",
        {"original": {"type": "pos.ticket", "id": t3["id"]}, "refund_tender": "CASH", "reason": "cliente desiste"},
    )
    assert r.status_code == 201, r.content
    ret = r.json()
    assert ret["total"]["amount"] == "23800.0000" and "cost_total" not in ret
    r = api.post(f"returns/{ret['id']}:post")
    assert r.status_code == 403  # cajero_pos denies pos.return.post
    api.as_("supervisor")
    r = api.post(f"returns/{ret['id']}:post")
    assert r.status_code == 200, r.content
    assert r.json()["cost_total"]["amount"] == "8000.0000"  # original cost 8 × 1,000

    # Close: expected cash 200,000 + 119,000 + 23,800 − 23,800 = 319,000; counted 318,500 → shortage 500
    api.as_("cashier")
    r = api.post(f"sessions/{session.id}:close", {"parameters": {"counted": {"CASH": "318500", "CARD": "59500"}}})
    assert r.status_code == 200, r.content
    with bound(env.world.ctx("admin")):
        closed = CashSession.objects.get(id=session.id)
    assert closed.expected_cash == Decimal("319000.0000") and closed.difference == Decimal("-500.0000")
    books = gl(env, session.id)
    assert books == {"530595": Decimal("500.00"), "110510": Decimal("-500.00")}  # Dr CASH_DIFFERENCE / Cr CASH_POS
    assert closed.z_report["tickets"] == 3 and closed.z_report["by_tender"]["CARD"] == "59500.0000"

    # T2 is converted to an FE on buyer request (R-FE-03 buyer data); the posted ticket itself is unchanged
    api.as_("cashier")
    buyer = {
        "id_type": "31",
        "id_number": "900123456",
        "dv": "8",
        "name": "Comprador SINTÉTICO S.A.S.",
        "email": "fe@example.invalid",
    }
    r = api.post(f"tickets/{t2_id}:to-invoice", {"parameters": {"buyer": buyer}})
    assert r.status_code == 200, r.content
    assert "invoice_request" not in r.json()  # restricted-pii: masked for the cashier
    api.as_("supervisor")
    assert api.get(f"tickets/{t2_id}").json()["invoice_request"]["buyer"]["id_number"] == "900123456"
    with bound(env.world.ctx("admin")):
        assert Ticket.objects.get(id=t2_id).fiscal_doc_type == "DEE_POS"
