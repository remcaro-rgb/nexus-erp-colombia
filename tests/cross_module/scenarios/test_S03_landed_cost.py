"""S3 - Landed cost allocation [P11, P09] (shared/11 S3).

Freight 300,000 + VAT 57,000 allocated by value or by quantity over the receipt of X (200 × 5,000) and
Y (100 × 20,000); variant: 50 Y sold before the freight posts (on-hand part → INVENTORY, consumed part →
COGS); rounding: the last line takes the residual. Real ledger/tax/sequences/outbox/audit; the valuation
and the landed-cost document are the reference doubles until P11a/P09a merge.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, refdocs, steps, taxes
from qa_harness.andina import item, party
from qa_harness.refdocs import Entry

D = Decimal
RECEIPT_DAY, SALE_DAY, FREIGHT_DAY = dt.date(2026, 5, 4), dt.date(2026, 5, 5), dt.date(2026, 5, 6)
pytestmark = pytest.mark.django_db


def _receive(w):
    a, wh = w.a, w.warehouse["BOG-01"]
    x, y = item("S3-X"), item("S3-Y")
    for it in (x, y):
        steps.define_item(w, a, it, wh)
    rx = steps.goods_receipt(w, a, x, wh, D(200), D(5000), RECEIPT_DAY)
    ry = steps.goods_receipt(w, a, y, wh, D(100), D(20000), RECEIPT_DAY)
    return a, wh, x, y, rx, ry


def _post_freight(w, a, wh, allocations, receipts, items):
    """Freight vendor invoice allocated to the receipt layers in one unit of work."""
    stock = w.services["stock"]
    tax = taxes.compute(a, FREIGHT_DAY, [("1", D(300000), ("FIX-IVA19",))], buyer_agent=False)
    vat = taxes.by_type(tax)["IVA"]

    def fn(u):
        lines = []
        for amount, receipt, it in zip(allocations, receipts, items, strict=True):
            qty = D(200) if it == item("S3-X") else D(100)
            to_inv, to_cogs = stock.revalue_receipt(u, a, receipt.ref, "1", amount / qty)
            if to_inv:
                lines.append(("INVENTORY", "DEBIT", to_inv, {"item_id": it, "warehouse_id": wh}))
            if to_cogs:
                lines.append(("COGS", "DEBIT", to_cogs, {"item_id": it}))
        lines += [("VAT_RECOVERABLE", "DEBIT", vat), ("AP", "CREDIT", D(300000) + vat,
                                                       {"party": party("Transportes Sabana"), "party_kind": "vendor"})]  # fmt: skip
        return refdocs.post_document(u, doc_type="purchase.invoice", company=a, day=FREIGHT_DAY,
                                     total=D(357000), entries=[Entry("landed_cost", lines)])  # fmt: skip

    return w.run(fn)


@pytest.mark.parametrize(("basis", "expected"), [
    ("value", [D(100000), D(200000)]),
    ("quantity", [D(200000), D(100000)]),
])  # fmt: skip
def test_p22b_S03_allocation_by_value_and_by_quantity(andina, stock, basis, expected):
    a, wh, x, y, rx, ry = _receive(andina)
    bases = [D(1000000), D(2000000)] if basis == "value" else [D(200), D(100)]
    alloc = golden.allocate(D(300000), bases)
    assert alloc == expected
    inv = _post_freight(andina, a, wh, alloc, [rx, ry], [x, y])
    assert andina.lines(inv.ref) == [("INVENTORY", "Dr", expected[0]), ("INVENTORY", "Dr", expected[1]),
                                     ("VAT_RECOVERABLE", "Dr", D(57000)), ("AP", "Cr", D(357000))]  # fmt: skip
    with andina.scope():
        sx, sy = stock.state(andina.ctx, a, x, wh), stock.state(andina.ctx, a, y, wh)
    assert sx["value"] == D(1000000) + expected[0] and sy["value"] == D(2000000) + expected[1]
    if basis == "value":
        assert (sx["avg"] - D(5000), sy["avg"] - D(20000)) == (D(500), D(2000))  # unit +500 / +2,000
    assert andina.balance("INVENTORY", a) == sx["value"] + sy["value"]  # invariant 3


def test_p22b_S03_variant_y_partly_sold_before_freight(andina, stock):
    a, wh, x, y, rx, ry = _receive(andina)
    steps.sales_invoice(andina, a, party("Cliente S3"), y, wh, D(50), D(30000), SALE_DAY)
    inv = _post_freight(andina, a, wh, golden.allocate(D(300000), [D(1000000), D(2000000)]), [rx, ry], [x, y])
    assert andina.lines(inv.ref) == [("INVENTORY", "Dr", D(100000)), ("INVENTORY", "Dr", D(100000)),
                                     ("COGS", "Dr", D(100000)), ("VAT_RECOVERABLE", "Dr", D(57000)),
                                     ("AP", "Cr", D(357000))]  # fmt: skip
    with andina.scope():
        assert stock.state(andina.ctx, a, y, wh)["value"] == D(1000000) + D(100000)


def test_p22b_S03_rounding_last_line_takes_the_residual(andina):
    parts = golden.allocate(D(100000), [D(1), D(1), D(1)])
    assert parts == [D("33333.3333"), D("33333.3333"), D("33333.3334")] and sum(parts) == D(100000)
    a = andina.a
    posted = andina.run(lambda u: refdocs.post_document(
        u, doc_type="purchase.invoice", company=a, day=FREIGHT_DAY, total=D(100000),
        entries=[Entry("landed_cost", [*(("INVENTORY", "DEBIT", p) for p in parts),
                                       ("AP", "CREDIT", D(100000), {"party": party("Fletes"), "party_kind": "vendor"})])]))  # fmt: skip
    lines = andina.lines(posted.ref)
    assert [x[2] for x in lines[:3]] == parts and not any(r == "ROUNDING" for r, _, _ in lines)


@pytest.mark.req("UR-08", "UR-10")
def test_p22b_S03_module_leg_landed_cost_document(andina):
    defects.require("PEND-P11", "PEND-P09")
