"""S8 - Fixed asset life cycle (two books) [P14, P11, P05] (shared/11 S8).

Laptop bought 2026-03-10 (4,800,000 + VAT 912,000 recoverable); NIIF straight line 36 months from April
(133,333.33/month, last month takes the residual); fiscal 20 %/year (FIX) = 80,000/month; sale on
2026-12-31 for 4,000,000 + VAT after December depreciation → NIIF gain 399,999.97.

Real ledger (NIIF book asserted; invariant 5 against the oracle register). The fiscal book cannot get its
own amounts through ``PostingRequest`` today: DEF-P22-007 (strict xfail). P14 register = module leg.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, refdocs
from qa_harness.andina import party
from qa_harness.refdocs import Entry

D = Decimal
COST, VAT = D(4800000), D(912000)
pytestmark = pytest.mark.django_db


def _acquire(w):
    vendor = {"party": party("Tecnología Andina (proveedor)"), "party_kind": "vendor"}
    return w.run(lambda u: refdocs.post_document(
        u, doc_type="assets.acquisition", company=w.a, day=dt.date(2026, 3, 10), total=COST + VAT,
        entries=[Entry("acquisition", [("FIXED_ASSET_COST", "DEBIT", COST), ("VAT_RECOVERABLE", "DEBIT", VAT),
                                       ("AP", "CREDIT", COST + VAT, vendor)])],
        payload={"asset": "PORTÁTIL-001 (SINTÉTICO)", "niif_months": 36, "fiscal_rate": "0.20 FIX"}))  # fmt: skip


def _depreciate(w, months):
    schedule = golden.straight_line(COST, 36)
    posted = []
    for k in range(months):
        first = golden.month_add(dt.date(2026, 4, 1), k)
        amount = schedule[k]
        posted.append(w.run(lambda u, first=first, amount=amount: refdocs.post_document(
            u, doc_type="assets.depreciation", company=w.a, day=golden.month_end(first), total=amount,
            entries=[Entry("depreciation", [("DEPRECIATION_EXPENSE", "DEBIT", amount),
                                            ("ACCUM_DEPRECIATION", "CREDIT", amount)])])))  # fmt: skip
    return posted


def test_p22b_S08_acquisition_and_eight_months_niif(andina):
    acq = _acquire(andina)
    assert andina.lines(acq.ref) == [("FIXED_ASSET_COST", "Dr", COST), ("VAT_RECOVERABLE", "Dr", VAT),
                                     ("AP", "Cr", D(5712000))]  # fmt: skip
    runs = _depreciate(andina, 8)
    assert [andina.lines(r.ref)[0][2] for r in runs] == [D("133333.33")] * 8
    accumulated = -andina.balance("ACCUM_DEPRECIATION", andina.a)
    assert accumulated == D("1066666.64")
    nbv_gl = andina.balance("FIXED_ASSET_COST", andina.a) - accumulated
    assert nbv_gl == COST - sum(golden.straight_line(COST, 36)[:8])  # invariant 5 (NIIF book)
    fiscal = golden.fiscal_monthly(COST) * 8
    assert accumulated - fiscal == D("426666.64")  # temporary difference (tax-difference register)


def test_p22b_S08_sale_after_december_gain(andina):
    _acquire(andina)
    _depreciate(andina, 9)
    accumulated = -andina.balance("ACCUM_DEPRECIATION", andina.a)
    assert accumulated == D("1199999.97")
    nbv = COST - accumulated
    price, vat = D(4000000), golden.round_cop(D(4000000) * golden.FIX["iva.general"])
    gain = price - nbv
    sale = andina.run(lambda u: refdocs.post_document(
        u, doc_type="assets.disposal", company=andina.a, day=dt.date(2026, 12, 31), total=price + vat,
        entries=[Entry("disposal", [
            ("ACCUM_DEPRECIATION", "DEBIT", accumulated),
            ("AR", "DEBIT", price + vat, {"party": party("Comprador del portátil"), "party_kind": "customer"}),
            ("FIXED_ASSET_COST", "CREDIT", COST), ("VAT_PAYABLE", "CREDIT", vat), ("GAIN_ON_DISPOSAL", "CREDIT", gain),
        ])]))  # fmt: skip
    assert andina.lines(sale.ref) == [("ACCUM_DEPRECIATION", "Dr", D("1199999.97")), ("AR", "Dr", D(4760000)),
                                      ("FIXED_ASSET_COST", "Cr", COST), ("VAT_PAYABLE", "Cr", D(760000)),
                                      ("GAIN_ON_DISPOSAL", "Cr", D("399999.97"))]  # fmt: skip
    assert andina.balance("FIXED_ASSET_COST", andina.a) == 0 and andina.balance("ACCUM_DEPRECIATION", andina.a) == 0


def test_p22b_S08_last_month_takes_the_residual():
    schedule = golden.straight_line(COST, 36)
    assert schedule[-1] == D("133333.45") and sum(schedule) == COST


@defects.xfail("DEF-P22-007")
@pytest.mark.req("UR-13")
def test_p22b_S08_fiscal_book_has_its_own_depreciation(andina):
    """shared/07 §5: separate entries per book. Needs a book selector on PostingRequest (CCR P22b-3)."""
    from kernel.interfaces.posting import PostingRequest

    assert "book_code" in PostingRequest.__dataclass_fields__
    _acquire(andina)
    _depreciate(andina, 8)
    assert -andina.balance("ACCUM_DEPRECIATION", andina.a, book="FISCAL") == D(640000)


@pytest.mark.req("UR-13")
def test_p22b_S08_module_leg_asset_register(andina):
    defects.require("PEND-P14")
