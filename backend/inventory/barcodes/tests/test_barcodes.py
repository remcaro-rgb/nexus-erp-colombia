"""F-INV-BARCODE integration: T-INV-BARCODE-02 (unique internal codes under 50 concurrent generations; GS1 needs a
user-entered prefix) and T-INV-BARCODE-03 (scan lookup of item/presentation/location/lot; unknown → barcode_unknown).
Also: label templates/print jobs never carry cost; PNG preview is deterministic."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

import pytest

from inventory import masters
from inventory.barcodes import core
from inventory.barcodes import service as barcodes
from inventory.barcodes.models import Barcode
from inventory.issues.tests.threads import run_threads
from kernel.models import OutboxMessage
from kernel.uow import run_in_unit_of_work

BASE = "/api/v1/inventory"


def _generate(api: Any, owner_type: str, owner_id: Any, **params: Any) -> Any:
    return api.post(
        f"{BASE}/barcodes:generate",
        {"parameters": {"owner_type": owner_type, "owner_id": str(owner_id), **params}},
    )


@pytest.mark.django_db(transaction=True)
def test_T_INV_BARCODE_02_internal_codes_unique_under_50_concurrent_generations(inv: Any) -> None:
    items = [inv.item(f"P-BC-{n:02d}") for n in range(5)]
    ctx, company = inv.ctx(), inv.company()

    def target(i: int) -> Any:
        return run_in_unit_of_work(
            lambda u: barcodes.generate(u, company, "item", items[i % 5].id, "CODE128").value, ctx
        )

    values, errors = run_threads(50, target)
    assert errors == [], errors
    assert len(values) == 50 and len(set(values)) == 50
    assert all(re.fullmatch(r"ALFA-\d{8}", v) for v in values)  # D-004 <company_code>-<8-digit sequence>
    assert sorted(int(v.split("-")[1]) for v in values) == list(range(1, 51))  # gap-free sequence
    # unique per company: BETA starts its own sequence; the same number is fine in another company
    beta_item = inv.item("P-BC-B", company="BETA")
    beta = inv.run(lambda u: barcodes.generate(u, inv.company("BETA"), "item", beta_item.id), "BETA")
    assert beta.value == "BETA-00000001"
    with inv.scope():
        assert Barcode.objects.filter(source="GENERATED_INTERNAL").count() == 50
        assert (
            OutboxMessage.objects.filter(event_type="inventory.barcode.generated.v1", company_id=company).count() == 50
        )


@pytest.mark.django_db
def test_T_INV_BARCODE_02_gs1_generation_refused_without_user_prefix(inv: Any, clerk: Any, login: Any) -> None:
    api = login(clerk)
    item = inv.item("P-GS1")
    # no company prefix entered by a user → refused (D-004), nothing stored
    r = _generate(api, "item", item.id, symbology="EAN13")
    assert r.status_code == 422 and r.json()["code"] == "gs1_prefix_missing", r.content
    with inv.scope():
        assert Barcode.objects.count() == 0

    # restricted-circulation prefix 29 (synthetic demo): valid EAN-13, internal use, ownership never claimed
    r = _generate(api, "item", item.id, symbology="EAN13", gs1_prefix="29")
    assert r.status_code == 202, r.content
    op = r.json()
    assert op["status"] == "SUCCEEDED"
    bc = op["result"]
    assert bc["value"] == "2900000000018" and core.validate_gtin(bc["value"])
    assert bc["source"] == "GENERATED_GS1_PREFIX" and bc["gs1_ownership_claimed"] is False
    assert bc["label"] == "uso interno, no licencia GS1"
    # the stored prefix is reused without re-entering it
    r = _generate(api, "item", item.id, symbology="EAN13")
    assert r.status_code == 202 and r.json()["result"]["value"] == "2900000000025"

    # a (synthetic, test-entered) non-restricted company prefix claims ownership only for generated codes
    r = _generate(api, "item", item.id, symbology="EAN13", gs1_prefix="7709990")
    assert r.status_code == 202 and r.json()["result"]["gs1_ownership_claimed"] is True
    assert r.json()["result"]["value"].startswith("7709990") and len(r.json()["result"]["value"]) == 13

    # imported GTINs are validated (mod-10) and never claimed
    r = _generate(api, "item", item.id, mode="import", value="4006381333932")
    assert r.status_code == 422 and r.json()["code"] == "barcode_checkdigit_invalid"
    r = _generate(api, "item", item.id, mode="import", value="4006381333931")
    assert r.status_code == 202, r.content
    assert r.json()["result"]["source"] == "IMPORTED" and r.json()["result"]["gs1_ownership_claimed"] is False
    # duplicates (also as UPC-A/GTIN-14 spellings) are refused
    other = inv.item("P-GS1-2")
    for dup in ("4006381333931", "04006381333931"):
        r = _generate(api, "item", other.id, mode="import", value=dup)
        assert r.status_code == 422 and r.json()["code"] == "barcode_duplicate", (dup, r.content)
    # validate endpoint: check digit and uniqueness
    r = api.post(f"{BASE}/barcodes:validate", {"parameters": {"value": "5901234123457"}})
    assert r.status_code == 200 and r.json()["data"]["check_digit_valid"] is True
    r = api.post(f"{BASE}/barcodes:validate", {"parameters": {"value": "5901234123458"}})
    assert r.status_code == 422 and r.json()["code"] == "barcode_checkdigit_invalid"
    r = api.post(f"{BASE}/barcodes:validate", {"parameters": {"value": "2900000000018"}})
    assert r.status_code == 422 and r.json()["code"] == "barcode_duplicate"
    r = api.post(
        f"{BASE}/barcodes:validate",
        {"parameters": {"value": "2900000000018", "owner_type": "item", "owner_id": str(item.id)}},
    )
    assert r.status_code == 200  # the owner's own code is not a duplicate of itself


@pytest.mark.django_db
def test_T_INV_BARCODE_03_lookup_resolves_item_presentation_location_lot_unknown_is_barcode_unknown(
    inv: Any, clerk: Any, login: Any, world: Any
) -> None:
    api = login(clerk)
    wh = inv.warehouse("BOG-SCAN", branch="BOG")
    med = inv.warehouse("MED-SCAN", branch="MED")
    item = inv.item("P-SCAN", tracking="LOT")
    box = inv.presentation(item, "CJ12", "12", barcode="7709990000017")
    lot = inv.run(
        lambda u: masters.create_lot(u, inv.company(), item, {"code": "L-2026-11", "expiry_date": "2027-03-31"})
    )
    alm = inv.bin(wh, "ALMACEN")
    med_bin = inv.bin(med, "ALMACEN")

    item_code = _generate(api, "item", item.id).json()["result"]["value"]
    loc_code = _generate(api, "location", alm.id).json()["result"]["value"]
    med_code = _generate(api, "location", med_bin.id).json()["result"]["value"]
    lot_code = _generate(api, "lot", lot.id).json()["result"]["value"]
    r = _generate(api, "item", item.id, mode="import", value="036000291452")  # UPC-A
    assert r.status_code == 202, r.content

    def scan(client: Any, value: str) -> Any:
        return client.get(f"{BASE}/barcodes/lookup", value=value)

    # item (keyboard-wedge input with CR/LF), presentation (master barcode), location, lot
    r = scan(api, f"{item_code}\r\n")
    assert r.status_code == 200, r.content
    d = r.json()["data"]
    assert (d["owner_type"], d["item_id"], d["presentation_id"]) == ("item", str(item.id), None)
    assert r.json()["resource"] == {"type": "inventory.item", "id": str(item.id), "number": "P-SCAN"}

    d = scan(api, "7709990000017").json()["data"]
    assert (d["owner_type"], d["item_id"], d["presentation_id"], d["factor"]) == (
        "presentation", str(item.id), str(box.id), "12.000000",
    )  # fmt: skip

    d = scan(api, loc_code).json()["data"]
    assert (d["owner_type"], d["location_id"], d["warehouse_id"]) == ("location", str(alm.id), str(wh.id))

    d = scan(api, lot_code).json()["data"]
    assert (d["owner_type"], d["lot_id"], d["item_id"], d["expiry_date"]) == (
        "lot", str(lot.id), str(item.id), "2027-03-31",
    )  # fmt: skip

    # the imported UPC-A also resolves when scanned as EAN-13 (leading zero)
    d = scan(api, "0036000291452").json()["data"]
    assert (d["owner_type"], d["item_id"], d["source"]) == ("item", str(item.id), "IMPORTED")

    # unknown → barcode_unknown (404)
    r = scan(api, "NO-EXISTE-123")
    assert r.status_code == 404 and r.json()["code"] == "barcode_unknown"

    # another company's code is unknown here (no cross-company resolution, no leak)
    beta_item = inv.item("P-SCAN-B", company="BETA")
    beta_code = inv.run(lambda u: barcodes.generate(u, inv.company("BETA"), "item", beta_item.id), "BETA").value
    r = scan(api, beta_code)
    assert r.status_code == 404 and r.json()["code"] == "barcode_unknown"

    # a warehouse-scoped scanner (Bogotá only) cannot resolve a Medellín bin: barcode_unknown, not 403/200
    world.add_role("escaner", ["inventory.barcode.read", "inventory.item.read", "inventory.location.read"])
    world.add_user("escaner_bog", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("escaner_bog", "escaner", "ALFA", scope={"warehouse_ids": [str(wh.id)]})
    scanner = login("escaner_bog")
    assert scan(scanner, loc_code).status_code == 200
    r = scan(scanner, med_code)
    assert r.status_code == 404 and r.json()["code"] == "barcode_unknown"
    # without inventory.barcode.read no lookup at all
    world.add_role("sin_codigos", ["inventory.item.read"])
    world.add_user("sin_codigos", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("sin_codigos", "sin_codigos", "ALFA")
    assert scan(login("sin_codigos"), item_code).status_code == 403
    # scans never post: no stock move was created by any lookup
    from inventory.models import StockMove

    with inv.scope():
        assert StockMove.objects.count() == 0


@pytest.mark.django_db
def test_label_print_job_renders_without_cost_and_previews_png(
    inv: Any, clerk: Any, login: Any, barcode_services: Any
) -> None:
    api = login(clerk)
    item = inv.item("P-LBL", standard_cost="98765.4321")
    _generate(api, "item", item.id)
    r = api.post(
        f"{BASE}/label-templates",
        {
            "code": "ET-50x25",
            "name": "Etiqueta 50×25",
            "symbology": "CODE128",
            "layout": {"fields": ["value", "code", "name"]},
            "size_mm": "50x25",
        },
    )
    assert r.status_code == 201, r.content
    tpl = r.json()
    r = api.post(
        f"{BASE}/label-templates",
        {"code": "ET-COSTO", "name": "x", "symbology": "CODE128", "layout": {"fields": ["value", "cost"]}},
    )
    assert r.status_code == 422  # fixed fields only: no cost field exists
    r = api.post(
        f"{BASE}/label-print-jobs",
        {"template_id": tpl["id"], "targets": {"items": [{"id": str(item.id), "copies": 3}]}},
    )
    assert r.status_code == 201, r.content
    job = r.json()
    assert job["status"] == "RENDERED" and job["label_count"] == 3 and job["data_hash"]
    pdf = api.get(f"{BASE}/label-print-jobs/{job['id']}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert b"98765" not in pdf.content and b"cost" not in pdf.content
    again = api.get(f"{BASE}/label-print-jobs/{job['id']}")
    assert again.json()["status"] == "DOWNLOADED" and again.json()["pdf_hash"]
    png = api.get(f"{BASE}/label-print-jobs/{job['id']}/preview.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.content == api.get(f"{BASE}/label-print-jobs/{job['id']}/preview.png").content  # deterministic
    assert Decimal("98765.4321") == item.standard_cost
