"""T-INV-BOM-01 / T-INV-BOM-02 — recipes / BOM: cycle and version-overlap checks; exact, costed explosion."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from inventory.bom import core
from inventory.bom import service as boms
from inventory.bom.models import Bom
from inventory.models import Item, Status, StockMove
from inventory.stock.service import SERVICE
from kernel import ids
from kernel.errors import DomainError
from kernel.interfaces.common import DocumentRef

pytestmark = pytest.mark.django_db
D = Decimal


def _code(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


def test_T_INV_BOM_01_cycles_across_multi_level_boms_and_overlapping_versions_rejected(
    xw: Any, inv: Any, clerk: Any, login: Any
) -> None:
    # ---- pure graph checks
    a, b, c = (uuid.uuid4() for _ in range(3))
    assert core.find_cycle({a: {b}, b: {c}}, a) is None
    assert core.find_cycle({a: {b}, b: {c}, c: {a}}, a) == [a, b, c, a]
    assert core.overlaps(dt.date(2026, 1, 1), None, dt.date(2026, 6, 1), None)
    assert not core.overlaps(dt.date(2026, 1, 1), dt.date(2026, 5, 31), dt.date(2026, 6, 1), None)
    assert core.overlaps(dt.date(2026, 6, 1), dt.date(2026, 6, 1), dt.date(2026, 1, 1), dt.date(2026, 6, 1))

    # ---- multi-level: A ← B ← C (active) then C ← A closes the cycle three levels down
    fin, sub, raw = xw.inv.item("BOM-A"), xw.inv.item("BOM-B"), xw.inv.item("BOM-C")
    xw.activate(xw.bom("R-A", fin, "1", [(sub, "2", "0")]))
    xw.activate(xw.bom("R-B", sub, "1", [(raw, "3", "0")]))
    closing = xw.bom("R-C", raw, "1", [(fin, "1", "0")])
    with pytest.raises(DomainError) as exc:
        xw.activate(closing)
    assert _code(exc) == "bom_cycle" and "BOM-C" in exc.value.detail and "BOM-A" in exc.value.detail
    with inv.scope():
        assert Bom.objects.get(id=closing.id).bom_status == "DRAFT"  # rolled back, still a draft
    with pytest.raises(DomainError) as exc:  # direct self-reference is refused at creation
        xw.bom("R-SELF", fin, "1", [(fin, "1", "0")])
    assert _code(exc) == "bom_cycle"

    # ---- the API reports the stable code (RFC 9457)
    api = login(clerk)
    r = api.post(f"/api/v1/inventory/boms/{closing.id}:activate")
    assert r.status_code == 422 and r.json()["code"] == "bom_cycle", r.content

    # ---- versions: v1 open-ended from Jan 1; v2 from Jun 1 overlaps → rejected, unless it supersedes v1
    out = xw.inv.item("BOM-V")
    v1 = xw.activate(xw.bom("R-V", out, "10", [(raw, "4", "0")]))
    v2 = xw.run(lambda u: boms.copy_version(u, v1, valid_from=dt.date(2026, 6, 1)))
    assert (v1.version_no, v2.version_no, v2.bom_status) == (1, 2, "DRAFT")
    with pytest.raises(DomainError) as exc:
        xw.activate(v2)
    assert _code(exc) == "bom_version_overlap"
    r = api.post(f"/api/v1/inventory/boms/{v2.id}:activate", {"supersede": True})
    assert r.status_code == 200 and r.json()["bom_status"] == "ACTIVE", r.content
    with inv.scope():
        assert Bom.objects.get(id=v1.id).valid_to == dt.date(2026, 5, 31)
        assert getattr(boms.active_bom(inv.company(), out.id, dt.date(2026, 5, 31)), "id", None) == v1.id
        assert getattr(boms.active_bom(inv.company(), out.id, dt.date(2026, 6, 1)), "id", None) == v2.id
    # v3 inside v1's (now closed) range overlaps v1 → rejected even with supersede (v1 is not open-ended)
    v3 = xw.run(lambda u: boms.copy_version(u, v1, valid_from=dt.date(2026, 3, 1), valid_to=dt.date(2026, 4, 30)))
    with pytest.raises(DomainError) as exc:
        xw.activate(v3, supersede=True)
    assert _code(exc) == "bom_version_overlap" and v3.version_no == 3
    # one ACTIVE version per output and date holds
    with inv.scope():
        for day in (dt.date(2026, 1, 1), dt.date(2026, 5, 31), dt.date(2026, 6, 1), dt.date(2027, 1, 1)):
            active = [
                bm
                for bm in Bom.objects.filter(item_id=out.id, bom_status="ACTIVE")
                if bm.valid_from <= day and (bm.valid_to is None or day <= bm.valid_to)
            ]
            assert len(active) == 1, day

    # ---- yield and component checks
    with pytest.raises(DomainError) as exc:
        xw.bom("R-Y0", out, "10", [(raw, "1", "0")], yield_percent="0")
    assert _code(exc) == "bom_yield_invalid"
    with pytest.raises(DomainError) as exc:
        xw.bom("R-Y101", out, "10", [(raw, "1", "0")], yield_percent="100.5")
    assert _code(exc) == "bom_yield_invalid"
    dead = xw.inv.item("BOM-DEAD")
    draft = xw.bom("R-DEAD", xw.inv.item("BOM-OUT2"), "1", [(dead, "1", "0")])
    with inv.scope():
        Item.objects.filter(id=dead.id).update(status=Status.ARCHIVED)
    with pytest.raises(DomainError) as exc:
        xw.activate(draft)
    assert _code(exc) == "bom_component_inactive"

    # ---- only drafts are editable; obsolete keeps history
    r = api.get(f"/api/v1/inventory/boms/{v1.id}")
    assert r.status_code == 200 and r.json()["version_no"] == 1
    r = api.patch(f"/api/v1/inventory/boms/{v1.id}", {"name": "otro"}, HTTP_IF_MATCH=r["ETag"])
    assert r.status_code == 409 and r.json()["code"] == "invalid_state_transition", r.content
    r = api.post(f"/api/v1/inventory/boms/{v1.id}:obsolete")
    assert r.status_code == 200 and r.json()["bom_status"] == "OBSOLETE" and r.json()["status"] == "ARCHIVED"


def test_T_INV_BOM_02_explosion_with_yields_and_scrap_is_exact_and_costed_explosion_matches_valuation(
    xw: Any, inv: Any, clerk: Any, login: Any
) -> None:
    # ---- pure: 20 good units of a batch of 10 at 80 % yield = 2.5 batches; 4 kg × 2.5 × 1.05 scrap = 10.5 kg
    fin, mix, flour, salt, water = (uuid.uuid4() for _ in range(5))
    spec_mix = core.BomSpec(mix, mix, D("3"), D("100"), (core.Component(water, D("1"), line_no=1),))
    spec_fin = core.BomSpec(
        fin,
        fin,
        D("10"),
        D("80"),
        (
            core.Component(flour, D("4"), D("5"), line_no=1),
            core.Component(salt, D("0.125"), line_no=2),
            core.Component(mix, D("2.4"), D("0"), line_no=3),
            core.Component(uuid.uuid4(), D("1"), is_byproduct=True, cost_share_percent=D("10"), line_no=4),
        ),
    )
    tree = core.explode(spec_fin, D("20"), lambda i: spec_mix if i == mix else None)
    by_item = {n.item_id: n for n in tree.children}
    assert by_item[flour].qty == Fraction("10.5") and by_item[flour].quantity == D("10.500000")
    assert by_item[salt].qty == Fraction("0.3125")
    assert by_item[mix].qty == Fraction(6)  # 2.4 × 2.5
    # second level: 6 units of a 3-unit batch = 2 batches → exactly 2 water (no 1.999999 drift)
    [water_node] = by_item[mix].children
    assert water_node.qty == Fraction(2) and water_node.level == 2
    leaves = {n.item_id: n.qty for n in core.leaves(tree)}
    assert leaves == {flour: Fraction("10.5"), salt: Fraction("0.3125"), water: Fraction(2)}
    # thirds stay exact until the edge: 1 unit of a 3-unit batch needs 1/3, quantized only when leaving
    third = core.explode(spec_mix, D("1"), lambda _i: None).children[0]
    assert third.qty == Fraction(1, 3) and third.quantity == D("0.333333")

    # ---- real: costed explosion = what consuming the leaves takes from valuation
    kg = {"base_uom": "KG"}
    i_fin, i_mix = inv.item("PAN-FIN", **kg), inv.item("PAN-MIX", **kg)
    i_flour, i_salt, i_water = inv.item("HARINA", **kg), inv.item("SAL", **kg), inv.item("AGUA", **kg)
    xw.stock(i_flour, "30", "2350.5")
    xw.stock(i_flour, "7", "2400")  # WAC 2359.95946…
    xw.stock(i_salt, "5", "1300")
    xw.stock(i_water, "50", "3.333")
    xw.activate(xw.bom("R-MIX", i_mix, "3", [(i_water, "1", "0")]))
    bom = xw.activate(
        xw.bom(
            "R-PAN", i_fin, "10", [(i_flour, "4", "5"), (i_salt, "0.125", "0"), (i_mix, "2.4", "0")], yield_percent="80"
        )
    )
    with inv.scope():
        exp = boms.explode(bom, D("20"), dt.date(2026, 11, 9))
    got = {c.item_code: (c.quantity, c.value) for c in exp.leaves}
    assert {k: q for k, (q, _v) in got.items()} == {"HARINA": D("10.5"), "SAL": D("0.3125"), "AGUA": D("2")}
    assert exp.total_cost == sum((v for _q, v in got.values()), D(0))
    with inv.scope():
        single = boms.explode(bom, D("20"), dt.date(2026, 11, 9), multi_level=False)
    assert {c.item_code for c in single.leaves} == {"HARINA", "SAL", "PAN-MIX"}

    # the API returns the same costed explosion (confidential cost visible to the inventory role)
    api = login(clerk)
    r = api.get(f"/api/v1/inventory/boms/{bom.id}/explode", qty="20", date="2026-11-09")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total_cost"] == {"amount": format(exp.total_cost, "f"), "currency": "COP"}
    assert body["tree"]["children"][2]["children"][0]["quantity"] == "2.000000"

    # consuming exactly the leaves takes exactly the costed values from valuation
    doc = DocumentRef("test.bom.consumption", ids.new_id(), "BOM-02")
    res = xw.run(
        lambda u: SERVICE.issue(
            u,
            inv.company(),
            doc,
            [
                inv.line(i_flour, xw.wh, "10.500", "f"),
                inv.line(i_water, xw.wh, "2.000", "w"),
            ],
            dt.date(2026, 11, 9),
        )
    )
    with inv.scope():
        values = {m.item_id: -m.value for m in StockMove.objects.filter(id__in=res.move_ids)}
    assert values[i_flour.id] == got["HARINA"][1] and values[i_water.id] == got["AGUA"][1]
    # salt (KG precision 3) cannot be issued as 0.3125: the explosion is exact, the stock edge rounds per UoM
    assert got["SAL"][1] == D("406.2500")  # 0.3125 × 1300 exactly
