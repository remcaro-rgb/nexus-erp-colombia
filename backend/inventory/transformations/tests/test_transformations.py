"""T-INV-XFORM-01/02/03/04/05/07 — transformation engine: repack, portioning (direct + template), manual, BOM
production, ledger-failure rollback. Real PostgreSQL (forced RLS), real StockService, real PostingService."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum

from inventory.bom import service as boms
from inventory.models import ItemValuation, PackagedQuant, Reservation, StockMove, StockQuant
from inventory.transformations import service as xf
from inventory.transformations.models import TransformationLine, TransformationOrder
from kernel import services
from kernel.errors import DomainError
from kernel.interfaces.posting import PostingService
from kernel.money import round_half_up
from ledger.models import JournalEntry, JournalLine

pytestmark = pytest.mark.django_db
D = Decimal
L = xf.LineInput


def gl(inv: Any, source_id: Any) -> dict[str, tuple[Decimal, Decimal]]:
    """account code → (Σ debit, Σ credit) of the entries of a document."""
    with inv.scope():
        rows = (
            JournalLine.objects.filter(entry__source_id=source_id, entry__book__primary=True)
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: (r["d"], r["c"]) for r in rows}


def valuation(inv: Any, item: Any) -> tuple[Decimal, Decimal]:
    with inv.scope():
        v = ItemValuation.objects.filter(item_id=item.id).first()
        return (v.qty, v.value) if v else (D(0), D(0))


def on_hand(inv: Any, item: Any) -> Decimal:
    with inv.scope():
        return StockQuant.objects.filter(item_id=item.id).aggregate(t=Sum("on_hand"))["t"] or D(0)


def lines_of(inv: Any, o: Any) -> list[TransformationLine]:
    with inv.scope():
        return list(TransformationLine.objects.filter(order_id=o.id).order_by("line_no"))


# =========================================================================== XFORM-01


def test_T_INV_XFORM_01_repack_unpack_and_pack_canonical_stock_unchanged_no_entry(xw, inv, clerk, login):
    api = login(clerk)
    item = inv.item("AZUCAR-UND")
    box = inv.presentation(item, "CJ12", "12")
    alm = xw.bin()
    inv.receive(inv.line(item, xw.wh, "24", location=alm, presentation=box, cost="1500.25"))
    before_val = valuation(inv, item)
    with inv.scope():
        assert PackagedQuant.objects.get(presentation_id=box.id, location_id=alm.id).qty_packages == D(2)

    # unpack: 2 boxes × 12 → 24 units
    r = api.post(
        "/api/v1/inventory/transformations",
        {
            "transformation_type": "PRESENTATION",
            "warehouse_id": str(xw.wh.id),
            "document_date": "2026-11-09",
            "lines": [
                {
                    "line_no": 1,
                    "role": "INPUT",
                    "item_id": str(item.id),
                    "presentation_id": str(box.id),
                    "qty_packages": "2",
                },
                {"line_no": 2, "role": "OUTPUT", "item_id": str(item.id), "quantity": {"value": "24", "uom": "UND"}},
            ],
        },
    )
    assert r.status_code == 201, r.content
    unpack = r.json()
    assert unpack["number"].startswith("TRF-") and unpack["status"] == "DRAFT"
    assert unpack["lines"][0]["quantity"]["value"] == "24.000000"
    r = api.post(f"/api/v1/inventory/transformations/{unpack['id']}:post")
    assert r.status_code == 200 and r.json()["status"] == "POSTED", r.content
    assert r.json()["journal_entry_id"] is None
    with inv.scope():
        assert PackagedQuant.objects.get(presentation_id=box.id, location_id=alm.id).qty_packages == D(0)
        assert not StockMove.objects.filter(source_id=unpack["id"]).exists()
    assert on_hand(inv, item) == D(24) and valuation(inv, item) == before_val

    # pack back: 24 loose units → 2 boxes
    r = api.post(
        "/api/v1/inventory/transformations",
        {
            "transformation_type": "PRESENTATION",
            "warehouse_id": str(xw.wh.id),
            "lines": [
                {"line_no": 1, "role": "INPUT", "item_id": str(item.id), "quantity": "24"},
                {
                    "line_no": 2,
                    "role": "OUTPUT",
                    "item_id": str(item.id),
                    "presentation_id": str(box.id),
                    "qty_packages": "2",
                },
            ],
        },
    )
    assert r.status_code == 201, r.content
    pack = r.json()
    r = api.post(f"/api/v1/inventory/transformations/{pack['id']}:post")
    assert r.status_code == 200 and r.json()["status"] == "POSTED", r.content
    with inv.scope():
        assert PackagedQuant.objects.get(presentation_id=box.id, location_id=alm.id).qty_packages == D(2)
    assert on_hand(inv, item) == D(24) and valuation(inv, item) == before_val
    # within one valuation account: no journal entry for either document
    assert gl(inv, unpack["id"]) == {} and gl(inv, pack["id"]) == {}

    # packing more than the loose stock is refused (packages never exceed canonical stock, D-013)
    o = xw.order(
        "PRESENTATION", [L("INPUT", item.id, D(12)), L("OUTPUT", item.id, qty_packages=D(1), presentation_id=box.id)]
    )
    with pytest.raises(DomainError) as exc:
        xw.post(o)
    assert exc.value.code in ("stock_insufficient", "packaged_exceeds_canonical")

    # reversal of the pack opens the boxes again; still no entry and no value change
    r = api.post(f"/api/v1/inventory/transformations/{pack['id']}:reverse", {"reason": "error de empaque"})
    assert r.status_code == 200 and r.json()["status"] == "REVERSED", r.content
    with inv.scope():
        assert PackagedQuant.objects.get(presentation_id=box.id, location_id=alm.id).qty_packages == D(0)
    assert on_hand(inv, item) == D(24) and valuation(inv, item) == before_val

    # repack rules are derived from the presentation factors
    r = api.get("/api/v1/inventory/repack-rules", item_id=str(item.id))
    assert r.status_code == 200, r.content
    pairs = {(x["from_presentation_id"], x["to_presentation_id"]) for x in r.json()["items"]}
    assert pairs == {(str(box.id), None), (None, str(box.id))}


# =========================================================================== XFORM-02


def test_T_INV_XFORM_02_portioning_direct_actual_weights_waste_values_balance_rounding_to_main(xw, inv):
    kg = {"base_uom": "KG"}
    res = inv.item("RES-CANAL", **kg)
    lomo = inv.item("LOMO", standard_cost="40000", **kg)
    molida = inv.item("MOLIDA", standard_cost="18000", **kg)
    hueso = inv.item("HUESO", standard_cost="3000", **kg)
    xw.stock(res, "10", "23456.789")  # 234 567.8900
    value_in = valuation(inv, res)[1]
    assert value_in == D("234567.8900")

    o = xw.order(
        "PORTIONING",
        [
            L("INPUT", res.id, D("10")),
            L("OUTPUT", lomo.id, D("3.250"), actual_weight=D("3.250"), is_main=True),
            L("OUTPUT", molida.id, D("4.100"), actual_weight=D("4.100")),
            L("OUTPUT", hueso.id, D("2.150"), actual_weight=D("2.150")),
            L("WASTE", res.id, D("0.400")),  # normal waste: absorbed by the outputs
            L("WASTE", res.id, D("0.100"), abnormal=True),  # abnormal waste: expensed
        ],
    )
    with inv.scope():
        plan = xf.simulate(o)
    assert plan.balanced and plan.allocation_basis == "standard_value" and not plan.approval_required
    o = xw.post(o)
    assert o.status == "POSTED"
    waste = round_half_up(D("0.100") * value_in / D(10), 4)
    assert o.waste_expensed == waste == D("2345.6789")
    distributable = value_in - waste
    weights = {"LOMO": D("3.250") * 40000, "MOLIDA": D("4.100") * 18000, "HUESO": D("2.150") * 3000}
    wsum = sum(weights.values())
    expected_molida = round_half_up(distributable * weights["MOLIDA"] / wsum, 4)
    expected_hueso = round_half_up(distributable * weights["HUESO"] / wsum, 4)
    by_item = {ln.item_id: ln for ln in lines_of(inv, o) if ln.role == "OUTPUT"}
    assert by_item[molida.id].value == expected_molida
    assert by_item[hueso.id].value == expected_hueso
    assert by_item[lomo.id].value == distributable - expected_molida - expected_hueso  # remainder on the main output
    # values balance exactly: Σ outputs + expensed waste = Σ inputs (no additional costs)
    assert o.value_out + o.waste_expensed == o.value_in == value_in
    # stock and valuation in-line: input fully consumed, outputs valued at their allocation
    assert valuation(inv, res) == (D(0), D(0)) and on_hand(inv, res) == D(0)
    for item, ln in ((lomo, by_item[lomo.id]), (molida, by_item[molida.id]), (hueso, by_item[hueso.id])):
        assert valuation(inv, item) == (ln.quantity, ln.value)
    # GL: one valuation account → only the abnormal waste posts (Dr waste expense / Cr inventory)
    assert gl(inv, o.id) == {"529505": (waste, D(0)), "143505": (D(0), waste)}

    # unbalanced base quantities are refused (no stock from nothing)
    xw.stock(res, "5", "20000")
    bad = xw.order("PORTIONING", [L("INPUT", res.id, D("5")), L("OUTPUT", lomo.id, D("5.200"))])
    with pytest.raises(DomainError) as exc:
        xw.post(bad)
    assert exc.value.code == "transformation_unbalanced_value"
    assert on_hand(inv, res) == D(5)


# =========================================================================== XFORM-03


def _template(xw: Any, inv: Any, res: Any, lomo: Any, molida: Any, hueso: Any) -> Any:
    data = xf.TemplateInput(
        code="DESPOSTE-RES",
        name="Desposte res (SINTÉTICO)",
        input_item_id=res.id,
        outputs=(
            xf.TemplateOutputInput(lomo.id, D("32"), D("50"), is_main=True),
            xf.TemplateOutputInput(molida.id, D("41"), D("40")),
            xf.TemplateOutputInput(hueso.id, D("22"), D("10")),
        ),
        tolerance_percent=D("2"),
        waste_percent=D("5"),
    )
    return xw.run(lambda u: xf.create_template(u, inv.company(), data))


def test_T_INV_XFORM_03_portioning_template_outside_tolerance_requires_approval_inside_posts(
    xw, inv, clerk, approver, login
):
    kg = {"base_uom": "KG"}
    res, lomo = inv.item("RES-T", **kg), inv.item("LOMO-T", **kg)
    molida, hueso = inv.item("MOLIDA-T", **kg), inv.item("HUESO-T", **kg)
    tpl = _template(xw, inv, res, lomo, molida, hueso)
    xw.stock(res, "20", "21000.1234")

    # inside ±2 %: 33 / 40 / 22 / 5 → posts directly from DRAFT
    inside = xw.order(
        "PORTIONING",
        [
            L("INPUT", res.id, D("10")),
            L("OUTPUT", lomo.id, D("3.300")),
            L("OUTPUT", molida.id, D("4.000")),
            L("OUTPUT", hueso.id, D("2.200")),
            L("WASTE", res.id, D("0.500")),
        ],
        template_id=tpl.id,
    )
    inside = xw.post(inside)
    assert inside.status == "POSTED" and not inside.approval_required
    outs = {ln.item_id: ln.value for ln in lines_of(inv, inside) if ln.role == "OUTPUT"}
    total = inside.value_in
    assert outs[molida.id] == round_half_up(total * D("0.40"), 4)
    assert outs[hueso.id] == round_half_up(total * D("0.10"), 4)
    assert outs[lomo.id] == total - outs[molida.id] - outs[hueso.id]
    assert inside.waste_expensed == D(0)  # 5 % waste is within template waste + tolerance (normal, absorbed)

    # outside: LOMO 29 % (−3) and MOLIDA 45 % (+4) → needs an approval with a reason (D-015)
    api, boss = login(clerk), login(approver)
    r = api.post(
        "/api/v1/inventory/transformations",
        {
            "transformation_type": "PORTIONING",
            "warehouse_id": str(xw.wh.id),
            "template_id": str(tpl.id),
            "lines": [
                {"line_no": 1, "role": "INPUT", "item_id": str(res.id), "quantity": "10"},
                {"line_no": 2, "role": "OUTPUT", "item_id": str(lomo.id), "quantity": "2.9", "actual_weight": "2.9"},
                {"line_no": 3, "role": "OUTPUT", "item_id": str(molida.id), "quantity": "4.5"},
                {"line_no": 4, "role": "OUTPUT", "item_id": str(hueso.id), "quantity": "2.1"},
                {"line_no": 5, "role": "WASTE", "item_id": str(res.id), "quantity": "0.5"},
            ],
        },
    )
    assert r.status_code == 201, r.content
    oid = r.json()["id"]
    r = api.post(f"/api/v1/inventory/transformations/{oid}:simulate")
    assert r.status_code == 200, r.content
    sim = r.json()
    assert sim["approval_required"] is True and sim["balanced"] is True
    assert [c["within"] for c in sim["yield_checks"]] == [False, False, True]
    r = api.post(f"/api/v1/inventory/transformations/{oid}:post")
    assert r.status_code == 422 and r.json()["code"] == "transformation_yield_out_of_tolerance", r.content
    assert on_hand(inv, res) == D(10)  # nothing consumed
    r = api.post(f"/api/v1/inventory/transformations/{oid}:submit")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED" and r.json()["approval_required"], r.content
    r = api.post(f"/api/v1/inventory/transformations/{oid}:approve", {"reason": "canal magra"})
    assert r.status_code == 409 and r.json()["code"] == "sod_violation", r.content  # requester never approves
    r = boss.post(f"/api/v1/inventory/transformations/{oid}:approve")
    assert r.status_code == 422, r.content  # a reason is mandatory outside tolerance
    r = boss.post(f"/api/v1/inventory/transformations/{oid}:approve", {"reason": "canal magra, verificado"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.content
    r = api.post(f"/api/v1/inventory/transformations/{oid}:post")
    assert r.status_code == 200 and r.json()["status"] == "POSTED", r.content
    assert on_hand(inv, res) == D(0)


# =========================================================================== XFORM-04


def test_T_INV_XFORM_04_manual_multi_input_output_balances_value_and_quantities_outputs_without_inputs_rejected(
    xw, inv, world, clerk, login, issuer
):
    a, b = inv.item("MAN-A"), inv.item("MAN-B")
    c, d = inv.item("MAN-C"), inv.item("MAN-D")
    xw.stock(a, "5", "1000")
    xw.stock(b, "3", "2000")
    api = login(clerk)
    payload = {
        "transformation_type": "MANUAL",
        "warehouse_id": str(xw.wh.id),
        "additional_costs": {"amount": "500", "currency": "COP"},
        "reason": "ensamble de canastas (SINTÉTICO)",
        "lines": [
            {"line_no": 1, "role": "INPUT", "item_id": str(a.id), "quantity": "5"},
            {"line_no": 2, "role": "INPUT", "item_id": str(b.id), "quantity": "3"},
            {"line_no": 3, "role": "OUTPUT", "item_id": str(c.id), "quantity": "6", "is_main": True},
            {"line_no": 4, "role": "OUTPUT", "item_id": str(d.id), "quantity": "2"},
        ],
    }
    r = api.post("/api/v1/inventory/transformations", payload)
    assert r.status_code == 201, r.content
    oid = r.json()["id"]
    r = api.post(f"/api/v1/inventory/transformations/{oid}:post")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "POSTED"
    assert body["value_in"]["amount"] == "11000.0000" and body["value_out"]["amount"] == "11500.0000"
    vals = {ln["item_id"]: ln["value"]["amount"] for ln in body["lines"] if ln["role"] == "OUTPUT"}
    assert vals == {str(d.id): "2875.0000", str(c.id): "8625.0000"}  # by quantity, remainder on the main output
    # base quantities: inputs consumed exactly, outputs created exactly
    assert (on_hand(inv, a), on_hand(inv, b), on_hand(inv, c), on_hand(inv, d)) == (D(0), D(0), D(6), D(2))
    assert valuation(inv, c) == (D(6), D("8625.0000")) and valuation(inv, d) == (D(2), D("2875.0000"))
    # GL: inventory nets to + additional costs; Dr INVENTORY / Cr cost clearing
    assert gl(inv, oid) == {"143505": (D(500), D(0)), "239595": (D(0), D(500))}

    # outputs without inputs → transformation_no_inputs (no stock from nothing)
    r = api.post(
        "/api/v1/inventory/transformations",
        {**payload, "lines": [{"line_no": 1, "role": "OUTPUT", "item_id": str(c.id), "quantity": "1"}]},
    )
    assert r.status_code == 422 and r.json()["code"] == "transformation_no_inputs", r.content
    # base quantities must balance (same UoM)
    xw.stock(a, "4", "1000")
    o = xw.order("MANUAL", [L("INPUT", a.id, D(4)), L("OUTPUT", c.id, D(3))])
    with pytest.raises(DomainError) as exc:
        xw.post(o)
    assert exc.value.code == "transformation_unbalanced_value"
    # MANUAL needs its own permission
    world.add_role(
        "inv_no_manual", ["inventory.*.*"], denies=["inventory.transformation_manual.*"], max_cls="confidential"
    )
    world.add_user("operario", ["ALFA"], subject="operario-subject")
    world.assign("operario", "inv_no_manual", "ALFA")
    r = login("operario").post("/api/v1/inventory/transformations", payload)
    assert r.status_code == 403, r.content


# =========================================================================== XFORM-05


def test_T_INV_XFORM_05_production_from_bom_v2_consumes_components_with_scrap_byproduct_by_cost_share(xw, inv):
    kg = {"base_uom": "KG"}
    pan, harina = inv.item("PAN-TAJ"), inv.item("HARINA-P", **kg)
    sal, salvado = inv.item("SAL-P", **kg), inv.item("SALVADO", **kg)
    xw.stock(harina, "50", "2400.125")
    xw.stock(sal, "2", "1300")
    v1 = xw.activate(xw.bom("R-PAN-TAJ", pan, "10", [(harina, "5", "0")], valid_from=dt.date(2026, 1, 1)))
    v2 = xw.run(lambda u: boms.copy_version(u, v1, valid_from=dt.date(2026, 11, 1)))
    data = boms.BomInput(
        code="R-PAN-TAJ",
        name="Pan tajado v2 (SINTÉTICO)",
        item_id=pan.id,
        valid_from=dt.date(2026, 11, 1),
        output_qty=D("10"),
        lines=(
            boms.LineInput(harina.id, D("4"), scrap_percent=D("5")),
            boms.LineInput(sal.id, D("0.1")),
            boms.LineInput(salvado.id, D("0.5"), is_byproduct=True, cost_share_percent=D("10")),
        ),
    )
    v2 = xw.run(lambda u: boms.update(u, v2, data))
    v2 = xw.activate(v2, supersede=True)
    with inv.scope():
        assert boms.active_bom(inv.company(), pan.id, dt.date(2026, 11, 9)).id == v2.id

    o = xw.order("PRODUCTION", [], bom_id=v2.id, planned_qty=D("20"))
    lines = {(ln.role, ln.item_id): ln for ln in lines_of(inv, o)}
    assert lines[("INPUT", harina.id)].quantity == D("8.400")  # 4 × 2 batches × 1.05 scrap
    assert lines[("INPUT", sal.id)].quantity == D("0.200")
    assert lines[("OUTPUT", pan.id)].is_main and lines[("OUTPUT", pan.id)].cost_share == D(90)
    assert lines[("OUTPUT", salvado.id)].is_byproduct and lines[("OUTPUT", salvado.id)].quantity == D("1.000")

    o = xw.post(o)
    value_in = o.value_in
    with inv.scope():
        issued = {m.item_id: m for m in StockMove.objects.filter(source_id=o.id, kind="ISSUE")}
    assert issued[harina.id].quantity == D("8.4") and issued[sal.id].quantity == D("0.2")
    assert value_in == -(issued[harina.id].value + issued[sal.id].value)
    posted = {ln.item_id: ln for ln in lines_of(inv, o) if ln.role == "OUTPUT"}
    assert posted[salvado.id].value == round_half_up(value_in * D("0.10"), 4)
    assert posted[pan.id].value == value_in - posted[salvado.id].value
    assert valuation(inv, pan) == (D(20), posted[pan.id].value)
    assert valuation(inv, salvado) == (D(1), posted[salvado.id].value)
    assert on_hand(inv, harina) == D("41.6") and gl(inv, o.id) == {}

    # reversal at original cost: components back, outputs out, no value created or lost
    o = xw.run(lambda u: xf.reverse(u, o, "orden duplicada"))
    assert o.status == "REVERSED"
    assert on_hand(inv, harina) == D(50) and on_hand(inv, pan) == D(0) and on_hand(inv, salvado) == D(0)
    assert valuation(inv, harina) == (D(50), D("120006.2500"))


# =========================================================================== XFORM-07


class FailingLedger:
    """Wraps the real PostingService and fails inside ``post`` (after the stock moves were written)."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls = 0

    def post(self, uow: Any, request: Any) -> Any:
        self.calls += 1
        raise DomainError("ledger unavailable (injected failure)", code="ledger_failure_injected")

    def simulate(self, uow: Any, request: Any) -> Any:
        return self.real.simulate(uow, request)

    def reverse(self, uow: Any, entry_id: Any, reversal_date: Any, reason: str) -> Any:
        return self.real.reverse(uow, entry_id, reversal_date, reason)


