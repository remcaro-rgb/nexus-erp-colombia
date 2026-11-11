"""Barcode label sheets (F-INV-BARCODE, D-004/D-005): one page per label, sized by the label template (mm).

Input is the data P09's ``inventory.barcodes.labels.render_pdf`` hands to the ``DocumentRenderer``: the template
(code, name, width_mm, height_mm, fields) and the rendered labels (value, symbology, modules, text). The bars are
drawn as a grayscale image XObject from the module string (no font, no vector trick), so the barcode in the PDF can
be decoded back to its value from the image alone (:func:`documents.barcode.decode_image`). Labels never print cost:
only the label's own text fields (a fixed whitelist set by P09) are drawn.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from documents.barcode import bars_image
from documents.markup import TemplateError
from documents.pdf.fonts import fit
from documents.pdf.writer import MM, PdfDocument

TEXT_FIELDS = ("code", "name", "presentation", "lot", "expiry_date", "location", "company", "notice")
MAX_LABELS = 2000


def _mm(value: Any, default: int) -> Decimal:
    try:
        mm = int(value)
    except (TypeError, ValueError):
        mm = default
    return Decimal(min(max(mm, 10), 300)) * MM


def render_labels(data: Mapping[str, Any], *, watermark: str | None, title: str) -> bytes:
    template = data.get("template") or {}
    labels = data.get("labels") or []
    if not isinstance(labels, list) or not labels:
        raise TemplateError("a label sheet needs at least one label")
    if len(labels) > MAX_LABELS:
        raise TemplateError(f"at most {MAX_LABELS} labels per document")
    width = _mm(template.get("width_mm"), 50)
    height = _mm(template.get("height_mm"), 25)
    doc = PdfDocument(title)
    margin = Decimal(4)
    for n, label in enumerate(labels):
        if not isinstance(label, Mapping):
            raise TemplateError(f"label {n} is not an object")
        modules = str(label.get("modules") or "")
        value = str(label.get("value") or "")
        if not modules or not value:
            raise TemplateError(f"label {n} has no barcode")
        page = doc.page(width, height)
        img = bars_image(modules, scale=2, height=40)
        name = doc.add_image(img)
        bar_w = width - 2 * margin
        bar_h = (height - 2 * margin) * Decimal("0.5")
        top = height - margin
        if watermark:
            page.text(margin, top - 5, fit(watermark, "Helvetica", 4.5, width - 2 * margin), "Helvetica", 4.5)
            top -= 6
        page.image(name, margin, top - bar_h, bar_w, bar_h)
        y = top - bar_h - 7
        page.text_center(width / 2, y, value, "Courier", 6.5)
        texts = label.get("text") or {}
        for key in TEXT_FIELDS:
            if key == "value" or key not in texts or y < margin + 5:
                continue
            y -= Decimal("6.5")
            page.text(margin, y, fit(str(texts[key]), "Helvetica", 5.5, width - 2 * margin), "Helvetica", 5.5)
    return doc.build()
