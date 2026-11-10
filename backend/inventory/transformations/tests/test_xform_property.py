"""T-INV-XFORM-08 — property: random receipts and random transformations (portioning with normal/abnormal waste and
additional costs, manual multi-item, approved by a second user above the threshold) keep the value invariant and never
create negative stock.

Invariant after every step (company-wide valuation, D-016):
    Σ inventory value (valuation rows) + Σ expensed waste = Σ received value + Σ additional costs
and per document ``value_out + expensed waste = value_in + additional costs``; Σ quants = valuation qty per item;
no quant below zero; a rejected document (insufficient stock / unbalanced) changes nothing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from inventory.models import ItemValuation, StockQuant
from inventory.transformations import service as xf
from kernel.errors import DomainError
from kernel.money import q_amount

pytestmark = pytest.mark.django_db
D = Decimal
L = xf.LineInput

STEP = st.one_of(
    st.tuples(
        st.just("receive"),
        st.integers(min_value=1, max_value=40_000),  # grams → kg with 3 decimals
        st.integers(min_value=1_000_000, max_value=4_000_000_000),  # unit cost × 10⁴
    ),
    st.tuples(
        st.just("portion"),
        st.integers(min_value=500, max_value=30_000),  # input grams
        st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=3),  # output weights (relative)
        st.integers(min_value=0, max_value=15),  # waste % of the input
        st.booleans(),  # waste abnormal?
        st.integers(min_value=0, max_value=50_000),  # additional costs COP
    ),
    st.tuples(
        st.just("manual"),
        st.integers(min_value=1_000, max_value=20_000),
        st.integers(min_value=1, max_value=9),  # split of the output
    ),
)

_N = {"n": 0}


@settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow]
)
@given(steps=st.lists(STEP, min_size=2, max_size=8))
def test_T_INV_XFORM_08_random_transformations_value_invariant_and_no_negative_stock(
    xw: Any, inv: Any, approver: Any, world: Any, steps: Any
) -> None:
    boss = world.users[approver].id
    _N["n"] += 1
    n = _N["n"]
    kg = {"base_uom": "KG"}
    raw = inv.item(f"PROP-RAW-{n}", **kg)
    outs = [inv.item(f"PROP-OUT-{n}-{k}", **kg) for k in range(3)]
    side = inv.item(f"PROP-SIDE-{n}", **kg)
    items = [raw, *outs, side]
    received = D(0)
    added = D(0)
    expensed = D(0)

    def state() -> tuple[Decimal, dict[Any, Decimal], dict[Any, Decimal], Decimal]:
        with inv.scope():
            vals = {v.item_id: v for v in ItemValuation.objects.filter(item_id__in=[i.id for i in items])}
            quants = {
                r["item_id"]: r["t"]
                for r in StockQuant.objects.filter(item_id__in=[i.id for i in items])
                .values("item_id")
                .annotate(t=Sum("on_hand"))
            }
            low = StockQuant.objects.filter(item_id__in=[i.id for i in items]).order_by("on_hand").first()
        total = q_amount(sum((v.value for v in vals.values()), D(0)))
        return total, {k: v.qty for k, v in vals.items()}, quants, (low.on_hand if low else D(0))

    for step in steps:
        before = state()
        try:
            if step[0] == "receive":
                qty = D(step[1]) / 1000
                res = xw.stock(raw, format(qty, "f"), format(D(step[2]) / 10_000, "f"))
                received += res.total_value
            elif step[0] == "portion":
                _, grams, rel, waste_pct, abnormal, extra = step
                qty_in = D(grams) / 1000
                waste = (qty_in * waste_pct / 100).quantize(D("0.001"))
                rest = qty_in - waste
                parts = [(rest * r / sum(rel)).quantize(D("0.001")) for r in rel]
                parts[0] += rest - sum(parts)  # exact mass balance
                if any(p <= 0 for p in parts):
                    continue
                lines = [L("INPUT", raw.id, qty_in)]
                lines += [L("OUTPUT", outs[k].id, p, is_main=k == 0) for k, p in enumerate(parts)]
                if waste > 0:
                    lines.append(L("WASTE", raw.id, waste, abnormal=abnormal))
                o = xw.post(xw.order("PORTIONING", lines, additional_costs=D(extra)))
                assert o.value_out + o.waste_expensed == o.value_in + D(extra)
                added += D(extra)
                expensed += o.waste_expensed
            else:
                _, grams, split = step
                qty = D(grams) / 1000
                a = (qty * split / 10).quantize(D("0.001"))
                b = qty - a
                if a <= 0 or b <= 0:
                    continue
                o = xw.order(
                    "MANUAL",
                    [L("INPUT", raw.id, qty), L("OUTPUT", side.id, a, is_main=True), L("OUTPUT", outs[2].id, b)],
                )
                o = xw.run(lambda u, o=o: xf.submit(u, o))
                if o.status == "SUBMITTED":  # above the manual threshold: a second user approves (SoD, D-023)
                    o = xw.run(lambda u, o=o: xf.approve(u, o, "verificado (SINTÉTICO)"), boss)
                o = xw.post(o)
                assert o.value_out == o.value_in
        except DomainError as exc:
            assert exc.code in ("stock_insufficient", "transformation_unbalanced_value"), exc
            assert state() == before  # a refused document leaves no trace
        total, val_qty, quants, lowest = state()
        assert total + expensed == q_amount(received + added), (step, total, expensed, received, added)
        assert lowest >= 0
        for item_id, qty in quants.items():
            assert val_qty.get(item_id, D(0)) == qty
