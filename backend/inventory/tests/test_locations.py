"""F-INV-LOC integration tests: T-INV-LOC-01 (tree), T-INV-LOC-02 (bin/lot availability + reservations),
T-INV-LOC-03 (putaway + bin-to-bin keeps valuation)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, transaction

from inventory import masters
from inventory.locations import service as locations
from inventory.models import ItemValuation, Location, StockMove, StockQuant
from inventory.stock.service import SERVICE
from kernel import ids
from kernel.interfaces.common import DocumentRef

pytestmark = pytest.mark.django_db
D = Decimal


def _code(fn):
    with pytest.raises(Exception) as exc:
        fn()
    return getattr(exc.value, "code", None), getattr(exc.value, "extra", {})


def test_T_INV_LOC_01_tree_no_cycles_parent_same_warehouse_posting_only_to_leaf_bins(inv, clerk, login):
    api = login(clerk)
    wh1, wh2 = inv.warehouse("WH1"), inv.warehouse("WH2")
    base = f"/api/v1/inventory/warehouses/{wh1.id}/locations"

    # zone A (INTERNAL group) under ALMACEN's sibling root; bins A-01, A-02 under it
    r = api.post(base, {"code": "ZA", "name": "Zona A", "location_type": "INTERNAL"})
    assert r.status_code == 201, r.content
    zone = r.json()
    assert zone["parent_id"] == str(wh1.root_location_id) and zone["is_leaf"] is True and zone["path"] == "WH1/ZA"
    a1 = api.post(base, {"code": "A-01", "name": "A-01", "location_type": "INTERNAL", "parent_id": zone["id"]}).json()
    a2 = api.post(base, {"code": "A-02", "name": "A-02", "location_type": "INTERNAL", "parent_id": zone["id"]}).json()
    assert a1["path"] == "WH1/ZA/A-01"
    assert api.get(f"{base}/{zone['id']}").json()["is_leaf"] is False

    # parent in another warehouse → location_cross_warehouse (API and composite FK)
    r = api.post(base, {"code": "X", "name": "x", "location_type": "INTERNAL", "parent_id": str(wh2.root_location_id)})
    assert r.status_code == 422 and r.json()["code"] == "location_cross_warehouse"
    with inv.scope(), pytest.raises(IntegrityError), transaction.atomic():
        Location.objects.create(
            tenant_id=wh1.tenant_id, company_id=wh1.company_id, warehouse=wh1, parent_id=wh2.root_location_id,
            code="RAW", name="raw", location_type="INTERNAL",
        )  # fmt: skip

    # cycles: moving the zone under its own child is refused by the service and by the DB trigger
    detail = api.get(f"{base}/{zone['id']}")
    r = api.patch(f"{base}/{zone['id']}", {"parent_id": a1["id"]}, HTTP_IF_MATCH=detail["ETag"])
    assert r.status_code == 422 and r.json()["code"] == "validation_failed", r.content
    with inv.scope(), pytest.raises(DatabaseError, match="cycle"), transaction.atomic():
        Location.objects.filter(id=zone["id"]).update(parent_id=a1["id"])
    # a TRANSIT bin cannot be a parent
    r = api.post(base, {"code": "T2", "name": "t", "location_type": "INTERNAL", "parent_id": str(wh1.transit_location_id)})
    assert r.status_code == 422 and r.json()["code"] == "location_not_leaf"

    item = inv.item("P-LOC1")
    # posting to a non-leaf (the zone) or to the VIEW root is refused; leaf bins accept stock
    for target in (zone["id"], str(wh1.root_location_id)):
        code, _ = _code(lambda t=target: inv.receive(inv.line(item, wh1, "5", location=t, cost="100")))
        assert code == "location_not_leaf"
    code, _ = _code(lambda: inv.receive(inv.line(item, wh1, "5", location=inv.bin(wh2, "ALMACEN"), cost="100")))
    assert code == "location_cross_warehouse"
    inv.receive(inv.line(item, wh1, "5", location=a1["id"], cost="100"))
    # a bin with stock cannot become a group
    r = api.post(base, {"code": "A-01-x", "name": "x", "location_type": "INTERNAL", "parent_id": a1["id"]})
    assert r.status_code == 409 and r.json()["code"] == "location_not_empty"
    # archive: refused while holding stock, allowed for the empty sibling; tree endpoint reflects it
    r = api.post(f"{base}/{a1['id']}:archive")
    assert r.status_code == 409 and r.json()["code"] == "location_not_empty"
    r = api.post(f"{base}/{a2['id']}:archive")
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    tree = api.get(f"{base}/tree").json()["items"]
    assert len(tree) == 1 and tree[0]["code"] == "WH1"
    za = next(n for n in tree[0]["children"] if n["code"] == "ZA")
    assert za["is_leaf"] is False and {c["code"]: c["on_hand"] for c in za["children"]} == {"A-01": "5.000000", "A-02": "0"}


def test_T_INV_LOC_02_availability_and_reservations_per_bin_and_lot(inv, clerk, login):
    api = login(clerk)
    wh = inv.warehouse("WH1")
    item = inv.item("P-LOT", tracking="LOT")
    lot_a = inv.run(lambda u: masters.create_lot(u, inv.company(), item, {"code": "L-A", "expiry_date": "2027-01-31"}))
    lot_b = inv.run(lambda u: masters.create_lot(u, inv.company(), item, {"code": "L-B", "expiry_date": "2026-12-31"}))
    alm, des = inv.bin(wh, "ALMACEN"), inv.bin(wh, "DESPACHO")
    inv.receive(
        inv.line(item, wh, "10", "1", location=alm, lot=lot_a, cost="1000"),
        inv.line(item, wh, "4", "2", location=alm, lot=lot_b, cost="1000"),
        inv.line(item, wh, "6", "3", location=des, lot=lot_a, cost="1000"),
    )
    # lot is mandatory for a lot-tracked item
    code, _ = _code(lambda: inv.receive(inv.line(item, wh, "1", location=alm, cost="1")))
    assert code == "lot_required"

    rows = {(r.location_code, r.lot_id): r for r in inv.avail(item_id=item.id)}
    assert rows[("ALMACEN", lot_a.id)].available == 10 and rows[("ALMACEN", lot_b.id)].available == 4
    assert rows[("DESPACHO", lot_a.id)].available == 6

    # reserve 8 of lot A in ALMACEN
    order = DocumentRef("sales.order", ids.new_id(), "PED-1")
    inv.reserve(inv.line(item, wh, "8", "L1", location=alm, lot=lot_a), source=order)
    r = api.get("/api/v1/inventory/availability", item=str(item.id), location=str(alm.id), lot=str(lot_a.id))
    assert r.status_code == 200, r.content
    [row] = r.json()["items"]
    assert (row["on_hand"], row["reserved"], row["available"]) == ("10.000000", "8.000000", "2.000000")

    # another document cannot issue more than the unreserved 2 from that bin/lot ...
    code, extra = _code(lambda: inv.issue(inv.line(item, wh, "3", location=alm, lot=lot_a)))
    assert code == "stock_insufficient" and extra.get("reason") == "reserved"
    # ... and cannot reserve them either
    code, _ = _code(lambda: inv.reserve(inv.line(item, wh, "3", location=alm, lot=lot_a)))
    assert code == "stock_insufficient"
    # other bins / lots are unaffected
    inv.issue(inv.line(item, wh, "4", location=alm, lot=lot_b))
    inv.issue(inv.line(item, wh, "2", location=des, lot=lot_a))
    # the reserving document issues its own 8 (reservation consumed), the free 2 remain
    inv.issue(inv.line(item, wh, "8", "L1", location=alm, lot=lot_a), source=order)
    rows = {(r.location_code, r.lot_id): r for r in inv.avail(item_id=item.id)}
    assert rows[("ALMACEN", lot_a.id)].on_hand == 2 and rows[("ALMACEN", lot_a.id)].reserved == 0
    assert ("ALMACEN", lot_b.id) not in rows and rows[("DESPACHO", lot_a.id)].on_hand == 4

    # FEFO pick when neither bin nor lot is named: lot B (expires first) then lot A by bin path
    inv.receive(inv.line(item, wh, "3", location=des, lot=lot_b, cost="1000"))
    res = inv.issue(inv.line(item, wh, "4"))
    with inv.scope():
        picked = [
            (m.from_location.code, m.lot_id, m.quantity)
            for m in StockMove.objects.filter(id__in=res.move_ids).select_related("from_location").order_by("line_ref")
        ]
    assert picked == [("DESPACHO", lot_b.id, D(3)), ("ALMACEN", lot_a.id, D(1))]
    # naming a bin of a lot-tracked item without the lot is refused
    code, _ = _code(lambda: inv.issue(inv.line(item, wh, "1", location=des)))
    assert code == "lot_required"


def test_T_INV_LOC_03_putaway_rule_else_recepcion_and_bin_to_bin_keeps_valuation(inv, clerk, login):
    api = login(clerk)
    wh = inv.warehouse("WH1")
    frozen = inv.item("P-FRZ", category="CONGELADOS")
    rice = inv.item("P-ARROZ", category="SECOS")
    other = inv.item("P-OTRO")
    base = f"/api/v1/inventory/warehouses/{wh.id}"
    cold = api.post(f"{base}/locations", {"code": "FRIO-01", "name": "Cuarto frío", "location_type": "INTERNAL"}).json()
    dry = api.post(f"{base}/locations", {"code": "SECO-01", "name": "Secos", "location_type": "INTERNAL"}).json()

    # rule by category and a higher-priority rule by item
    r = api.post(f"{base}/putaway-rules", {"category": "CONGELADOS", "location_id": cold["id"], "priority": 1})
    assert r.status_code == 201, r.content
    r = api.post(f"{base}/putaway-rules", {"item_id": str(rice.id), "location_id": dry["id"], "priority": 5})
    assert r.status_code == 201
    # a putaway rule cannot target a non-leaf/foreign bin
    r = api.post(f"{base}/putaway-rules", {"category": "X", "location_id": str(wh.root_location_id)})
    assert r.status_code == 422 and r.json()["code"] == "location_not_leaf"

    inv.receive(
        inv.line(frozen, wh, "10", "1", cost="2000"),
        inv.line(rice, wh, "20", "2", cost="3000"),
        inv.line(other, wh, "7", "3", cost="500"),
    )
    with inv.scope():
        where = {
            m.item_id: m.to_location.code
            for m in StockMove.objects.filter(kind="RECEIPT", item_id__in=[frozen.id, rice.id, other.id]).select_related("to_location")
        }
        val_before = {v.item_id: (v.qty, v.value, v.avg_cost) for v in ItemValuation.objects.filter(item_id__in=[rice.id])}
    assert where == {frozen.id: "FRIO-01", rice.id: "SECO-01", other.id: "RECEPCION"}

    # item default bin is used when no rule matches
    alm = inv.bin(wh, "ALMACEN")
    other2 = inv.item("P-DEF", default_bin_id=str(alm.id))
    inv.receive(inv.line(other2, wh, "1", cost="10"))
    with inv.scope():
        assert StockMove.objects.get(item_id=other2.id).to_location_id == alm.id

    # bin-to-bin through the API: stock moves, valuation (qty, value, avg) unchanged, no journal entry
    r = api.post(
        "/api/v1/inventory/moves:internal",
        {"item_id": str(rice.id), "from_location_id": dry["id"], "to_location_id": str(alm.id), "quantity": "8"},
    )
    assert r.status_code == 201, r.content
    move = r.json()["move"]
    assert move["kind"] == "INTERNAL" and move["value"] == {"amount": "0.0000", "currency": "COP"}
    assert move["journal_entry_id"] is None and move["unit_cost"] == "3000.00000000"
    with inv.scope():
        after = {v.item_id: (v.qty, v.value, v.avg_cost) for v in ItemValuation.objects.filter(item_id__in=[rice.id])}
        q = {x.location_id: x.on_hand for x in StockQuant.objects.filter(item_id=rice.id)}
    assert after == val_before
    assert q == {__import__("uuid").UUID(dry["id"]): D(12), alm.id: D(8)}
    from ledger.models import JournalEntry

    with inv.scope():
        assert not JournalEntry.objects.filter(source_type="inventory.internal_move").exists()

    # bin-to-bin across warehouses is refused (that is a transfer) and so is moving more than available
    wh2 = inv.warehouse("WH2")
    r = api.post(
        "/api/v1/inventory/moves:internal",
        {"item_id": str(rice.id), "from_location_id": str(alm.id), "to_location_id": str(inv.bin(wh2, "ALMACEN").id), "quantity": "1"},
    )
    assert r.status_code == 422 and r.json()["code"] == "location_cross_warehouse"
    r = api.post(
        "/api/v1/inventory/moves:internal",
        {"item_id": str(rice.id), "from_location_id": str(alm.id), "to_location_id": dry["id"], "quantity": "9"},
    )
    assert r.status_code == 422 and r.json()["code"] == "stock_insufficient"
    # decimals must be strings
    r = api.post(
        "/api/v1/inventory/moves:internal",
        {"item_id": str(rice.id), "from_location_id": str(alm.id), "to_location_id": dry["id"], "quantity": 1.5},
    )
    assert r.status_code == 422
    # posted moves are immutable (DB trigger)
    with inv.scope(), pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
        StockMove.objects.filter(id=move["id"]).update(quantity=D(1))
    assert locations.is_leaf(alm)
    assert SERVICE.availability(inv.company(), rice.id, wh.id)[0].on_hand == 20
