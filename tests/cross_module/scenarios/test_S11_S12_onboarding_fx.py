"""S11 - Onboarding from spreadsheets [P21, P18, P05] and S12 - Foreign currency payable [P11, P16, P05]
(shared/11).

S11: a deterministic synthetic pack (``tests/cross_module/fixtures/onboarding/andina/``, CSV; see the
handoff for why not ``tests/fixtures``): 50 customers (5 with an invalid NIT DV), 30 suppliers, 200 items,
12 open AR invoices (45,000,000), 8 open AP invoices (22,000,000), bank 30,000,000 and 5 fixed assets.
The real ``localization_co.nit`` validator finds exactly the 5 invalid DVs and proposes the computed DV;
the opening entry posts on 2026-10-31 balanced against OPENING_BALANCE_EQUITY and every sub-ledger
reconciles with the GL. The agent, dry-run UI and batch confirmation are the P21/P18 leg.

S12: USD 1,000 at TRM 4,000 → AP 4,000,000; month-end TRM 4,100 → unrealized loss 100,000 auto-reversed on
day 1; payment at TRM 4,050 → realized loss 50,000. TRM from a labelled fixture adapter (P16's real adapter
is a module leg). Real ledger multi-currency balancing (invariant 1 per currency and in COP).
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from qa_harness import andina as andina_mod
from qa_harness import defects, golden, refdocs
from qa_harness.refdocs import Entry

from localization_co import nit

D = Decimal
PACK = Path(__file__).resolve().parents[1] / "fixtures" / "onboarding" / "andina"
OPENING = dt.date(2026, 10, 31)


def _read(name):
    with (PACK / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_p22b_S11_pack_shape():
    assert len(_read("customers.csv")) == 50 and len(_read("suppliers.csv")) == 30
    assert len(_read("items.csv")) == 200 and len(_read("fixed_assets.csv")) == 5
    ar, ap = _read("open_ar.csv"), _read("open_ap.csv")
    assert (len(ar), sum(D(r["amount"]) for r in ar)) == (12, D(45000000))
    assert (len(ap), sum(D(r["amount"]) for r in ap)) == (8, D(22000000))
    assert D(_read("bank.csv")[0]["balance"]) == D(30000000)
    accounts = _read("chart_of_accounts.csv")
    assert sum(1 for a in accounts if a["origin"] == "custom") == 40


def test_p22b_S11_invalid_dv_detected_and_fix_proposed():
    proposals = []
    for row in _read("customers.csv") + _read("suppliers.csv"):
        result = nit.validate(row["nit"], row["dv"])
        if not result.valid:
            proposals.append((row["code"], row["dv"], nit.check_digit(row["nit"])))
    assert len(proposals) == 5 and all(c.startswith("CLI-") for c, _, _ in proposals)
    for _code, given, proposed in proposals:
        assert int(given) != proposed and 0 <= proposed <= 9
    for row in _read("customers.csv"):  # the oracle agrees with the production validator
        assert golden.nit_check_digit(row["nit"]) == nit.check_digit(row["nit"])


@pytest.mark.django_db
def test_p22b_S11_opening_entry_balanced_and_subledgers_reconcile(andina):
    ar = sum(D(r["amount"]) for r in _read("open_ar.csv"))
    ap = sum(D(r["amount"]) for r in _read("open_ap.csv"))
    bank = D(_read("bank.csv")[0]["balance"])
    inventory = sum(D(r["qty"]) * D(r["unit_cost"]) for r in _read("items.csv"))
    assets = _read("fixed_assets.csv")
    cost = sum(D(a["cost"]) for a in assets)
    accum = sum(D(a["accumulated_depreciation"]) for a in assets)
    equity = ar + bank + inventory + cost - accum - ap
    lines = [("BANK", "DEBIT", bank), ("INVENTORY", "DEBIT", inventory), ("FIXED_ASSET_COST", "DEBIT", cost),
             ("ACCUM_DEPRECIATION", "CREDIT", accum)]  # fmt: skip
    lines += [("AR", "DEBIT", D(r["amount"]), {"party": andina_mod.party(r["customer"]), "party_kind": "customer"})
              for r in _read("open_ar.csv")]  # fmt: skip
    lines += [("AP", "CREDIT", D(r["amount"]), {"party": andina_mod.party(r["supplier"]), "party_kind": "vendor"})
              for r in _read("open_ap.csv")]  # fmt: skip
    lines.append(("OPENING_BALANCE_EQUITY", "CREDIT", equity))
    opening = andina.run(lambda u: refdocs.post_document(
        u, doc_type="onboarding.opening", company=andina.a, day=OPENING, total=equity,
        entries=[Entry("opening", lines)], payload={"pack": "andina (SINTÉTICO)", "batches_confirmed": 6}))  # fmt: skip
    (entry,) = andina.entries(opening.ref)
    assert entry.total_debit == entry.total_credit  # invariant 1
    assert andina.balance("AR", andina.a) == ar and andina.balance("AP", andina.a) == -ap  # invariant 4
    assert andina.balance("INVENTORY", andina.a) == inventory  # invariant 3 (opening layers = items pack)
    assert andina.balance("FIXED_ASSET_COST", andina.a) + andina.balance("ACCUM_DEPRECIATION", andina.a) == (
        cost - accum
    )  # invariant 5
    assert andina.balance("OPENING_BALANCE_EQUITY", andina.a) == -equity
    assert not [r for r, _, _ in andina.lines(opening.ref) if r == "ROUNDING"]


@pytest.mark.django_db
@pytest.mark.req("UR-18")
def test_p22b_S11_module_leg_agent_dry_run_and_first_invoice(andina):
    defects.require("PEND-P21", "PEND-P10", "PEND-P15")


# --------------------------------------------------------------------------- S12

INVOICE_DAY, MONTH_END, DAY_ONE, PAY_DAY = (dt.date(2026, 10, 15), dt.date(2026, 10, 31), dt.date(2026, 11, 1),
                                            dt.date(2026, 11, 20))  # fmt: skip
VENDOR = {"party": andina_mod.party("Proveedor del exterior (USD)"), "party_kind": "vendor"}


def _rates(w):
    for day, rate in ((INVOICE_DAY, "4000"), (MONTH_END, "4100"), (PAY_DAY, "4050")):
        andina_mod.add_rate(w, w.a, "USD", day, rate)


@pytest.mark.django_db
def test_p22b_S12_fx_payable_revaluation_reversal_and_payment(andina):
    a = andina.a
    _rates(andina)
    usd = {"currency": "USD"}
    invoice = andina.run(lambda u: refdocs.post_document(
        u, doc_type="purchase.invoice", company=a, day=INVOICE_DAY, total=D(4000000),
        entries=[Entry("invoice", [("EXPENSE", "DEBIT", D(1000), usd), ("AP", "CREDIT", D(1000), {**usd, **VENDOR})])]))  # fmt: skip
    assert andina.lines(invoice.ref) == [("EXPENSE", "Dr", D(4000000)), ("AP", "Cr", D(4000000))]

    reval_amount = golden.fx(D(1000), D(4100)) - golden.fx(D(1000), D(4000))
    reval = andina.run(lambda u: refdocs.post_document(
        u, doc_type="treasury.fx_revaluation", company=a, day=MONTH_END, total=reval_amount,
        entries=[Entry("revaluation", [("UNREALIZED_FX_LOSS", "DEBIT", reval_amount),
                                       ("AP", "CREDIT", reval_amount, VENDOR)])]))  # fmt: skip
    assert andina.lines(reval.ref) == [("UNREALIZED_FX_LOSS", "Dr", D(100000)), ("AP", "Cr", D(100000))]
    assert andina.balance("AP", a) == D(-4100000)

    from kernel import services
    from kernel.interfaces.posting import PostingService

    andina.run(lambda u: services.get(PostingService).reverse(u, reval.entries[0].entry_id, DAY_ONE,
                                                              "reversión automática día 1 (política)"))  # fmt: skip
    assert andina.balance("AP", a) == D(-4000000) and andina.balance("UNREALIZED_FX_LOSS", a) == 0

    realized = golden.fx(D(1000), D(4050)) - golden.fx(D(1000), D(4000))
    payment = andina.run(lambda u: refdocs.post_document(
        u, doc_type="treasury.payment", company=a, day=PAY_DAY, total=D(4050000),
        entries=[Entry("payment", [("AP", "DEBIT", D(1000), {**usd, "fx_rate": D(4000), **VENDOR}),
                                   ("FX_REALIZED_LOSS", "DEBIT", realized),
                                   ("BANK_USD", "CREDIT", D(1000), usd)])]))  # fmt: skip
    assert andina.lines(payment.ref) == [("AP", "Dr", D(4000000)), ("FX_REALIZED_LOSS", "Dr", D(50000)),
                                         ("BANK_USD", "Cr", D(4050000))]  # fmt: skip
    assert andina.balance("AP", a) == 0 and andina.balance("AP", a, currency="USD") == 0  # invariant 4 both ways
    (entry,) = andina.entries(payment.ref)
    assert entry.total_debit == entry.total_credit == D(4050000)  # invariant 1 in COP


@pytest.mark.django_db
@pytest.mark.req("UR-07")
def test_p22b_S12_missing_rate_fails_closed(andina):
    from kernel.errors import DomainError

    before = andina.counts()
    with pytest.raises(DomainError) as exc:
        andina.run(lambda u: refdocs.post_document(
            u, doc_type="purchase.invoice", company=andina.a, day=dt.date(2026, 2, 2), total=D(1),
            entries=[Entry("invoice", [("EXPENSE", "DEBIT", D(1), {"currency": "EUR"}),
                                       ("AP", "CREDIT", D(1), {"currency": "EUR", **VENDOR})])]))  # fmt: skip
    assert exc.value.code == "fx_rate_missing" and andina.counts() == before


@pytest.mark.django_db
@pytest.mark.req("UR-07")
def test_p22b_S12_module_leg_trm_adapter_and_revaluation_job(andina):
    defects.require("PEND-P16", "PEND-P11")
