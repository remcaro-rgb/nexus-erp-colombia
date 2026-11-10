"""T-PORTAL-SUP-01 — a supplier submits a quote before the deadline via the portal; the buyer sees it in the
comparison; another supplier cannot see it. T-PORTAL-SUP-02 — PO acknowledgement and shipment notice flow;
receiving sees the notice.

Real portal identities (invitation → erp-portal token → accept), real policy engine, real P11a purchasing."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from kernel import clock
from kernel.errors import PermissionDenied
from purchasing.portal import service
from purchasing.requisitions.tests.kit import (
    P11bWorld,
    advance,
    onboard_supplier,
    order_sent,
    quote_lines,
    rfq_sent,
)

SP = "/api/portal/v1/supplier"
PUR = "/api/v1/purchasing"


def two_suppliers(w: P11bWorld, notifications: Any) -> dict[str, Any]:
    a, b, c = w.supplier("PROV-A"), w.supplier("PROV-B"), w.supplier("PROV-C")
    return {
        "a": a,
        "b": b,
        "c": c,
        "api_a": onboard_supplier(w, notifications, a, "prov-a"),
        "api_b": onboard_supplier(w, notifications, b, "prov-b"),
        "api_c": onboard_supplier(w, notifications, c, "prov-c"),
    }


def test_T_PORTAL_SUP_01_quote_via_portal_before_deadline_is_compared_and_private(
    w: P11bWorld, fakes: dict[str, Any]
) -> None:
    s = two_suppliers(w, fakes["notifications"])
    item = w.item("HARINA")
    items = [(item, "100")]
    deadline = clock.now() + dt.timedelta(days=2)
    rfq = rfq_sent(w, items, [s["a"], s["b"]], deadline=deadline)
    rid = rfq["id"]

    listed = s["api_a"].get(f"{SP}/rfqs").json()["items"]
    assert [x["id"] for x in listed] == [rid] and listed[0]["rfq_status"] == "OPEN"
    assert listed[0]["lines"][0]["quantity"] == {"value": "100.000000", "uom": "UND"}
    assert s["api_c"].get(f"{SP}/rfqs").json()["items"] == []  # not invited
    assert s["api_c"].get(f"{SP}/rfqs/{rid}").status_code == 404

    qa = s["api_a"].post(
        f"{SP}/rfqs/{rid}/quotes", {"currency": "COP", "lead_time_days": 4, "lines": quote_lines(items, ["2800"])}
    )
    assert qa.status_code == 201, qa.content
    qb = s["api_b"].post(
        f"{SP}/rfqs/{rid}/quotes", {"currency": "COP", "lead_time_days": 2, "lines": quote_lines(items, ["3100"])}
    )
    assert qb.status_code == 201, qb.content
    a_id, b_id = qa.json()["id"], qb.json()["id"]
    assert qa.json()["lines"][0]["unit_price"] == {"amount": "2800.0000", "currency": "COP"}

    # each supplier sees only the own quote
    assert [q["id"] for q in s["api_a"].get(f"{SP}/rfqs/{rid}/quotes").json()["items"]] == [a_id]
    assert s["api_a"].get(f"{SP}/rfqs/{rid}/quotes/{b_id}").status_code == 404
    assert s["api_c"].get(f"{SP}/rfqs/{rid}/quotes").status_code == 404
    # a body naming another vendor is refused (the party comes from the token only)
    r = s["api_a"].post(
        f"{SP}/rfqs/{rid}/quotes",
        {"party": {"id": str(s["b"].id)}, "currency": "COP", "lines": quote_lines(items, ["1"])},
    )
    assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation"

    # after the deadline the portal refuses new quotes
    advance(deadline + dt.timedelta(minutes=1))
    late = s["api_b"].post(f"{SP}/rfqs/{rid}/quotes", {"currency": "COP", "lines": quote_lines(items, ["2000"])})
    assert late.status_code == 422 and late.json()["code"] == "portal_rfq_closed"
    assert s["api_a"].get(f"{SP}/rfqs/{rid}").json()["rfq_status"] == "CLOSED"

    # the buyer sees both portal quotes in the comparison
    buyer = w.api("comprador")
    assert buyer.post(f"{PUR}/rfqs/{rid}:close").status_code == 200
    assert buyer.post(f"{PUR}/rfqs/{rid}:evaluate").status_code == 200
    comparison = buyer.get(f"{PUR}/rfqs/{rid}/comparison").json()
    assert comparison["recommended_quote_id"] in (a_id, b_id)
    quotes = {q["id"]: q for q in buyer.get(f"{PUR}/rfqs/{rid}/quotes").json()["items"]}
    assert set(quotes) == {a_id, b_id} and {q["channel"] for q in quotes.values()} == {"supplier_portal"}


def draft_order_for(w: P11bWorld, supplier: Any, item: Any, qty: str = "10") -> dict[str, Any]:
    r = w.api("comprador").post(
        f"{PUR}/orders",
        {
            "party": {"id": str(supplier.id)},
            "currency": "COP",
            "notes": "NOTA-INTERNA: negociar descuento (no visible al proveedor)",
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(item.id),
                    "description": item.name,
                    "quantity": {"value": qty, "uom": "UND"},
                    "unit_price": {"amount": "1500", "currency": "COP"},
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    return dict(r.json())


def test_T_PORTAL_SUP_02_acknowledgement_and_shipment_notice_reach_receiving(
    w: P11bWorld, fakes: dict[str, Any]
) -> None:
    s = two_suppliers(w, fakes["notifications"])
    item = w.item("LECHE")
    draft = draft_order_for(w, s["a"], item)
    assert s["api_a"].get(f"{SP}/purchase-orders").json()["items"] == []  # drafts are internal
    order = order_sent(w, draft["id"])
    oid = order["id"]

    seen = s["api_a"].get(f"{SP}/purchase-orders/{oid}").json()
    assert seen["status"] == "SENT" and seen["acknowledged_at"] is None and "notes" not in seen
    assert s["api_b"].get(f"{SP}/purchase-orders/{oid}").status_code == 404
    assert s["api_b"].post(f"{SP}/purchase-orders/{oid}:acknowledge").status_code == 404

    ack = s["api_a"].post(f"{SP}/purchase-orders/{oid}:acknowledge", {"comment": "Confirmado"})
    assert ack.status_code == 200 and ack.json()["status"] == "ACKNOWLEDGED", ack.content
    assert ack.json()["acknowledged_at"] is not None
    assert w.api("comprador").get(f"{PUR}/orders/{oid}").json()["status"] == "ACKNOWLEDGED"
    again = s["api_a"].post(f"{SP}/purchase-orders/{oid}:acknowledge")
    assert again.status_code == 409

    too_much = s["api_a"].post(
        f"{SP}/shipments",
        {"purchase_order_id": oid, "expected_date": "2026-11-12", "lines": [{"line_no": 1, "quantity": "11"}]},
    )
    assert too_much.status_code == 422
    asn = s["api_a"].post(
        f"{SP}/shipments",
        {
            "purchase_order_id": oid,
            "expected_date": "2026-11-12",
            "tracking": "Guía SINT-123",
            "lines": [{"line_no": 1, "quantity": "6"}],
        },
    )
    assert asn.status_code == 201, asn.content
    assert asn.json()["number"].startswith("AE-") and asn.json()["status"] == "ANNOUNCED"
    assert s["api_b"].get(f"{SP}/shipments/{asn.json()['id']}").status_code == 404
    assert (
        s["api_b"]
        .post(
            f"{SP}/shipments",
            {"purchase_order_id": oid, "expected_date": "2026-11-12", "lines": [{"line_no": 1, "quantity": "1"}]},
        )
        .status_code
        == 404
    )

    # receiving sees the announced shipment of the order; others without the permission do not
    notices = w.run(lambda u: service.notices_for_receiving(u, w.company(), order_id=None), user="bodega")
    assert [(n["purchase_order_number"], n["expected_date"], n["tracking"]) for n in notices] == [
        (order["number"], "2026-11-12", "Guía SINT-123")
    ]
    assert notices[0]["lines"] == [{"line_no": 1, "quantity": "6.000000"}]
    with pytest.raises(PermissionDenied):
        w.run(lambda u: service.notices_for_receiving(u, w.company()), user="externo")

    # a change request keeps the order SENT for the buyer and is visible as CHANGE_REQUESTED
    second = order_sent(w, draft_order_for(w, s["a"], item, "5")["id"])
    no_comment = s["api_a"].post(
        f"{SP}/purchase-orders/{second['id']}:acknowledge", {"parameters": {"accepted": False}}
    )
    assert no_comment.status_code == 422
    change = s["api_a"].post(
        f"{SP}/purchase-orders/{second['id']}:acknowledge",
        {"parameters": {"accepted": False}, "comment": "Solo podemos entregar 4 unidades"},
    )
    assert change.status_code == 200 and change.json()["status"] == "CHANGE_REQUESTED"
    assert w.api("comprador").get(f"{PUR}/orders/{second['id']}").json()["status"] == "SENT"
