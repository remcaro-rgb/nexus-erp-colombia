"""T-INV-BARCODE-04 — the label PDF contains the barcode as an image that decodes back to the value (independent
decoder over the raster extracted from the PDF) and is deterministic; it never prints cost."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from documents.barcode import decode_image
from documents.pdf import reader

BASE = "/api/v1/inventory"
pytestmark = pytest.mark.django_db


def _template(api: Any, code: str, symbology: str = "CODE128") -> dict[str, Any]:
    r = api.post(
        f"{BASE}/label-templates",
        {
            "code": code,
            "name": f"Etiqueta {code}",
            "symbology": symbology,
            "layout": {"fields": ["value", "code", "name"]},
            "size_mm": "50x25",
        },
    )
    assert r.status_code == 201, r.content
    return dict(r.json())


def test_T_INV_BARCODE_04_label_pdf_barcode_image_decodes_to_value_and_is_deterministic(
    inv: Any, clerk: Any, login: Any
) -> None:
    api = login(clerk)
    items = [inv.item("P-ETQ-1", standard_cost="98765.4321"), inv.item("P-ETQ-2", standard_cost="12345.6789")]
    from inventory.barcodes import service as barcodes
    from kernel.uow import run_in_unit_of_work

    values = [
        run_in_unit_of_work(
            lambda u, i=item: barcodes.generate(u, inv.company(), "item", i.id, "CODE128").value, inv.ctx()
        )
        for item in items
    ]
    tpl = _template(api, "ET-P17")
    job = api.post(
        f"{BASE}/label-print-jobs",
        {
            "template_id": tpl["id"],
            "targets": {"items": [{"id": str(items[0].id), "copies": 2}, {"id": str(items[1].id), "copies": 1}]},
        },
    )
    assert job.status_code == 201, job.content
    jid = job.json()["id"]

    pdf = api.get(f"{BASE}/label-print-jobs/{jid}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    content = pdf.content
    assert content.startswith(b"%PDF-1.4")
    pages = reader.page_texts(content)
    assert len(pages) == 3  # one page per label (2 + 1 copies)
    # the bars are a raster image XObject: decode it with the independent Code 128 decoder
    images = reader.images(content)
    assert len(images) == 2  # identical labels share one image object
    decoded = sorted(decode_image(img.width, img.height, img.pixels) for img in images)
    assert decoded == sorted(("CODE128", v) for v in values)
    # every page draws its image and prints the human-readable value under it
    for page, value in zip(pages, [values[0], values[0], values[1]], strict=True):
        assert value in page.split("\n")
    # no cost on labels (D-004 fixed fields), neither as text nor in any byte of the file
    for secret in (b"98765", b"12345.6789", b"cost", b"costo"):
        assert secret not in content
    # deterministic: the same job renders to identical bytes; the stored pdf hash is that of the file
    again = api.get(f"{BASE}/label-print-jobs/{jid}/pdf")
    assert again.content == content
    stored = api.get(f"{BASE}/label-print-jobs/{jid}").json()
    assert stored["pdf_hash"] == hashlib.sha256(content).hexdigest()
    # a corrupted raster no longer decodes (the decoder really reads the bars)
    img = images[0]
    broken = bytearray(img.pixels)
    mid = img.width * (img.height // 2)
    for x in range(img.width // 3, img.width // 3 + 6):
        broken[mid + x] = 0
    with pytest.raises(ValueError):
        decode_image(img.width, img.height, bytes(broken))


def test_T_INV_BARCODE_04_ean13_label_decodes(inv: Any, clerk: Any, login: Any) -> None:
    from inventory.barcodes import core
    from inventory.barcodes import service as barcodes
    from kernel.uow import run_in_unit_of_work

    api = login(clerk)
    item = inv.item("P-ETQ-EAN")
    value = core.ean13_from_prefix("29", 42)  # restricted circulation (D-004 synthetic), imported, not claimed
    run_in_unit_of_work(lambda u: barcodes.register_imported(u, inv.company(), "item", item.id, value), inv.ctx())
    tpl = _template(api, "ET-EAN", "EAN13")
    job = api.post(f"{BASE}/label-print-jobs", {"template_id": tpl["id"], "targets": {"items": [{"id": str(item.id)}]}})
    assert job.status_code == 201, job.content
    content = api.get(f"{BASE}/label-print-jobs/{job.json()['id']}/pdf").content
    (img,) = reader.images(content)
    assert decode_image(img.width, img.height, img.pixels) == ("EAN13", value)
