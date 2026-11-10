"""S2 - Purchases, WAC vs FIFO, GRNI, price difference [P11, P09, P05] (shared/11 S2).

Real: P05 ledger (both books), P06 tax engine (FIX rates), kernel sequences/outbox, P07 audit.
Double (until P09a/P11a merge): stock valuation (``refstock``) and the purchase documents (``refdocs``).
The module legs at the end run against P11/P09 once they are installed (XFAIL until then).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, refdocs, steps, taxes
from qa_harness.andina import item, party
from qa_harness.refdocs import Entry

D = Decimal
R1_DAY, R2_DAY, SALE_DAY, INV1_DAY, INV2_DAY = (dt.date(2026, 3, d) for d in (2, 3, 4, 5, 6))
pytestmark = pytest.mark.django_db


def vendor_invoice(world, company, receipt, received_qty, receipt_price, invoice_price, day, item_id, wh):
    """Vendor invoice matched to a receipt (shared/07 §2), price difference split on-hand/consumed."""
    stock = world.services["stock"]
    net = received_qty * invoice_price
    tax = taxes.compute(company, day, [("1", net, ("FIX-IVA19", "FIX-RF-COMPRAS"))], buyer_agent=True,
                        seller_regime="RESPONSABLE_IVA")  # fmt: skip
    # The company is the buyer here: it withholds renta on the vendor (Cr WHT_PAYABLE).
    by = taxes.by_type(tax)
    vat, wht = by["IVA"], by["RETEFUENTE"]
    grni = received_qty * receipt_price
    delta = invoice_price - receipt_price

    def fn(u):
        to_inv, to_cogs = (D(0), D(0))
        if delta:
            to_inv, to_cogs = stock.revalue_receipt(u, company, receipt.ref, "1", delta)
        lines = [("GRNI", "DEBIT", grni)]
        if to_inv:
            lines.append(("INVENTORY", "DEBIT", to_inv))
        if to_cogs:
            lines.append(("COGS", "DEBIT", to_cogs))
        lines += [("VAT_RECOVERABLE", "DEBIT", vat, {"tax_code": "FIX-IVA19"}),
                  ("AP", "CREDIT", net + vat - wht, {"party": party("Tostadora Andes"), "party_kind": "vendor"}),
                  ("WHT_PAYABLE", "CREDIT", wht, {"tax_code": "FIX-RF-COMPRAS"})]  # fmt: skip
        return refdocs.post_document(
            u, doc_type="purchase.invoice", company=company, day=day, total=net + vat - wht,
            entries=[Entry("invoice", lines)], links={"receipt": str(receipt.ref.id)},
        )  # fmt: skip

    return world.run(fn), {"vat": vat, "wht": wht, "net": net}


def run_s2(world, code="CAF-500", method="WAC"):
    a, wh, it = world.a, world.warehouse["BOG-01"], item(code)
    steps.define_item(world, a, it, wh, method)
    r1 = steps.goods_receipt(world, a, it, wh, D(100), D(10000), R1_DAY)
    r2 = steps.goods_receipt(world, a, it, wh, D(50), D(13000), R2_DAY)
    return a, wh, it, r1, r2


def test_p22b_S02_receipts_layers_and_grni(andina, stock):
    a, wh, it, r1, r2 = run_s2(andina)
    assert andina.lines(r1.ref) == [("INVENTORY", "Dr", D(1000000)), ("GRNI", "Cr", D(1000000))]
    assert andina.lines(r2.ref) == [("INVENTORY", "Dr", D(650000)), ("GRNI", "Cr", D(650000))]
    with andina.scope():
        st = stock.state(andina.ctx, a, it, wh)
    assert st["qty"] == D(150) and st["avg"] == D("11000.00000000") and st["value"] == D(1650000)
    assert andina.balance("GRNI", a) == D(-1650000)
    assert andina.balance("INVENTORY", a) == st["value"]  # invariant 3
    assert refdocs.numbers(andina.ctx, a, "purchase.receipt") == ["EA-1", "EA-2"]  # gap-free


@pytest.mark.parametrize(("code", "method", "cogs", "remaining", "after_pd"), [
    ("CAF-500", "WAC", D(1320000), D(330000), D(345000)),
    ("CAF-500F", "FIFO", D(1260000), D(390000), D(405000)),
])  # fmt: skip
def test_p22b_S02_wac_vs_fifo_vendor_invoices_and_price_difference(andina, stock, code, method, cogs, remaining,
                                                                   after_pd):  # fmt: skip
    a, wh, it, r1, r2 = run_s2(andina, code, method)
    sale = steps.sales_invoice(andina, a, party("Hoteles Sabana"), it, wh, D(120), D(20000), SALE_DAY)
    assert andina.lines(sale.ref, "cogs") == [("COGS", "Dr", cogs), ("INVENTORY", "Cr", cogs)]
    with andina.scope():
        assert stock.state(andina.ctx, a, it, wh)["value"] == remaining

    inv1, t1 = vendor_invoice(andina, a, r1, D(100), D(10000), D(10000), INV1_DAY, it, wh)
    assert (t1["vat"], t1["wht"]) == (D(190000), D(25000))
    assert andina.lines(inv1.ref) == [
        ("GRNI", "Dr", D(1000000)), ("VAT_RECOVERABLE", "Dr", D(190000)),
        ("AP", "Cr", D(1165000)), ("WHT_PAYABLE", "Cr", D(25000)),
    ]  # fmt: skip

    inv2, t2 = vendor_invoice(andina, a, r2, D(50), D(13000), D(13500), INV2_DAY, it, wh)
    assert (t2["vat"], t2["wht"]) == (D(128250), D(16875))
    expected = [("GRNI", "Dr", D(650000)), ("INVENTORY", "Dr", D(15000)), ("COGS", "Dr", D(10000)),
                ("VAT_RECOVERABLE", "Dr", D(128250)), ("AP", "Cr", D(786375)), ("WHT_PAYABLE", "Cr", D(16875))]  # fmt: skip
    assert andina.lines(inv2.ref) == expected
    with andina.scope():
        st = stock.state(andina.ctx, a, it, wh)
    assert st["value"] == after_pd and st["qty"] == D(30)
    if method == "WAC":
        assert st["avg"] == D(11500)  # new WAC = (330,000 + 15,000) / 30
    else:
        assert st["layer_value"] == D(405000)  # remaining layer 30 × 13,500
    assert andina.balance("GRNI", a) == 0  # S2.6
    assert andina.balance("INVENTORY", a) == st["value"] == st["moves_value"] + D(15000)  # invariant 3
    assert andina.balance("AP", a) == -(D(1165000) + D(786375))


def test_p22b_S02_oracle_agrees_with_reference_valuation(andina, stock):
    """The DB valuation double and the oracle compute the same layers for a random-ish sequence."""
    a, wh = andina.a, andina.warehouse["BOG-01"]
    it = item("ORACLE-1")
    steps.define_item(andina, a, it, wh, "FIFO")
    oracle = golden.FifoItem()
    for k, (qty, cost) in enumerate([(D(7), D("1000.333")), (D(5), D("999.999")), (D(3), D("1234.5678"))]):
        steps.goods_receipt(andina, a, it, wh, qty, cost, dt.date(2026, 3, 10 + k))
        oracle.receive(str(k), qty, cost)
    sale = steps.sales_invoice(andina, a, party("Cliente Oráculo"), it, wh, D(9), D(2000), dt.date(2026, 3, 20))
    expected = oracle.issue(D(9))
    assert andina.lines(sale.ref, "cogs")[0] == ("COGS", "Dr", expected)


@pytest.mark.req("UR-08", "UR-10")
def test_p22b_S02_module_leg_purchasing_and_inventory(andina):
    """The same S2 against P11 goods receipts / vendor invoices and P09 valuation (shared/11 S2)."""
    defects.require("PEND-P11", "PEND-P09")
    from kernel import services
    from kernel.interfaces.stock import StockService

    assert services.optional(StockService) is not None, "P09 must register StockService (kernel.services)"
