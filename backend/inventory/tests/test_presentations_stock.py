"""T-INV-PRES-03 — availability by presentation / lot / bin; reserving 2 boxes reserves 24 base units and 2 packages."""

from __future__ import annotations

from decimal import Decimal

import pytest

from inventory import masters
from inventory.models import PackagedQuant, Reservation, StockQuant

pytestmark = pytest.mark.django_db
D = Decimal


def _code(fn):
    with pytest.raises(Exception) as exc:
        fn()
    return getattr(exc.value, "code", None)


def test_T_INV_PRES_03_availability_by_presentation_lot_bin_and_box_reservation(inv, clerk, login):
    api = login(clerk)
    wh = inv.warehouse("WH1")
    item = inv.item("P-GALLETA", tracking="LOT")
    alm, des = inv.bin(wh, "ALMACEN"), inv.bin(wh, "DESPACHO")
    lot1 = inv.run(lambda u: masters.create_lot(u, inv.company(), item, {"code": "G-1", "expiry_date": "2027-03-01"}))
    lot2 = inv.run(lambda u: masters.create_lot(u, inv.company(), item, {"code": "G-2"}))

    # presentations through the API (factor validation + barcode uniqueness)
    base = f"/api/v1/inventory/items/{item.id}/presentations"
    r = api.post(base, {"code": "CJ12", "name": "Caja x12", "uom": "caja", "factor": "12", "barcode": "2900000000017"})
    assert r.status_code == 201, r.content
    box = r.json()
    assert box["factor"] == "12.000000" and box["precision"] == 0
    r = api.post(base, {"code": "SIX", "name": "Six pack", "uom": "paq", "factor": "6", "barcode": "2900000000017"})
    assert r.status_code == 422 and r.json()["code"] == "presentation_barcode_duplicate"
    r = api.post(base, {"code": "BAD", "name": "x", "uom": "x", "factor": "0"})
    assert r.status_code == 422 and r.json()["code"] == "presentation_factor_invalid"
    r = api.post(base, {"code": "HALF", "name": "x", "uom": "x", "factor": "0.5"})  # UND has precision 0
    assert r.status_code == 422 and r.json()["code"] == "uom_precision_violation"
    six = api.post(base, {"code": "SIX", "name": "Six pack", "uom": "paq", "factor": "6"}).json()

    # receipts: 5 boxes (60) + 8 loose of lot 1 in ALMACEN, 2 six-packs (12) of lot 2 in ALMACEN, 3 boxes lot 1 in DESPACHO
    inv.receive(
        inv.line(item, wh, "60", "1", location=alm, lot=lot1, presentation=box["id"], cost="500"),
        inv.line(item, wh, "8", "2", location=alm, lot=lot1, cost="500"),
        inv.line(item, wh, "12", "3", location=alm, lot=lot2, presentation=six["id"], cost="500"),
        inv.line(item, wh, "36", "4", location=des, lot=lot1, presentation=box["id"], cost="500"),
    )
    # a receipt that is not a whole number of boxes is refused
    assert _code(lambda: inv.receive(inv.line(item, wh, "30", location=alm, lot=lot1, presentation=box["id"], cost="1"))) == "uom_precision_violation"

    r = api.get("/api/v1/inventory/availability", item=str(item.id), presentation=box["id"])
    assert r.status_code == 200, r.content
    rows = {(x["location_code"], x["lot_id"]): x for x in r.json()["items"]}
    assert set(rows) == {("ALMACEN", str(lot1.id)), ("DESPACHO", str(lot1.id))}
    alm1 = rows[("ALMACEN", str(lot1.id))]
    assert (alm1["on_hand"], alm1["packaged_base"], alm1["loose"]) == ("68.000000", "60.000000", "8.000000")
    assert alm1["packaged"] == [
        {"presentation_id": box["id"], "code": "CJ12", "factor": "12.000000", "packages": "5.000000",
         "reserved_packages": "0.000000", "available_packages": "5.000000"}
    ]  # fmt: skip
    r = api.get("/api/v1/inventory/availability", item=str(item.id), location=str(alm.id), lot=str(lot2.id))
    [row] = r.json()["items"]
    assert row["on_hand"] == "12.000000" and row["loose"] == "0.000000" and row["packaged"][0]["code"] == "SIX"

    # reserve 2 boxes in ALMACEN / lot 1 → 24 base units and 2 packages reserved
    ids = inv.reserve(inv.line(item, wh, "24", "L1", location=alm, lot=lot1, presentation=box["id"]))
    with inv.scope():
        res = Reservation.objects.get(id=ids[0])
        quant = StockQuant.objects.get(item_id=item.id, location_id=alm.id, lot_id=lot1.id)
        pq = PackagedQuant.objects.get(presentation_id=box["id"], location_id=alm.id, lot_id=lot1.id)
    assert (res.quantity, res.qty_packages) == (D(24), D(2))
    assert (quant.on_hand, quant.reserved) == (D(68), D(24))
    assert (pq.qty_packages, pq.reserved_packages) == (D(5), D(2))
    r = api.get("/api/v1/inventory/availability", item=str(item.id), location=str(alm.id), lot=str(lot1.id))
    [row] = r.json()["items"]
    assert (row["reserved"], row["available"]) == ("24.000000", "44.000000")
    assert row["packaged"][0]["available_packages"] == "3.000000"

    # 4 more boxes cannot be reserved/issued there (only 3 free boxes); 3 can
    assert _code(lambda: inv.reserve(inv.line(item, wh, "48", location=alm, lot=lot1, presentation=box["id"]))) == "stock_insufficient"
    assert _code(lambda: inv.issue(inv.line(item, wh, "48", location=alm, lot=lot1, presentation=box["id"]))) == "stock_insufficient"
    inv.issue(inv.line(item, wh, "36", location=alm, lot=lot1, presentation=box["id"]))
    # loose issue of 10 opens one free... none free (2 reserved); only 8 loose + 0 free boxes → refused
    assert _code(lambda: inv.issue(inv.line(item, wh, "10", location=alm, lot=lot1))) == "stock_insufficient"
    # after releasing, a loose issue of 10 opens one box: 8 + 12 − 10 = 10 loose, 1 box left
    inv.run(lambda u: __import__("inventory.stock.service", fromlist=["SERVICE"]).SERVICE.release(u, ids))
    inv.issue(inv.line(item, wh, "10", location=alm, lot=lot1))
    [row] = api.get("/api/v1/inventory/availability", item=str(item.id), location=str(alm.id), lot=str(lot1.id)).json()["items"]
    assert (row["on_hand"], row["reserved"], row["packaged"][0]["packages"], row["loose"]) == (
        "22.000000", "0.000000", "1.000000", "10.000000"
    )  # fmt: skip

    # archiving a presentation with packaged stock is refused
    r = api.post(f"{base}/{box['id']}:archive")
    assert r.status_code == 422 and r.json()["code"] == "packaged_exceeds_canonical"
