"""Checkout rules: atomicity (T-P12-02), idempotency (T-P12-03), reconnect diff (T-P12-05), DIAN-down (T-P12-07)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from pos.models import Ticket
from pos.services import checkout
from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import checkout_params, counts, stock
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_T_P12_02_checkout_atomicity_failure_at_stock_step_persists_nothing(failing_env: PosEnv) -> None:
    env = failing_env
    session = open_counter(env)
    soda = env.items["soda"]
    params = checkout_params(
        session, [{"item_id": str(soda.id), "quantity": "2"}], [{"type": "CASH", "amount": "5950"}]
    )
    before, stock_before = counts(env), stock(env, soda)
    env.world.fakes.inventory.armed = True
    with pytest.raises(RuntimeError):
        env.run("cashier", lambda u: checkout.checkout(u, env.company, params))
    assert counts(env) == before and stock(env, soda) == stock_before  # no number, move, entry, event, ticket
    env.world.fakes.inventory.armed = False
    first = env.run("cashier", lambda u: checkout.checkout(u, env.company, params))
    again = env.run("cashier", lambda u: checkout.checkout(u, env.company, params))  # retry, same client UUID
    assert first.id == again.id and first.number == "SETP1"
    assert counts(env)["posted_tickets"] == 1


def test_T_P12_03_idempotent_checkout_same_key_twice_one_ticket(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    api.as_("cashier")
    params = checkout_params(session, [{"item_id": str(env.items["soda"].id)}], [{"type": "CASH", "amount": "5000"}])
    r1 = api.post("tickets:checkout", {"parameters": params}, key=params["client_uuid"])
    r2 = api.post("tickets:checkout", {"parameters": params}, key=params["client_uuid"])
    assert r1.status_code == r2.status_code == 200, r1.content
    assert r1["Idempotent-Replayed"] == "false" and r2["Idempotent-Replayed"] == "true"
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]
    assert r1.json()["data"]["change_due"]["amount"] == "2025.0000"  # 2,975 due, 5,000 tendered
    with bound(env.world.ctx("admin")):
        assert Ticket.objects.filter(company_id=env.company, status="POSTED").count() == 1
    # a new key for the same client UUID still returns the same ticket (the client UUID is the idempotency identity)
    r3 = api.post("tickets:checkout", {"parameters": params}, key=str(uuid.uuid4()))
    assert r3.status_code == 200 and r3.json()["data"]["id"] == r1.json()["data"]["id"]


def test_T_P12_05_reconnect_commit_surfaces_server_price_change(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    soda = env.items["soda"]
    # parked draft built while prices were 2,500
    api.as_("cashier")
    client = str(uuid.uuid4())
    r = api.post(
        "tickets",
        {
            "session_id": str(session.id),
            "client_uuid": client,
            "document_date": "2026-11-09",
            "lines": [{"item_id": str(soda.id), "quantity": "2", "unit_price": "2500"}],
        },
    )
    assert r.status_code == 201, r.content
    parked = r.json()
    assert (
        parked["status"] == "DRAFT" and parked["number"] == "" and parked["watermark"] == "BORRADOR – sin valor fiscal"
    )
    # the server price changes before the reconnect
    env.world.base_price(soda.id, "2600", valid_from="2026-11-09")
    before = counts(env)
    params = {
        "client_uuid": client,
        "ticket_id": parked["id"],
        "session_id": str(session.id),
        "lines": parked["draft"]["lines"],
        "payments": [{"type": "CASH", "amount": "5950"}],
        "expected_total": "5950.00",
    }
    r = api.post("tickets:checkout", {"parameters": params}, key=client)
    assert r.status_code == 409 and r.json()["code"] == "pos_price_changed", r.content
    server = r.json()["errors"]["server"]
    assert server["total"] == "6188.00" and server["diff"] == [
        {"line_no": 1, "item_id": str(soda.id), "draft_unit_price": "2500", "server_unit_price": "2600.00"}
    ]
    assert counts(env) == before  # nothing committed before the cashier confirms
    # the cashier confirms the new total → atomic commit of the parked ticket
    params.update(expected_total=server["total"], payments=[{"type": "CASH", "amount": "6188"}])
    r = api.post("tickets:checkout", {"parameters": params}, key=client)
    assert r.status_code == 200, r.content
    assert r.json()["data"]["id"] == parked["id"] and r.json()["data"]["status"] == "POSTED"
    assert r.json()["data"]["number"] == "SETP1"


def test_T_P12_07_dian_down_contingency_checkout_still_commits_inline(pos_env: PosEnv) -> None:
    """DIAN/PT outage with our server up: the checkout never calls DIAN; the DEE hand-off is the outbox event that
    P15 consumes (its contingency flow owns the 48 h timer, CCR P12-1 §P15)."""
    env = pos_env
    session = open_counter(env)
    params = checkout_params(session, [{"item_id": str(env.items["snack"].id)}], [{"type": "CASH", "amount": "119000"}])
    ticket = env.run("cashier", lambda u: checkout.checkout(u, env.company, params))
    assert ticket.status == "POSTED" and ticket.fiscal_status == "PENDING" and ticket.journal_entry_id is not None
    with bound(env.world.ctx("admin")):
        ev = OutboxMessage.objects.get(event_type="pos.ticket.checked_out.v1")
    payload = ev.envelope["payload"]
    assert payload == {
        "ticket_id": str(ticket.id),
        "number": "SETP1",
        "total": {"amount": "119000.0000", "currency": "COP"},
        "journal_entry_id": str(ticket.journal_entry_id),
    }


def test_checkout_validation_tenders_session_and_numbering(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    api.as_("cashier")
    soda = str(env.items["soda"].id)

    def go(payments: list, **extra: object):  # type: ignore[no-untyped-def,type-arg]
        p = checkout_params(session, [{"item_id": soda}], payments, **extra)
        return api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"])

    assert go([{"type": "CASH", "amount": "1000"}]).json()["code"] == "pos_tender_mismatch"
    assert go([{"type": "CARD", "amount": "2975"}]).json()["code"] == "validation_failed"  # card needs a reference
    assert go([{"type": "CARD", "amount": "3000", "reference": "X"}]).json()["code"] == "pos_tender_mismatch"
    assert go([{"type": "BITCOIN", "amount": "2975"}]).json()["code"] == "pos_tender_not_allowed"
    r = go([{"type": "CARD", "amount": "1000", "reference": "D-1"}, {"type": "CASH", "amount": "5000"}])
    assert r.status_code == 200, r.content  # mixed tenders: card 1,000 + cash 1,975 (change 3,025)
    assert "payments" not in r.json()["data"]  # contract: payments are confidential (cashier = internal)
    api.as_("supervisor")
    detail = api.get(f"tickets/{r.json()['data']['id']}").json()
    assert [p["amount"]["amount"] for p in detail["payments"]["items"]] == ["1000.0000", "1975.0000"]
    api.as_("cashier")
    # a manual discount above the cashier's limit (D-024 default 0 %) is refused at the counter
    p = checkout_params(session, [{"item_id": soda, "discount_pct": "10"}], [{"type": "CASH", "amount": "2678"}])
    r = api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"])
    assert r.status_code == 422 and r.json()["code"] == "pricing_override_requires_approval"
    # another cashier cannot sell on this session (row policy → 404)
    api.as_("cashier2")
    p = checkout_params(session, [{"item_id": soda}], [{"type": "CASH", "amount": "2975"}])
    assert api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"]).status_code == 404
    # without an active DEE POS range the checkout fails closed (numbering_range_missing)
    with bound(env.world.ctx("admin")):
        type(env.pos_range).objects.filter(id=env.pos_range.id).update(active=False)
    api.as_("cashier")
    r = go([{"type": "CASH", "amount": "2975"}])
    assert r.status_code == 422 and r.json()["code"] == "numbering_range_missing"
    assert Decimal(1) == Decimal("1")


def test_checkout_respects_period_and_session_state(pos_env: PosEnv) -> None:
    env = pos_env
    session = open_counter(env)
    from pos.services import sessions

    env.run("cashier", lambda u: sessions.close_session(u, session, "", {"counted_cash": "200000"}))
    params = checkout_params(session, [{"item_id": str(env.items["soda"].id)}], [{"type": "CASH", "amount": "2975"}])
    with pytest.raises(Exception) as exc:
        run_in_unit_of_work(lambda u: checkout.checkout(u, env.company, params), env.world.ctx("cashier"))
    assert getattr(exc.value, "code", "") == "pos_session_not_open"