def test_T_INV_XFORM_07_ledger_failure_during_post_rolls_back_all_stock_moves(xw, inv):
    kg = {"base_uom": "KG"}
    pollo, pechuga, alas = inv.item("POLLO", **kg), inv.item("PECHUGA", **kg), inv.item("ALAS", **kg)
    xw.stock(pollo, "12", "9876.54321")
    o = xw.order(
        "PORTIONING",
        [
            L("INPUT", pollo.id, D("10")),
            L("OUTPUT", pechuga.id, D("6"), is_main=True),
            L("OUTPUT", alas.id, D("3.5")),
            L("WASTE", pollo.id, D("0.5"), abnormal=True),
        ],
        additional_costs=D("1500"),
    )
    o = xw.run(lambda u: xf.reserve(u, o))
    before: dict[str, Any] = {"quants": on_hand(inv, pollo), "val": valuation(inv, pollo)}
    with inv.scope():
        before["moves"] = StockMove.objects.count()
        before["entries"] = JournalEntry.objects.count()
        [res] = Reservation.objects.filter(source_id=o.id, state="ACTIVE")
    failing = FailingLedger(services.get(PostingService))
    with services.override(PostingService, failing), pytest.raises(DomainError) as exc:
        xw.post(o)
    assert exc.value.code == "ledger_failure_injected" and failing.calls == 1
    with inv.scope():
        # no partial stock, valuation or ledger; the reservation from :reserve survives, the order is unchanged
        assert StockMove.objects.count() == before["moves"]
        assert not StockMove.objects.filter(source_id=o.id).exists()
        assert JournalEntry.objects.count() == before["entries"]
        assert Reservation.objects.get(id=res.id).state == "ACTIVE"
        order = TransformationOrder.objects.get(id=o.id)
        assert order.status == "DRAFT" and order.inputs_reserved and order.journal_entry_id is None
        assert not StockQuant.objects.filter(item_id__in=[pechuga.id, alas.id], on_hand__gt=0).exists()
    assert on_hand(inv, pollo) == before["quants"] and valuation(inv, pollo) == before["val"]
    assert valuation(inv, pechuga) == (D(0), D(0))

    # a real ledger error (missing account determination) rolls back the same way
    with inv.scope():
        from ledger.models import AccountDetermination

        AccountDetermination.objects.filter(role_code="SCRAP_EXPENSE").update(active=False)
    with pytest.raises(DomainError) as exc:
        xw.post(o)
    assert exc.value.code == "account_determination_missing"
    assert on_hand(inv, pollo) == D(12) and on_hand(inv, pechuga) == D(0)
    with inv.scope():
        AccountDetermination.objects.filter(role_code="SCRAP_EXPENSE").update(active=True)

    # with the ledger back the same document posts once, consuming its reservation
    o = xw.post(o)
    assert o.status == "POSTED" and o.journal_entry_id is not None
    assert on_hand(inv, pollo) == D(2) and o.value_out + o.waste_expensed == o.value_in + D(1500)
    with inv.scope():
        assert Reservation.objects.get(id=res.id).state == "CONSUMED"
    entry = gl(inv, o.id)
    assert entry["529505"] == (o.waste_expensed, D(0)) and entry["239595"] == (D(0), D(1500))
