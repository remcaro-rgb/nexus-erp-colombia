"""S9 - Documento soporte with weekly accumulation [P11, P15, P06] and S10 - Intercompany sale A → B
[P16, P10, P11, P09] (shared/11).

S9: services from a non-obligated individual on Mon/Wed/Fri; withholding (FIX 4 %, base 2 UVT = 104,748,
DISPUTED) on each payment ≥ base, computed by the real P06 engine; one DS on the last business day of the
week (real ``localization_co`` calendar; a holiday Friday moves it back). S10: both companies commit in the
same transaction (shared/07 §6); failure injection proves neither side persists alone.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, refdocs, steps, taxes
from qa_harness.andina import item, party
from qa_harness.failure_injection import AtomicityProbe, sweep
from qa_harness.refdocs import Entry, StockOp

from localization_co.holidays import is_business_day, is_holiday

D = Decimal
PROVIDER = party("Juan Pérez (persona natural no obligada, SINTÉTICO)")


def _withholdings(company, payments):
    out = []
    for day, amount in payments:
        result = taxes.compute(company, day, [("1", amount, ("FIX-RF-SERVICIOS",))], buyer_agent=True,
                               seller_regime="NO_RESPONSABLE_IVA")  # fmt: skip
        out.append((taxes.by_type(result).get("RETEFUENTE", D(0)), result))
    return out


@pytest.mark.parametrize(("monday", "expected_ds_day"), [
    (dt.date(2026, 5, 4), dt.date(2026, 5, 8)),    # ordinary week → Friday
    (dt.date(2026, 3, 30), dt.date(2026, 4, 1)),   # Holy Thursday + Good Friday → Wednesday
])  # fmt: skip
@pytest.mark.django_db
def test_p22b_S09_weekly_support_document(andina, monday, expected_ds_day):
    a = andina.a
    payments = [(monday, D(400000)), (monday + dt.timedelta(days=2), D(350000)),
                (monday + dt.timedelta(days=4), D(250000))]  # fmt: skip
    ds_day = golden.last_business_day_of_week(monday, is_business_day)
    assert ds_day == expected_ds_day
    if expected_ds_day.weekday() != 4:
        assert is_holiday(monday + dt.timedelta(days=4))
    wh = _withholdings(a, payments)
    assert [w for w, _ in wh] == [D(16000), D(14000), D(10000)]
    snaps = [s for _, r in wh for s in r.parameter_snapshots if s["code"] == "fix.retefuente.servicios.base_uvt"]
    assert snaps and all(s["status"] == "DISPUTED" for s in snaps)  # DISPUTED computes with a warning snapshot
    total, wht = sum((p for _, p in payments), D(0)), sum((w for w, _ in wh), D(0))
    ds = andina.run(lambda u: refdocs.post_document(
        u, doc_type="purchase.support_document", company=a, day=ds_day, total=total,
        entries=[Entry("support_document", [("SERVICES_EXPENSE", "DEBIT", total),
                                            ("AP", "CREDIT", total - wht, {"party": PROVIDER, "party_kind": "vendor"}),
                                            ("WHT_PAYABLE", "CREDIT", wht)])],
        payload={"accumulation": "WEEKLY", "payments": [(d.isoformat(), str(p)) for d, p in payments]}))  # fmt: skip
    assert andina.lines(ds.ref) == [("SERVICES_EXPENSE", "Dr", D(1000000)), ("AP", "Cr", D(960000)),
                                    ("WHT_PAYABLE", "Cr", D(40000))]  # fmt: skip
    assert refdocs.document(andina.ctx, ds.ref)["number"] == "DS-1"
    assert [e.posting_date for e in andina.entries(ds.ref)] == [expected_ds_day]


@pytest.mark.django_db
def test_p22b_S09_payment_below_base_has_no_withholding(andina):
    (w, result), = _withholdings(andina.a, [(dt.date(2026, 5, 4), D(104747))])
    assert w == 0 and not [t for t in result.lines if t.withholding]
    (w2, _), = _withholdings(andina.a, [(dt.date(2026, 5, 4), D(104748))])
    assert w2 == golden.round_cop(D(104748) * D("0.04"))


@pytest.mark.django_db
@pytest.mark.req("UR-10", "UR-04")
def test_p22b_S09_module_leg_ds_transmission(andina):
    defects.require("PEND-P11", "PEND-P15")


# --------------------------------------------------------------------------- S10


def _ic_sale(w, hooks=None):
    a, b = w.a, w.b
    wh_a, wh_b = w.warehouse["BOG-01"], w.warehouse["CLO-01"]
    it = item("IC-CAF")
    stock = w.services["stock"]
    tax = taxes.compute(a, dt.date(2026, 7, 7), [("1", D(150000), ("FIX-IVA19",))], buyer_agent=False)
    vat = taxes.by_type(tax)["IVA"]

    def fn(u):
        posted = refdocs.post_document(
            u, doc_type="intercompany.sale", company=a, day=dt.date(2026, 7, 7), total=D(150000) + vat,
            stock=[StockOp("issue", [steps.stock_line("1", it, wh_a, D(10))]),
                   StockOp("receive", [steps.stock_line("1", it, wh_b, D(10), D(15000))], company=b)],
            stock_service=stock, links={"ic_partner": str(b)}, hooks=hooks,
        )  # fmt: skip
        cogs = posted.stock[0].total_value
        steps.post_entries(u, posted, dt.date(2026, 7, 7), [
            Entry("issue", [("IC_RECEIVABLE", "DEBIT", D(150000) + vat, {"party": b, "party_kind": "party"}),
                            ("REVENUE_IC", "CREDIT", D(150000)), ("VAT_PAYABLE", "CREDIT", vat)]),
            Entry("cogs", [("COGS", "DEBIT", cogs), ("INVENTORY", "CREDIT", cogs)]),
            Entry("mirror", [("INVENTORY", "DEBIT", D(150000)), ("VAT_RECOVERABLE", "DEBIT", vat),
                             ("IC_PAYABLE", "CREDIT", D(150000) + vat, {"party": a, "party_kind": "party"})],
                  company=b),
        ])  # fmt: skip
        return posted

    return fn


def _setup_s10(w):
    a, b = w.a, w.b
    it = item("IC-CAF")
    steps.define_item(w, a, it, w.warehouse["BOG-01"])
    steps.define_item(w, b, it, w.warehouse["CLO-01"])
    steps.goods_receipt(w, a, it, w.warehouse["BOG-01"], D(10), D(11500), dt.date(2026, 7, 1))
    return it


@pytest.mark.django_db
def test_p22b_S10_intercompany_both_companies_one_transaction(andina, stock):
    it = _setup_s10(andina)
    ic = andina.run(_ic_sale(andina))
    assert andina.lines(ic.ref, "issue") == [("IC_RECEIVABLE", "Dr", D(178500)), ("REVENUE_IC", "Cr", D(150000)),
                                             ("VAT_PAYABLE", "Cr", D(28500))]  # fmt: skip
    assert andina.lines(ic.ref, "cogs") == [("COGS", "Dr", D(115000)), ("INVENTORY", "Cr", D(115000))]
    assert andina.lines(ic.ref, "mirror") == [("INVENTORY", "Dr", D(150000)), ("VAT_RECOVERABLE", "Dr", D(28500)),
                                              ("IC_PAYABLE", "Cr", D(178500))]  # fmt: skip
    with andina.scope():
        entries = andina.entries(ic.ref)
        assert {e.company_id for e in entries} == {andina.a, andina.b}  # linked by the same source (ic link)
        sb = stock.state(andina.ctx, andina.b, it, andina.warehouse["CLO-01"])
    assert (sb["qty"], sb["value"]) == (D(10), D(150000))
    assert andina.balance("IC_RECEIVABLE", andina.a) == -andina.balance("IC_PAYABLE", andina.b) == D(178500)
    # Consolidation [A] (oracle): unrealized profit in B's unsold inventory.
    assert sb["qty"] * (D(15000) - D(11500)) == D(35000)


@pytest.mark.django_db
def test_p22b_S10_failure_injection_neither_side_persists(andina):
    _setup_s10(andina)
    tables = ["qa_ref_document", "qa_ref_move", "ledger_journal_entry", "ledger_journal_line", "kernel_outbox"]
    result = sweep(lambda: andina.run(_ic_sale(andina)), AtomicityProbe(tables, ctx=andina.ctx),
                   expect_changed=tables, isolate=True, sample=40)  # fmt: skip
    assert result.ok, result.violations


@pytest.mark.django_db
@pytest.mark.req("UR-01")
def test_p22b_S10_a_context_without_b_cannot_mirror(andina):
    """Cross-company postings need both companies in the principal's scope (tenant/company row security)."""
    from qa_harness.andina import demo_context

    from kernel.uow import run_in_unit_of_work

    _setup_s10(andina)
    only_a = demo_context([andina.a], principal_id=andina.ctx.principal_id)
    before = andina.counts()
    with pytest.raises(Exception) as exc:
        run_in_unit_of_work(_ic_sale(andina), only_a)
    assert "company" in str(exc.value).lower() or getattr(exc.value, "status", 0) in (403, 404)
    assert andina.counts() == before


@pytest.mark.django_db
@pytest.mark.req("UR-01")
def test_p22b_S10_module_leg_intercompany_and_consolidation(andina):
    defects.require("PEND-P16", "PEND-P16C", "PEND-P10", "PEND-P11")
