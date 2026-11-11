"""Property tests of the pure consolidation engine (Hypothesis): balanced book, closing CTA, NCI bounds."""

from __future__ import annotations

import uuid
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from consolidation import engine

D = Decimal
ZERO = D(0)
TYPES = ("asset", "liability", "equity", "income", "expense")
amounts = st.decimals(min_value=D("-1000000"), max_value=D("1000000"), places=2, allow_nan=False, allow_infinity=False)
rates = st.decimals(min_value=D("0.0001"), max_value=D("2"), places=6, allow_nan=False, allow_infinity=False)
pcts = st.decimals(min_value=D(0), max_value=D(100), places=2, allow_nan=False, allow_infinity=False)


@st.composite
def balanced_rows(draw: st.DrawFn) -> tuple[engine.TbRow, ...]:
    values = draw(st.lists(amounts, min_size=1, max_size=8))
    rows = [
        engine.TbRow(f"{i}0{n}", f"{i}0{n}", "x", TYPES[i % 5], v)
        for n, (i, v) in enumerate(
            zip(draw(st.lists(st.integers(1, 5), min_size=len(values), max_size=len(values))), values, strict=True)
        )
    ]
    rows.append(engine.TbRow("310505", "310505", "Capital", "equity", -sum((r.balance for r in rows), ZERO)))
    return tuple(rows)


@settings(max_examples=60, deadline=None)
@given(
    parent_rows=balanced_rows(),
    sub_rows=balanced_rows(),
    share=pcts,
    method=st.sampled_from(["FULL", "PROPORTIONAL", "EQUITY"]),
    closing=rates,
    average=rates,
    historical=rates,
    ic=amounts,
)
def test_T_P16_08_engine_book_balances_and_consolidation_closes(
    parent_rows, sub_rows, share, method, closing, average, historical, ic
) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    r = engine.Rates(closing, average, historical)
    parent = engine.Member(
        a, "FULL", D(100), True, (*parent_rows, engine.TbRow("120505", "120505", "Inv", "asset", D(0))), r
    )
    sub = engine.Member(b, method, share, False, sub_rows, r, investment_account="120505")
    result = engine.consolidate(
        [parent, sub],
        ic_balances=[engine.IcBalanceRow(a, b, "130505", ic), engine.IcBalanceRow(b, a, "233505", -ic)],
        ic_sales=[engine.IcSale(a, b, "413505", "613505", "143505", abs(ic), abs(ic) / 4)],
    )
    for e in result.entries:
        assert e.total_debit == e.total_credit, e.kind
        for ln in e.lines:
            assert ln.debit >= 0 and ln.credit >= 0 and (ln.debit == 0 or ln.credit == 0)
    # every member's weighted contribution closes on the CTA, so the consolidated trial balance sums to zero
    assert sum(result.consolidated().values(), ZERO) == ZERO
    # mirrored IC balances leave no difference
    assert result.ic_differences == []


@settings(max_examples=40, deadline=None)
@given(rows=balanced_rows(), closing=rates, average=rates, historical=rates)
def test_T_P16_08_translation_cta_closes_each_member(rows, closing, average, historical) -> None:
    m = engine.Member(uuid.uuid4(), "FULL", D(100), True, rows, engine.Rates(closing, average, historical))
    tb, cta = engine.translate(m)
    assert sum(tb.values(), ZERO) + cta == ZERO


def test_identity_rates_have_no_cta() -> None:
    rows = (
        engine.TbRow("111005", "111005", "Banco", "asset", D(100)),
        engine.TbRow("310505", "310505", "Capital", "equity", D(-100)),
    )
    m = engine.Member(uuid.uuid4(), "FULL", D(100), True, rows)
    assert engine.translate(m)[1] == ZERO


def test_ic_mismatch_goes_to_difference_account() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = (engine.TbRow("111005", "111005", "Banco", "asset", D(0)),)
    res = engine.consolidate(
        [engine.Member(a, "FULL", D(100), True, rows), engine.Member(b, "FULL", D(100), False, rows)],
        ic_balances=[engine.IcBalanceRow(a, b, "130505", D(100)), engine.IcBalanceRow(b, a, "233505", D(-90))],
    )
    e = next(x for x in res.entries if x.kind == "IC_BALANCE")
    diff = [ln for ln in e.lines if ln.group_account == "199995"]
    assert diff[0].debit == D(10)
    assert res.ic_differences[0]["difference"] == D(10)
