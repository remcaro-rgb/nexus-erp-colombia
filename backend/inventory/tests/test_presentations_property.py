"""T-INV-PRES-02 — property: random receipts / issues / repacks keep Σ(packaged × factor) ≤ canonical and never
double count stock (real PostgreSQL, real StockService; the deferred DB constraint trigger checks it at commit too)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.models import Sum
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from inventory.models import ItemValuation, PackagedQuant, StockMove, StockQuant
from inventory.stock.service import SERVICE

pytestmark = pytest.mark.django_db
D = Decimal

OPS = st.lists(
    st.tuples(
        st.sampled_from(["receive_loose", "receive_box", "receive_six", "issue_loose", "issue_box", "pack", "unpack"]),
        st.integers(min_value=1, max_value=4),
        st.sampled_from(["ALMACEN", "DESPACHO"]),
    ),
    min_size=1,
    max_size=14,
)

_COUNTER = {"n": 0}


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(ops=OPS)
def test_T_INV_PRES_02_packaged_never_exceeds_canonical_and_stock_never_double_counted(inv_property, ops):
    inv, wh, box, six = inv_property["inv"], inv_property["wh"], inv_property["box"], inv_property["six"]
    _COUNTER["n"] += 1
    item = inv.item(f"P-PROP-{_COUNTER['n']}")
    box_p = inv.presentation(item, "CJ12", "12")
    six_p = inv.presentation(item, "SIX", "6")
    del box, six
    expected = D(0)
    for op, n, bin_code in ops:
        loc = inv.bin(wh, bin_code)
        try:
            if op == "receive_loose":
                inv.receive(inv.line(item, wh, str(n * 5), location=loc, cost="100"))
                expected += n * 5
            elif op == "receive_box":
                inv.receive(inv.line(item, wh, str(n * 12), location=loc, presentation=box_p, cost="100"))
                expected += n * 12
            elif op == "receive_six":
                inv.receive(inv.line(item, wh, str(n * 6), location=loc, presentation=six_p, cost="100"))
                expected += n * 6
            elif op == "issue_loose":
                inv.issue(inv.line(item, wh, str(n * 7), location=loc))
                expected -= n * 7
            elif op == "issue_box":
                inv.issue(inv.line(item, wh, str(n * 12), location=loc, presentation=box_p))
                expected -= n * 12
            else:
                inv.run(
                    lambda u, n=n, loc=loc, pack=(op == "pack"): SERVICE.repack(
                        u, inv.company(), item_id=item.id, location_id=loc.id, presentation_id=six_p.id, packages=D(n), pack=pack
                    )
                )
        except Exception as exc:  # refused operations must leave no trace (atomic UoW)
            assert getattr(exc, "code", None) in ("stock_insufficient", "packaged_exceeds_canonical"), exc
        with inv.scope():
            for q in StockQuant.objects.filter(item_id=item.id):
                assert q.on_hand >= 0 and 0 <= q.reserved <= q.on_hand
                packaged = sum(
                    (p.qty_packages * p.presentation.factor
                     for p in PackagedQuant.objects.filter(item_id=item.id, location_id=q.location_id).select_related("presentation")),
                    D(0),
                )  # fmt: skip
                assert packaged <= q.on_hand
            canonical = StockQuant.objects.filter(item_id=item.id).aggregate(t=Sum("on_hand"))["t"] or D(0)
            moves_in = StockMove.objects.filter(item_id=item.id, kind="RECEIPT").aggregate(t=Sum("quantity"))["t"] or D(0)
            moves_out = StockMove.objects.filter(item_id=item.id, kind="ISSUE").aggregate(t=Sum("quantity"))["t"] or D(0)
            val = ItemValuation.objects.filter(item_id=item.id).first()
        # total stock = Σ moves = expected; packaged stock is never added on top of the canonical quantity
        assert canonical == expected == moves_in - moves_out
        assert val is None or val.qty == canonical
        assert val is None or (val.qty != 0 or val.value == 0)


@pytest.fixture
def inv_property(inv):
    return {"inv": inv, "wh": inv.warehouse("WHP"), "box": None, "six": None}
