"""Unit tests of the document engine: sandboxed templates (T-P17-07 SSTI), amounts in words (es-CO), the PDF
writer/reader round trip and determinism, Code 128 / EAN-13 image decoding, exports."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from documents import barcode, builtin, layout, markup, words
from documents.pdf import reader
from documents.renderer import DEV_LABEL, RENDERER, plain, render_body


@pytest.mark.parametrize(
    "payload",
    [
        "{{ ''.__class__ }}",
        "{{ x.__class__.__mro__ }}",
        "{{ cycler.__init__.__globals__.os }}",
        "{{ self._TemplateReference__context }}",
        "{{ config.items() }}",
        "{{ lipsum.__globals__ }}",
        "{{ request.application.__globals__.__builtins__ }}",
        "{{ 7*7 }}",
        "{{ x|attr('__class__') }}",
        "{% for c in [].__class__.__base__.__subclasses__() %}{% endfor %}",
        "{% import os %}",
        "{% set x = 1 %}",
        "{{ _private }}",
        "{% if a == b %}x{% endif %}",
        "{{ data.keys }}{% for k in data.items %}{% endfor %}{{ a|upper:__class__ }}",
    ],
)
def test_T_P17_07_template_sandbox_blocks_ssti_attribute_escapes(payload: str) -> None:
    with pytest.raises(markup.TemplateError):
        markup.render(payload, {"x": "a", "data": {"keys": "k"}, "a": "b"})


def test_T_P17_07_lookups_walk_plain_data_only() -> None:
    class Secret:
        password = "s3cr3t"

        def __str__(self) -> str:
            return "Secret"

    # a Python object in the context is never traversed (no getattr) and cannot be printed
    with pytest.raises(markup.TemplateError):
        markup.render("{{ obj }}", {"obj": Secret()})
    assert markup.render("{{ obj.password }}", {"obj": Secret()}) == markup.VALUE_MARK
    assert markup.render("{{ a.b.0.c }}", {"a": {"b": [{"c": "ok"}]}}) == markup.VALUE_MARK + "ok"
    # limits
    with pytest.raises(markup.TemplateError):
        markup.render("x" * (markup.MAX_BODY + 1), {})
    with pytest.raises(markup.TemplateError):
        markup.render(
            "{% for a in xs %}{% for b in xs %}{% for c in xs %}.{% endfor %}{% endfor %}{% endfor %}",
            {"xs": list(range(30))},
        )


def test_data_cannot_inject_layout_markup() -> None:
    out = markup.render("{{ v }}\n|{{ v }}|ok|", {"v": "# TITULO\n|a|b|"})
    blocks = layout.blocks(out)
    assert [b.kind for b in blocks] == ["para", "row"]
    assert blocks[1].cells == ("# TITULO ¦a¦b¦", "ok")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", "CERO PESOS M/CTE"),
        ("1", "UN PESO M/CTE"),
        ("21", "VEINTIÚN PESOS M/CTE"),
        ("100", "CIEN PESOS M/CTE"),
        ("115", "CIENTO QUINCE PESOS M/CTE"),
        ("1001", "MIL UN PESOS M/CTE"),
        ("21000", "VEINTIÚN MIL PESOS M/CTE"),
        ("101000", "CIENTO UN MIL PESOS M/CTE"),
        ("1000000", "UN MILLÓN DE PESOS M/CTE"),
        ("1000001", "UN MILLÓN UN PESOS M/CTE"),
        ("2447500", "DOS MILLONES CUATROCIENTOS CUARENTA Y SIETE MIL QUINIENTOS PESOS M/CTE"),
        ("1250000.50", "UN MILLÓN DOSCIENTOS CINCUENTA MIL PESOS CON 50/100 M/CTE"),
        ("31000000", "TREINTA Y UN MILLONES DE PESOS M/CTE"),
        ("1000000000", "MIL MILLONES DE PESOS M/CTE"),
        ("2000000000000", "DOS BILLONES DE PESOS M/CTE"),
        ("0.995", "UN PESO M/CTE"),
    ],
)
def test_amount_in_words_es_co(amount: str, expected: str) -> None:
    assert words.amount_in_words(Decimal(amount)) == expected


def test_amount_in_words_other_currency_and_decimal_only() -> None:
    assert words.amount_in_words(Decimal("1.5"), "USD") == "UN DÓLAR CON 50/100 USD"
    with pytest.raises(TypeError):
        words.amount_in_words(15)  # type: ignore[arg-type]


@given(st.integers(min_value=0, max_value=10**15 - 1))
def test_words_are_well_formed_for_any_amount(n: int) -> None:
    text = words.integer_in_words(n)
    assert "  " not in text and not text.startswith(" ") and "uno mil" not in text and "uno millón" not in text


def test_pdf_round_trip_and_determinism() -> None:
    sample = builtin.BUILTINS["treasury.receipt"].sample
    a = RENDERER.render("treasury.receipt", dict(sample))
    b = RENDERER.render("treasury.receipt", dict(sample))
    assert a.content == b.content and a.template_version == "1.0.0" and a.watermark == DEV_LABEL
    text = reader.text(a.content)
    assert "RECIBO DE CAJA N.º RC-000001" in text and "SEISCIENTOS MIL PESOS M/CTE" in text
    changed = RENDERER.render("treasury.receipt", {**sample, "number": "RC-000002"})
    assert changed.content != a.content
    html = RENDERER.render("treasury.receipt", dict(sample), media_type="text/html").content.decode()
    assert "<h2>RECIBO DE CAJA N.º RC-000001</h2>" in html and '<th scope="col">Valor</th>' in html


def test_unknown_template_uses_the_generic_layout_and_long_documents_paginate() -> None:
    doc = RENDERER.render("hr.desprendible", {"employee": {"name": "Ana (SINTÉTICA)"}, "net": {"amount": "10"}})
    assert "Ana (SINTÉTICA)" in reader.text(doc.content)
    many = {
        "lines": [
            {
                "line_no": n,
                "description": f"Línea {n}",
                "kind_label": "Concepto",
                "base": None,
                "amount": {"amount": "1000", "currency": "COP"},
            }
            for n in range(1, 200)
        ]
    }
    long = RENDERER.render("treasury.receipt", {**builtin.BUILTINS["treasury.receipt"].sample, **many})
    pages = reader.page_texts(long.content)
    assert len(pages) > 3 and all(DEV_LABEL in p for p in pages)
    assert pages[-1].endswith(f"Página {len(pages)} de {len(pages)} · treasury.receipt v1.0.0")


def test_every_builtin_template_renders_its_markup() -> None:
    assert len(builtin.catalogue()) >= 10  # ≥ 8 document templates shipped (P17 acceptance)
    for code, t in builtin.BUILTINS.items():
        doc = render_body(t.body, code, t.version, t.sample or {"company": {"name": "X"}, "lines": []})
        assert doc.content.startswith(b"%PDF-1.4"), code


def test_plain_data_conversion() -> None:
    import datetime as dt
    import uuid

    uid = uuid.UUID(int=1)
    assert plain({"d": dt.date(2026, 1, 2), "u": uid, "n": 1.25, "t": (1, "a")}) == {
        "d": "2026-01-02",
        "u": str(uid),
        "n": Decimal("1.25"),
        "t": [1, "a"],
    }


@pytest.mark.parametrize("value", ["ACME-00000042", "12345678", "abc12345XYZ", "A1", "\tX", "ALFA-00000001"])
def test_code128_image_decodes(value: str) -> None:
    from inventory.barcodes.core import code128_modules

    img = barcode.bars_image(code128_modules(value), scale=3)
    assert barcode.decode_image(img.width, img.height, img.pixels) == ("CODE128", value)


def test_ean13_image_decodes_and_check_digit_is_verified() -> None:
    from inventory.barcodes.core import ean13_from_prefix, ean13_modules

    value = ean13_from_prefix("29", 123)
    img = barcode.bars_image(ean13_modules(value))
    assert barcode.decode_image(img.width, img.height, img.pixels) == ("EAN13", value)
    with pytest.raises(barcode.BarcodeDecodeError):
        barcode.decode_image(10, 1, b"\xff" * 10)


def test_exports_csv_xlsx_pdf() -> None:
    import io
    import zipfile

    from reporting import exports

    result = {
        "report": "ledger.journal_line",
        "columns": [
            {"key": "account_code", "label": "Cuenta", "type": "string"},
            {"key": "net", "label": "Neto", "type": "money"},
        ],
        "rows": [{"account_code": "413505", "net": "-150000.0000"}],
        "totals": {"net": "-150000.0000"},
    }
    csv_bytes = exports.to_csv(result)
    assert csv_bytes.startswith("﻿".encode()) and b"Cuenta;Neto" in csv_bytes and b"413505;-150000.0000" in csv_bytes
    xlsx = exports.to_xlsx(result)
    assert xlsx == exports.to_xlsx(result)  # deterministic
    with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "<v>-150000.0000</v>" in sheet and "413505" in sheet
    pdf = exports.to_pdf(result, "Prueba")
    assert "413505" in reader.text(pdf) and "Prueba" in reader.text(pdf)
