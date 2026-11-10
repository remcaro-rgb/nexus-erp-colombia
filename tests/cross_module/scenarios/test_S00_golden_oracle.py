"""S0 - the independent oracle reproduces every number published in shared/11 (S1-S12).

If the specification and the oracle disagree, this fails first, before any module is blamed.
Pure Decimal arithmetic; no database.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import golden as g

D = Decimal


def test_p22b_S00_oracle_s1_sale_and_receipt():
    t = g.sale_totals(D(120), D(20000))
    assert (t.net, t.vat, t.total) == (D(2400000), D(456000), D(2856000))
    assert (t.wht_renta, t.wht_iva, t.wht_ica) == (D(60000), D(68400), D(23184))
    assert t.receipt == D(2704416)


def test_p22b_S00_oracle_s2_wac_fifo_and_price_difference():
    wac, fifo = g.WacItem(), g.FifoItem()
    for key, qty, cost in (("R1", D(100), D(10000)), ("R2", D(50), D(13000))):
        wac.receive(qty, cost)
        fifo.receive(key, qty, cost)
    assert wac.avg == D("11000.00000000")
    assert wac.issue(D(120)) == D(1320000) and fifo.issue(D(120)) == D(1260000)
    assert (wac.value, fifo.value) == (D(330000), D(390000))
    inv = g.vendor_invoice(D(1000000))
    assert (inv["vat"], inv["wht"], inv["ap"]) == (D(190000), D(25000), D(1165000))
    to_inv, to_cogs = g.price_difference(D(50), wac.qty, D(500))
    assert (to_inv, to_cogs) == (D(15000), D(10000))
    inv2 = g.vendor_invoice(D(675000))
    assert (inv2["vat"], inv2["wht"], inv2["ap"]) == (D(128250), D(16875), D(786375))
    wac.revalue(to_inv)
    assert wac.avg == D(11500)
    assert fifo.revalue_layer("R2", D(500)) == D(15000) and fifo.value == D(405000)


def test_p22b_S00_oracle_s3_landed_cost():
    assert g.allocate(D(300000), [D(1000000), D(2000000)]) == [D(100000), D(200000)]
    assert g.allocate(D(300000), [D(200), D(100)]) == [D(200000), D(100000)]
    assert g.allocate(D(100000), [D(1), D(1), D(1)]) == [D("33333.3333"), D("33333.3333"), D("33333.3334")]
    y_share = D(200000)
    assert g.allocate(y_share, [D(50), D(50)]) == [D(100000), D(100000)]  # on hand vs sold


def test_p22b_S00_oracle_s4_pos_cash():
    expected = D(200000) + D(119000) + D(23800) - D(23800)
    assert expected == D(319000) and expected - D(318500) == D(500)
    assert g.round_cop(D(100000) * g.FIX["iva.general"]) == D(19000)
    assert g.round_cop(D(50000) * D("1.19")) == D(59500) and g.round_cop(D(20000) * D("1.19")) == D(23800)


def test_p22b_S00_oracle_s6_payroll():
    p = g.payroll_s6()
    assert p["transport"] == D(249095) and 2 * g.FIX["smmlv.2026"] == D(3501810)
    assert (p["health_employee"], p["pension_employee"], p["fsp"]) == (D(120000), D(120000), D(0))
    assert p["taxable"] == D(2070000) and p["taxable_uvt"] == D("39.52") and p["withholding"] == 0
    assert p["net"] == D(3009095)
    assert (p["pension_employer"], p["health_employer"], p["sena"], p["icbf"]) == (D(360000), 0, 0, 0)
    assert (p["ccf"], p["arl"]) == (D(120000), D(15660))
    assert (p["cesantias"], p["int_cesantias"], p["prima"], p["vacaciones"]) == (
        D("270757.92"), D("2707.58"), D("270757.92"), D(125000),
    )  # fmt: skip


@pytest.mark.parametrize(
    ("case", "day", "start", "end", "night", "surcharge"),
    [
        ("T7-1", dt.date(2025, 12, 24), 19, 22, 1, None),
        ("T7-2", dt.date(2025, 12, 25), 19, 22, 3, D("0.80")),
        ("T7-3", dt.date(2026, 6, 28), 14, 22, 3, D("0.80")),
        ("T7-4", dt.date(2026, 7, 5), 14, 22, 3, D("0.90")),
        ("T7-6", dt.date(2027, 7, 4), 14, 22, 3, D("1.00")),
    ],
)
def test_p22b_S00_oracle_s7_time_rules(case, day, start, end, night, surcharge):
    assert g.night_hours(day, start, end) == night, case
    if surcharge is not None:
        assert g.dated(g.SUNDAY_SURCHARGE, day) == surcharge, case


def test_p22b_S00_oracle_s7_t75_amounts_and_weekly_limit():
    salary = D(3000000)
    assert g.round_cop(g.hourly_value(salary, 210)) == D("14285.71")
    assert g.surcharge_amount(salary, 210, 8, D("0.90")) == D("102857.14")
    assert g.surcharge_amount(salary, 210, 3, g.FIX["recargo.nocturno"]) == D("15000.00")
    assert g.dated(g.WEEKLY_MAX_HOURS, dt.date(2026, 7, 5)) == 44
    assert g.dated(g.WEEKLY_MAX_HOURS, dt.date(2026, 7, 19)) == 42
    assert [g.is_habitual_sunday(n) for n in (1, 2, 3)] == [False, False, True]


def test_p22b_S00_oracle_s8_two_books():
    niif = g.straight_line(D(4800000), 36)
    assert niif[0] == D("133333.33") and sum(niif) == D(4800000) and niif[-1] == D("133333.45")
    assert sum(niif[:8]) == D("1066666.64") and g.fiscal_monthly(D(4800000)) * 8 == D(640000)
    assert sum(niif[:8]) - g.fiscal_monthly(D(4800000)) * 8 == D("426666.64")
    accumulated = sum(niif[:9])
    assert accumulated == D("1199999.97") and D(4800000) - accumulated == D("3600000.03")
    assert D(4000000) - (D(4800000) - accumulated) == D("399999.97")


def test_p22b_S00_oracle_s9_s10_s12():
    base = g.FIX["retefuente.servicios.base_uvt"] * g.FIX["uvt.2026"]
    assert base == D(104748)
    wh = [g.services_withholding(x, base_uvt=D(2), uvt=g.FIX["uvt.2026"], rate=D("0.04"))
          for x in (D(400000), D(350000), D(250000))]  # fmt: skip
    assert wh == [D(16000), D(14000), D(10000)]
    s10 = g.sale_totals(D(10), D(15000), withholding_agent=False)
    assert (s10.net, s10.vat, s10.total) == (D(150000), D(28500), D(178500))
    assert D(10) * (D(15000) - D(11500)) == D(35000)
    assert g.fx(D(1000), D(4000)) == D(4000000)
    assert g.fx(D(1000), D(4100)) - g.fx(D(1000), D(4000)) == D(100000)
    assert g.fx(D(1000), D(4050)) - g.fx(D(1000), D(4000)) == D(50000)


def test_p22b_S00_oracle_nit_check_digit_matches_published_examples():
    # Synthetic NIT 900.000.001 of the demo company A: DV computed by algorithm (shared/11).
    from localization_co import nit

    for number in ("900000001", "900000002", "860002964", "800197268", "1020304050"):
        assert g.nit_check_digit(number) == nit.check_digit(number), number
