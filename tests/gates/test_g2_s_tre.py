"""G2 gate test T-X-QA-01 (gate-owned, written by the wave-05 integrator; synthetic data, DEV only).

S-TRE on the integrated product:

* customer side — P10 sales order → delivery (stock issue + COGS) → invoice posted (AR open item) → P16 recibo de
  caja applying part of the invoice with the customer's withholding (retención, synthetic 2.5 %) → bank statement
  imported and matched to the RC by its voucher id → fund reconciled;
* vendor side — P11 PO → receipt → vendor invoice posted (AP open item) → P16 comprobante de egreso paying it.

Checks AR/AP sub-ledgers = GL control accounts, kardex = GL inventory and a balanced trial balance.
World: ``g2_world`` (real policy engine/OIDC, P09, P05, P10, P11, P16; see its docstring).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from g2_world import PUR, TRE, base_price, boot_sales, capital_in, customer, fund_id, gate_world, gl, money

from purchasing.requisitions.tests.kit import TODAY, P11bWorld, order_sent, receive_order

D = Decimal
SALES = "/api/v1/sales"
pytestmark = pytest.mark.django_db


@pytest.fixture
def w(db: Any) -> Iterator[P11bWorld]:
    with gate_world() as world:
        boot_sales(world)
        yield world


def ok(r: Any, code: int = 200) -> dict[str, Any]:
    assert r.status_code == code, r.content
    return dict(r.json())


def test_T_X_QA_01_s_tre_invoice_rc_partial_withholding_bank_match_and_vendor_ce(w: P11bWorld) -> None:
    from purchasing.models import ApOpenItem
    from sales.models import ArOpenItem
    from treasury.models import StatementLine

    wh = w.warehouse("PRIN")
    item = w.item("PAN-ART")
    w.receive(item, wh, "5", cost="60000")  # stock-only synthetic opening layer
    party = customer(w, "C-TRE")
    base_price(w, item, "100000")

    # 1. sales: order → confirm → delivery → invoice posted (3 × 100,000 + IVA 19 %)
    sales = w.api("vendedor")
    line = {"item_id": str(item.id), "quantity": "3", "tax_code": "IVA19", "warehouse_id": str(wh.id)}
    order = ok(sales.post(f"{SALES}/orders", {"party": str(party.id), "document_date": TODAY.isoformat(),
                                              "lines": [line]}), 201)  # fmt: skip
    ok(sales.post(f"{SALES}/orders/{order['id']}:confirm"))
    delivery = ok(sales.post(f"{SALES}/deliveries", {"order": order["id"], "warehouse_id": str(wh.id)}), 201)
    ok(sales.post(f"{SALES}/deliveries/{delivery['id']}:validate"))
    invoice = ok(sales.post(f"{SALES}/orders/{order['id']}:to-invoice"), 201)
    posted = ok(sales.post(f"{SALES}/invoices/{invoice['id']}:post"))
    assert posted["document_status"] == "POSTED" and posted["number"].startswith("SETT")
    assert D(posted["total"]["amount"] if isinstance(posted["total"], dict) else posted["total"]) == D("357000")
    with w.scope():
        (ar,) = ArOpenItem.objects.filter(party_id=party.id)
    assert ar.balance == D("357000")
    b = gl(w)
    assert b["130505"] == D("357000.0000") and b["413595"] == D("-300000.0000") and b["240805"] == D("-57000.0000")
    assert b["613510"] == D("180000.0000") and b["143505"] == D("-180000.0000")  # COGS at average cost 60,000

    # 2. RC partial: 200,000 applied to the invoice, the customer withheld 2.5 % of the 300,000 base
    cashier = w.api("tesorero")
    rc = ok(cashier.post(f"{TRE}/receipts", {
        "document_date": TODAY.isoformat(), "fund_id": fund_id(w), "payment_method": "TRANSFER", "currency": "COP",
        "party": {"id": str(party.id), "kind": "customer"},
        "lines": [{"line_no": 1, "open_item_id": str(ar.id), "applied_amount": money("200000")},
                  {"line_no": 2, "tax_code": "RTE25", "unit_price": money("300000"), "description": "Retefuente"}],
    }), 201)  # fmt: skip
    assert rc["total"] == money("192500.0000") and rc["withholdings"]["total"] == money("7500.0000")
    rc = ok(cashier.post(f"{TRE}/receipts/{rc['id']}:post"))
    assert rc["status"] == "POSTED" and rc["number"].startswith("RC-")
    with w.scope():
        ar.refresh_from_db()
    assert (ar.balance, ar.status) == (D("157000.0000"), "PARTIAL")
    b = gl(w)
    assert b["111005"] == D("192500.0000") and b["135515"] == D("7500.0000") and b["130505"] == D("157000.0000")

    # 3. bank statement: imported line matched to the RC by its voucher id → fund reconciled
    csv = f"bank_account,date,description,reference,amount\nBCO-1,{TODAY},Consignación cliente,{rc['number']},192500\n"
    upload = io.BytesIO(csv.encode())
    upload.name = "extracto-sintetico.csv"
    res = cashier.client.post(f"{TRE}/statements:import", {"file": upload}, format="multipart",
                              **cashier._headers(True, {}))  # fmt: skip
    assert res.status_code == 202, res.content
    (st,) = ok(cashier.get(f"{TRE}/statement-lines"))["items"]
    assert st["matched"] is False
    snap = ok(cashier.get(f"{TRE}/funds/{fund_id(w)}/snapshot"))["rows"][0]
    assert D(snap["unreconciled"]) == D("192500")
    m = ok(cashier.post(f"{TRE}/statement-lines/{st['id']}:match", {"parameters": {"voucher_id": rc["id"]}}))
    assert m["matched"] is True
    with w.scope():
        assert str(StatementLine.objects.get(id=st["id"]).voucher_id) == rc["id"]
    snap = ok(cashier.get(f"{TRE}/funds/{fund_id(w)}/snapshot"))["rows"][0]
    assert D(snap["unreconciled"]) == 0 and D(snap["book_balance"]) == D("192500")

    # 4. vendor side: PO → receipt → vendor invoice posted → CE
    vendor = w.supplier("PROV-TRE")
    buyer = w.api("comprador")
    po = ok(buyer.post(f"{PUR}/orders", {
        "document_date": TODAY.isoformat(), "party": {"id": str(vendor.id)}, "currency": "COP",
        "lines": [{"line_no": 1, "item_id": str(item.id), "description": "Pan (SINTÉTICO)",
                   "quantity": {"value": "2", "uom": "UND"}, "unit_price": money("50000"), "tax_code": "IVA19"}],
    }), 201)  # fmt: skip
    po = order_sent(w, po["id"])
    receive_order(w, po, wh, "2")
    inv = ok(buyer.post(f"{PUR}/vendor-invoices", {
        "document_date": TODAY.isoformat(), "party": po["party"], "vendor_invoice_number": "FV-TRE-01",
        "currency": "COP", "order": {"type": "purchasing.order", "id": po["id"]},
        "lines": [{"order_line_id": po["lines"][0]["id"], "quantity": {"value": "2", "uom": "UND"},
                   "unit_price": money("50000"), "tax_code": "IVA19"}],
    }), 201)  # fmt: skip
    assert ok(buyer.post(f"{PUR}/vendor-invoices/{inv['id']}:match"))["match_status"] == "MATCHED"
    ok(buyer.post(f"{PUR}/vendor-invoices/{inv['id']}:post"))
    with w.scope():
        (ap,) = ApOpenItem.objects.filter(document_id=inv["id"])
    assert ap.balance == D("119000.0000")  # 100,000 + IVA 19,000
    capital_in(w, "500000")
    ce = ok(cashier.post(f"{TRE}/disbursements", {
        "document_date": TODAY.isoformat(), "fund_id": fund_id(w), "payment_method": "TRANSFER", "currency": "COP",
        "party": {"id": str(vendor.party_id), "kind": "vendor"},
        "lines": [{"line_no": 1, "open_item_id": str(ap.id), "applied_amount": money("119000")}],
    }), 201)  # fmt: skip
    ce = ok(cashier.post(f"{TRE}/disbursements/{ce['id']}:post"))
    assert ce["status"] == "POSTED" and ce["number"].startswith("CE-")
    with w.scope():
        ap.refresh_from_db()
        ar_open = sum((x.balance for x in ArOpenItem.objects.all()), D(0))
        ap_open = sum((x.balance_cop for x in ApOpenItem.objects.all()), D(0))
    assert (ap.balance, ap.status) == (D(0), "PAID")

    # sub-ledgers = GL, kardex = GL (opening layer is stock-only), TB balanced
    b = gl(w)
    assert ar_open == b["130505"] == D("157000.0000")
    assert ap_open == -b.get("220505", D(0)) == D(0)
    assert b["111005"] == D("192500.0000") + D("500000.0000") - D("119000.0000")
    assert sum(b.values()) == D(0)
    from inventory.models import ItemValuation

    with w.scope():
        val = ItemValuation.objects.get(item_id=item.id)
    assert val.qty == D(4) and val.value == D("300000.0000") + b["143505"]
