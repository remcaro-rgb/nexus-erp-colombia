"""F-PORTAL-CUS: T-PORTAL-CUS-02 (payment intent: simulated success never posts; RC draft for treasury) and
T-PORTAL-CUS-03 (quote request → internal quote → acceptance → sales order). Real ledger, stock, policy engine."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kernel.models import OutboxMessage
from ledger.models import JournalEntry
from sales.models import ArOpenItem, Party, Quote, Receipt, SalesOrder
from sales.portal.models import CustomerRequest
from sales.portal.tests.kit import C, P10bWorld, S, ok

pytestmark = pytest.mark.django_db


def _counts(w: P10bWorld) -> tuple[int, int]:
    with w.scope():
        return (
            JournalEntry.objects.filter(tenant_id=w.world.tenant_id).count(),
            OutboxMessage.objects.filter(tenant_id=w.world.tenant_id, event_type="sales.receipt.posted.v1").count(),
        )


def test_T_PORTAL_CUS_02_simulated_payment_never_posts_and_leaves_rc_draft_for_treasury(
    w: P10bWorld, fakes: dict[str, Any]
) -> None:
    w.item("A", "1000")
    a = w.customer("CA", "900100001")
    b = w.customer("CB", "900100002")
    inv = w.sell(a, [("A", "2")])  # 2380
    inv_b = w.sell(b, [("A", "1")])
    api_a = w.onboard(a, "cli-a", fakes["notifications"])
    api_b = w.onboard(b, "cli-b", fakes["notifications"])
    open_items = ok(api_a.get(f"{C}/open-items"))["items"]
    assert [o["document"]["id"] for o in open_items] == [inv["id"]]
    assert open_items[0]["balance"] == {"amount": "2380.0000", "currency": "COP"}

    def intent(api: Any, docs: list[str], amount: Any, **extra: str) -> Any:
        return api.post(f"{C}/payment-intents", {"documents": docs, "amount": amount}, **extra)

    # amounts are validated against the open balance of the customer's own invoices
    for bad in (
        {"amount": "2380.01", "currency": "COP"},
        {"amount": "0", "currency": "COP"},
        {"amount": "-5", "currency": "COP"},
        {"amount": 100.5, "currency": "COP"},
        {"amount": "100.123", "currency": "COP"},
        {"amount": "100", "currency": "USD"},
        "100",
    ):
        r = intent(api_a, [inv["id"]], bad)
        assert r.status_code == 422 and r.json()["code"] == "payment_intent_amount_invalid", (bad, r.content)
    r = intent(api_a, [inv_b["id"]], {"amount": "10", "currency": "COP"})  # B's invoice: no oracle
    assert r.status_code == 404 and r.json()["code"] == "portal_document_not_owned"
    r = api_a.post(
        f"{C}/payment-intents",
        {"documents": [inv["id"]], "amount": {"amount": "1", "currency": "COP"}, "party_id": str(b.id)},
    )
    assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation"

    before = _counts(w)
    key = "pi-create-1"
    created = intent(api_a, [inv["id"]], {"amount": "2380.00", "currency": "COP"}, HTTP_IDEMPOTENCY_KEY=key)
    assert created.status_code == 201, created.content
    pi = created.json()
    assert pi["intent_status"] == "CREATED" and "SIMULADO" in pi["provider"] and pi["documents"] == [inv["id"]]
    replay = intent(api_a, [inv["id"]], {"amount": "2380.00", "currency": "COP"}, HTTP_IDEMPOTENCY_KEY=key)
    assert replay.status_code == 201 and replay.json()["id"] == pi["id"]  # idempotent: one intent
    assert len(ok(api_a.get(f"{C}/payment-intents"))["items"]) == 1
    assert api_b.get(f"{C}/payment-intents/{pi['id']}").status_code == 404
    assert (
        api_b.post(f"{C}/payment-intents/{pi['id']}:simulate", {"parameters": {"outcome": "success"}}).status_code
        == 404
    )

    # simulated provider success: NOTHING is posted, the AR item is untouched, an RC DRAFT waits for treasury
    done = ok(api_a.post(f"{C}/payment-intents/{pi['id']}:simulate", {"parameters": {"outcome": "success"}}))
    assert done["intent_status"] == "SUCCEEDED_SIMULATED"
    assert _counts(w) == before
    with w.scope():
        item = ArOpenItem.objects.get(document_id=inv["id"])
        assert item.balance == Decimal("2380.0000") and item.status == ArOpenItem.Status.OPEN
        drafts = list(Receipt.objects.filter(party_id=a.id))
    assert len(drafts) == 1 and drafts[0].status == Receipt.Status.DRAFT and not drafts[0].posted
    rc = drafts[0]
    assert rc.amount == Decimal("2380.0000") and rc.journal_entry_id is None
    assert rc.applications == [{"open_item_id": str(item.id), "amount": "2380.0000"}]
    assert ok(api_a.get(f"{C}/receipts"))["items"] == []  # the customer sees receipts only once posted
    r = api_a.post(f"{C}/payment-intents/{pi['id']}:simulate", {"parameters": {"outcome": "success"}})
    assert r.status_code == 409  # a processed intent cannot be simulated again (no double RC)
    # the portal user can never post: internal endpoints refuse portal tokens
    assert api_a.post(f"{S}/receipts/{rc.id}:post").status_code == 401

    # treasury sees the intent and its RC draft, and posts it manually (its own permission)
    tes = w.api("tesorero")
    listed = ok(tes.get(f"{S}/payment-intents"))["items"]
    assert [(x["id"], x["receipt_id"]) for x in listed] == [(pi["id"], str(rc.id))]
    assert ok(tes.get(f"{S}/receipts/{rc.id}"))["status"] == "DRAFT"
    assert w.api("vendedor").get(f"{S}/payment-intents").status_code == 200  # sales.*.* includes the read
    posted = ok(tes.post(f"{S}/receipts/{rc.id}:post"))
    assert posted["status"] == "POSTED"
    jes, events = _counts(w)
    assert jes > before[0] and events == before[1] + 1  # only treasury's manual post reaches the ledger
    with w.scope():
        assert ArOpenItem.objects.get(document_id=inv["id"]).status == ArOpenItem.Status.PAID
    assert ok(api_a.get(f"{C}/payment-intents/{pi['id']}"))["intent_status"] == "CONFIRMED_BY_TREASURY"
    assert [x["id"] for x in ok(api_a.get(f"{C}/receipts"))["items"]] == [str(rc.id)]
    assert ok(api_a.get(f"{C}/open-items"))["items"] == []

    # a paid invoice cannot be paid again; a simulated failure leaves no receipt
    r = intent(api_a, [inv["id"]], {"amount": "1", "currency": "COP"})
    assert r.status_code == 404
    inv2 = w.sell(a, [("A", "1")])
    pi2 = ok(intent(api_a, [inv2["id"]], {"amount": "500", "currency": "COP"}), 201)
    failed = ok(api_a.post(f"{C}/payment-intents/{pi2['id']}:simulate", {"parameters": {"outcome": "failure"}}))
    assert failed["intent_status"] == "FAILED"
    with w.scope():
        assert Receipt.objects.filter(party_id=a.id).count() == 1
    actions = {e.action for _rid, _t, e, _h in fakes["audit"].records}
    assert {"portal.customer.payment_intent_created", "portal.customer.payment_intent_simulated"} <= actions


def test_T_PORTAL_CUS_03_quote_request_to_internal_quote_to_acceptance_to_sales_order(
    w: P10bWorld, fakes: dict[str, Any]
) -> None:
    item = w.item("A", "1000")
    a = w.customer("CA", "900100001")
    b = w.customer("CB", "900100002")
    api_a = w.onboard(a, "cli-a", fakes["notifications"])
    api_b = w.onboard(b, "cli-b", fakes["notifications"])
    seller = w.api("vendedor")

    # 1. the customer asks for a quote (items + quantities; no prices from the portal)
    body = {
        "request_type": "QUOTE",
        "message": "Necesitamos 5 unidades",
        "items": [{"item_id": str(item.id), "quantity": "5"}],
    }
    req = ok(api_a.post(f"{C}/requests", body), 201)
    assert req["request_status"] == "SUBMITTED" and req["number"].startswith("SOLC-")
    for bad in (
        {**body, "items": []},
        {**body, "items": [{"item_id": str(item.id), "quantity": "0"}]},
        {**body, "items": [{"item_id": str(item.id), "quantity": "1", "unit_price": "1"}]},
        {"request_type": "OTRO", "message": "x"},
    ):
        assert api_a.post(f"{C}/requests", bad).status_code in (400, 422), bad
    assert ok(api_b.get(f"{C}/requests"))["items"] == []
    assert api_b.get(f"{C}/requests/{req['id']}").status_code == 404

    # 2. the internal team turns it into a quote (D-024 prices of the P10a engine) and sends it
    listed = ok(seller.get(f"{S}/portal-requests", request_status="SUBMITTED"))["items"]
    assert [x["id"] for x in listed] == [req["id"]] and listed[0]["party"]["id"] == str(a.id)
    assert w.api("tesorero").get(f"{S}/portal-requests").status_code == 403  # deny by default
    quoted = ok(
        seller.post(
            f"{S}/portal-requests/{req['id']}:quote", {"parameters": {"send": True, "valid_until": "2026-12-09"}}
        )
    )
    quote_id = quoted["resource"]["id"]
    assert quoted["status"] == "QUOTED" and quoted["data"]["quote_status"] == "SENT"
    r = seller.post(f"{S}/portal-requests/{req['id']}:quote", {"parameters": {}})
    assert r.status_code == 409  # quoted once
    mine = ok(api_a.get(f"{C}/requests/{req['id']}"))
    assert mine["request_status"] == "QUOTED" and mine["quote_id"] == quote_id
    q = ok(api_a.get(f"{C}/quotes/{quote_id}"))
    assert q["status"] == "SUBMITTED" and q["total"] == {
        "amount": "5000.0000",
        "currency": "COP",
    }  # 5 × 1000 (P10a quote totals are before tax)
    assert q["valid_until"] == "2026-12-09"

    # a DRAFT quote is internal: invisible and not acceptable from the portal
    draft = ok(
        seller.post(f"{S}/quotes", {"party": str(a.id), "lines": [{"item_id": str(item.id), "quantity": "1"}]}), 201
    )
    assert api_a.get(f"{C}/quotes/{draft['id']}").status_code == 404
    assert api_a.post(f"{C}/quotes/{draft['id']}:accept").status_code == 404
    # another customer cannot accept it
    assert api_b.post(f"{C}/quotes/{quote_id}:accept").status_code == 404

    # 3. the customer accepts → DRAFT sales order from the quote (nothing confirmed, reserved or posted)
    accepted = ok(api_a.post(f"{C}/quotes/{quote_id}:accept"))
    assert accepted["status"] == "ACCEPTED" and accepted["resource"]["type"] == "sales.order"
    order_id = accepted["resource"]["id"]
    assert accepted["data"]["quote"]["quote_status"] == "ORDERED"
    assert api_a.post(f"{C}/quotes/{quote_id}:accept").status_code == 409
    with w.scope():
        order = SalesOrder.objects.get(id=order_id)
        assert order.status == SalesOrder.Status.DRAFT and order.party_id == a.id and order.channel == "portal"
        assert order.source == {"type": "sales.quote", "id": quote_id, "number": q["number"]}
        assert order.total == Decimal("5000.0000")
        assert Quote.objects.get(id=quote_id).order_id == order.id
        assert CustomerRequest.objects.get(id=req["id"]).request_status == CustomerRequest.Status.ORDERED
    assert ok(api_a.get(f"{C}/orders"))["items"] == []  # drafts are internal

    # 4. the internal team confirms the order (credit check + reservation) → the customer follows it
    ok(seller.post(f"{S}/orders/{order_id}:confirm"))
    orders = ok(api_a.get(f"{C}/orders"))["items"]
    assert [o["id"] for o in orders] == [order_id] and orders[0]["status"] == "APPROVED"
    kpis = {k["id"]: k["value"] for k in ok(api_a.get(f"{C}/kpis"))["kpis"]}
    assert kpis["cp_pedidos_en_curso"] == "1"
    assert ok(api_b.get(f"{C}/orders"))["items"] == []

    # profile change requests go to an internal approval (applied by the P10a party service)
    pc = ok(
        api_a.post(
            f"{C}/requests", {"request_type": "PROFILE_CHANGE", "message": "", "changes": {"phone": "6015550101"}}
        ),
        201,
    )
    assert api_a.post(
        f"{C}/requests", {"request_type": "PROFILE_CHANGE", "changes": {"credit_limit": "1"}}
    ).status_code in (400, 422)
    with w.scope():
        assert Party.objects.get(id=a.id).phone is None
    ok(seller.post(f"{S}/portal-requests/{pc['id']}:approve", {"reason": "verificado (SINTÉTICO)"}))
    with w.scope():
        assert Party.objects.get(id=a.id).phone == "6015550101"
    assert ok(api_a.get(f"{C}/requests/{pc['id']}"))["request_status"] == "APPROVED"
    gen = ok(api_a.post(f"{C}/requests", {"request_type": "GENERAL", "message": "¿Horario de entrega?"}), 201)
    r = seller.post(f"{S}/portal-requests/{gen['id']}:reject", {})
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    ok(seller.post(f"{S}/portal-requests/{gen['id']}:reject", {"reason": "duplicada"}))
    assert ok(api_a.get(f"{C}/requests/{gen['id']}"))["request_status"] == "REJECTED"
