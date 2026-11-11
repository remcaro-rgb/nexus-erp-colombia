"""G2 gate test T-PUR-CYCLE-07 (gate-owned, written by the wave-05 integrator; synthetic data, DEV only).

S-PUR on the integrated product: purchase requisition → approval (SoD) → ``:to-rfq`` → RFQ sent to two vendors →
quotes → close/evaluate/award (recommended quote) → award approval (SoD) → DRAFT PO → PO approval/send → 2 partial
receipts into stock → vendor invoice (3-way match, IVA + retención, synthetic rates) → CE payment from the bank
fund (P16 treasury, P11 AP adapter) → AP item PAID, GRNI and AP cleared, TB balanced.

World: ``g2_world`` (real policy engine/OIDC, P09, P05, P11a/b, P16, P10 AR adapter; see its docstring).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from g2_world import PUR, TRE, capital_in, fund_id, gate_world, gl, money

from purchasing.requisitions.tests.kit import TODAY, P11bWorld, award_rfq, quote_lines, receive_order

D = Decimal
pytestmark = pytest.mark.django_db


@pytest.fixture
def w(db: Any) -> Iterator[P11bWorld]:
    with gate_world() as world:
        yield world


def test_T_PUR_CYCLE_07_s_pur_requisition_rfq_award_po_two_receipts_invoice_ce(w: P11bWorld) -> None:
    from purchasing.models import ApOpenItem, PurchaseOrder

    almacen = w.warehouse("PRINCIPAL")
    item = w.item("CAFE-ORG")
    v1, v2 = w.supplier("PROV-UNO"), w.supplier("PROV-DOS")

    # 1. purchase requisition → submit → approve (SoD: the requester cannot approve)
    sol = w.api("sol")
    r = sol.post(
        f"{PUR}/requisitions",
        {
            "document_date": TODAY.isoformat(),
            "needed_by": "2026-11-20",
            "currency": "COP",
            "lines": [{"line_no": 1, "item_id": str(item.id), "description": "Café orgánico (SINTÉTICO)",
                       "quantity": {"value": "10", "uom": "UND"}}],
        },
    )  # fmt: skip
    assert r.status_code == 201, r.content
    prid = r.json()["id"]
    assert sol.post(f"{PUR}/requisitions/{prid}:submit").status_code == 200
    assert w.api("sol").post(f"{PUR}/requisitions/{prid}:approve", {"reason": "yo"}).status_code == 403
    r = w.api("jefe").post(f"{PUR}/requisitions/{prid}:approve", {"reason": "ok"})
    assert r.status_code == 200, r.content

    # 2. → RFQ to two vendors, send, two quotes
    buyer = w.api("comprador")
    r = buyer.post(f"{PUR}/requisitions/{prid}:to-rfq", {"parameters": {"invited_vendors": [str(v1.id), str(v2.id)]}})
    assert r.status_code == 200, r.content
    target = r.json()["target"]
    assert r.json()["status"] == "POSTED" and target["type"] == "purchasing.rfq"
    rfq_id = target["id"]
    r = buyer.post(f"{PUR}/rfqs/{rfq_id}:send")
    assert r.status_code == 200, r.content
    from purchasing.models import Rfq

    with w.scope():
        rfq = Rfq.objects.get(id=rfq_id)
        assert rfq.source["id"] == prid  # lineage to the requisition
        assert rfq.lines.get().source_line_id is not None
    lines = [(item, "10")]
    for vendor, price in ((v1, "12000"), (v2, "11000")):
        q = buyer.post(
            f"{PUR}/rfqs/{rfq_id}/quotes",
            {"party": {"id": str(vendor.id)}, "currency": "COP", "lead_time_days": 3,
             "lines": quote_lines(lines, [price])},
        )  # fmt: skip
        assert q.status_code == 201, q.content

    # 3. award the recommended quote (cheapest, same lead time) + approval by someone else → DRAFT PO
    award_rfq(w, rfq_id)
    with w.scope():
        (po,) = PurchaseOrder.objects.filter(rfq_id=rfq_id)
        assert po.status == "DRAFT" and po.supplier_id == v2.id
    # the award approval created the PO (creator = aprobador); the buyer submits; SoD: neither approves it
    oid = str(po.id)
    r = buyer.post(f"{PUR}/orders/{oid}:submit")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.content
    for who in ("aprobador", "comprador"):
        denied = w.api(who).post(f"{PUR}/orders/{oid}:approve", {"reason": "ok"})
        assert denied.status_code == 403 and denied.json()["code"] == "sod_violation", denied.content
    r = w.api("jefe").post(f"{PUR}/orders/{oid}:approve", {"reason": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.content
    order = w.api("comprador").post(f"{PUR}/orders/{oid}:send").json()
    assert order["status"] == "SENT"

    # 4. two receipts (6 + 4)
    receive_order(w, order, almacen, "6")
    assert buyer.get(f"{PUR}/orders/{po.id}").json()["status"] == "PARTIALLY_RECEIVED"
    receive_order(w, buyer.get(f"{PUR}/orders/{po.id}").json(), almacen, "4")
    order = buyer.get(f"{PUR}/orders/{po.id}").json()
    assert order["status"] == "RECEIVED" and order["lines"][0]["received_qty"] == "10.000000"
    assert w.available(item, almacen) == D(10)
    b = gl(w)
    assert b["143505"] == D("110000.0000") and b["229505"] == D("-110000.0000")

    # 5. vendor invoice: 3-way match → post (IVA 19 %, retención 2.5 %, synthetic rates)
    inv = buyer.post(
        f"{PUR}/vendor-invoices",
        {
            "document_date": "2026-11-12",
            "party": order["party"],
            "vendor_invoice_number": "FV-G2-0001",
            "currency": "COP",
            "order": {"type": "purchasing.order", "id": order["id"]},
            "lines": [{"order_line_id": order["lines"][0]["id"], "quantity": {"value": "10", "uom": "UND"},
                       "unit_price": money("11000"), "tax_code": "IVA19", "withholding_codes": ["RTE25"]}],
        },
    )  # fmt: skip
    assert inv.status_code == 201, inv.content
    iid = inv.json()["id"]
    assert buyer.post(f"{PUR}/vendor-invoices/{iid}:match").json()["match_status"] == "MATCHED"
    posted = buyer.post(f"{PUR}/vendor-invoices/{iid}:post")
    assert posted.status_code == 200, posted.content
    # 110,000 + 20,900 IVA − 2,750 retención = 128,150 payable
    assert posted.json()["total"]["amount"] == "128150.0000"
    assert buyer.get(f"{PUR}/orders/{po.id}").json()["status"] == "CLOSED"
    b = gl(w)
    assert b["229505"] == D(0) and b["220505"] == D("-128150.0000")
    assert b["240810"] == D("20900.0000") and b["236540"] == D("-2750.0000")
    with w.scope():
        (ap,) = ApOpenItem.objects.filter(document_id=iid)
    assert ap.balance_cop == D("128150.0000") == -b["220505"]  # AP sub-ledger = GL

    # 6. CE payment of the vendor invoice from the bank fund
    capital_in(w, "1000000")
    cashier = w.api("tesorero")
    ce = cashier.post(
        f"{TRE}/disbursements",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": fund_id(w),
            "payment_method": "TRANSFER",
            "currency": "COP",
            "party": {"id": str(v2.party_id), "kind": "vendor"},
            "lines": [{"line_no": 1, "open_item_id": str(ap.id), "applied_amount": money("128150")}],
        },
    )
    assert ce.status_code == 201, ce.content
    paid = cashier.post(f"{TRE}/disbursements/{ce.json()['id']}:post")
    assert paid.status_code == 200, paid.content
    assert paid.json()["status"] == "POSTED" and paid.json()["number"].startswith("CE-")
    with w.scope():
        ap.refresh_from_db()
    assert (ap.balance, ap.status) == (D(0), "PAID")
    b = gl(w)
    assert b["220505"] == D(0) and b["229505"] == D(0)
    assert b["111005"] == D("1000000.0000") - D("128150.0000")
    assert b["143505"] == D("110000.0000")
    assert sum(b.values()) == D(0)
    from inventory.models import ItemValuation

    with w.scope():
        assert ItemValuation.objects.get(item_id=item.id).value == b["143505"]  # kardex = GL
