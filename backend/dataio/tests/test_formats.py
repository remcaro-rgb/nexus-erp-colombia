"""T-P18-07/08: formula injection neutralised on export; XLSX bombs, zip-slip and oversize files rejected; CSV
delimiter / es-CO decimal comma / XLSX and JSONL parsing."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

from dataio import formats
from dataio.errors import ImportFileRejected
from dataio.templates.base import Column, format_value, parse_cell


def test_formula_injection_neutralised_in_csv_and_xlsx():
    evil = ['=HYPERLINK("http://x")', "+1+1", "-2", "@SUM(A1)", "\tTAB", "normal"]
    back = formats.parse("x.csv", formats.write("csv", ["a"], [[v] for v in evil]))[0]
    assert [r[0] for r in back.rows] == ["'" + v for v in evil[:-1]] + ["normal"]
    t = formats.parse("x.xlsx", formats.write("xlsx", ["a"], [[v] for v in evil]))[0]
    assert [r[0] for r in t.rows] == ["'" + v for v in evil[:-1]] + ["normal"]
    # our own neutralisation is undone on import (round trip), user text is kept
    assert parse_cell(Column("a"), "'=SUM(A1)") == "=SUM(A1)"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_xlsx_bomb_and_xxe_rejected():
    laughs = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><sst>&lol2;</sst>'
    data = _zip({"xl/workbook.xml": b"<workbook/>", "xl/sharedStrings.xml": laughs})
    with pytest.raises(ImportFileRejected) as exc:
        formats.parse("bomba.xlsx", data)
    assert exc.value.extra["reason"] == "xml_bomb"
    ratio = _zip({"xl/workbook.xml": b"<workbook/>", "xl/worksheets/sheet1.xml": b"A" * 5_000_000})
    with pytest.raises(ImportFileRejected) as exc:
        formats.parse("ratio.xlsx", ratio)
    assert exc.value.extra["reason"] == "zip_bomb"


def test_zip_slip_and_nested_archives_rejected():
    for name in ("../evil.csv", "/etc/evil.csv", "a/../../evil.csv", "C:/evil.csv"):
        with pytest.raises(ImportFileRejected) as exc:
            formats.parse("paquete.zip", _zip({name: b"code\nA\n"}))
        assert exc.value.extra["reason"] == "zip_slip"
    with pytest.raises(ImportFileRejected):
        formats.parse("paquete.zip", _zip({"inner.xlsx": _zip({"a.csv": b"x"})}))
    pkg = formats.parse("paquete.zip", _zip({"dimensions.csv": b"code\nA\n", "otros.csv": b"code\nB\n"}), "dimensions")
    assert [t.rows for t in pkg] == [[["A"]]]


def test_size_limit_and_encoding(monkeypatch):
    monkeypatch.setattr(formats, "MAX_FILE_BYTES", 10)
    with pytest.raises(ImportFileRejected):
        formats.parse("x.csv", b"a" * 11)
    monkeypatch.undo()
    with pytest.raises(ImportFileRejected):
        formats.parse("x.csv", "código\nañ".encode("latin-1"))


def test_csv_semicolon_decimal_comma_jsonl_and_xlsx_roundtrip():
    t = formats.parse("x.csv", "\ufeffcode;factor\nA;1.234,5\n".encode())[0]
    assert t.headers == ["code", "factor"] and t.rows == [["A", "1.234,5"]]
    assert parse_cell(Column("f", type="decimal"), "1.234,5", decimal_comma=True) == Decimal("1234.5")
    assert parse_cell(Column("f", type="decimal"), "12,5") == Decimal("12.5")
    j = formats.parse("x.jsonl", b'{"code": "A", "n": 1.50}\n{"code": "B", "ok": true}\n')[0]
    assert j.headers == ["code", "n", "ok"] and j.rows == [["A", "1.50", ""], ["B", "", "true"]]
    x = formats.parse("x.xlsx", formats.write("xlsx", ["code", "name"], [["A", "Árbol & <raíz>"]], watermark="u1"))[0]
    assert x.rows == [["A", "Árbol & <raíz>"]]
    assert format_value(Decimal("12.500000")) == "12.5" and format_value(Decimal("10.0000")) == "10"
    assert parse_cell(Column("d", type="date"), "46000").isoformat() == "2025-12-09"
