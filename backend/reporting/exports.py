"""Report exports (``?format=json|csv|xlsx|pdf``) of a contract ``ReportResult`` — pure Python, deterministic.

* CSV: UTF-8 with BOM, ``;`` separator (es-CO spreadsheet default), header = column labels.
* XLSX: a minimal Office Open XML workbook (one sheet, inline strings, numeric cells for money/integers) written
  with ``zipfile`` (fixed timestamps → identical bytes for identical results).
* PDF: the documents engine (D-005) with a table layout and the DEV watermark.

Exports go through the same query engine (row/field policy) — they never re-read data on their own. P17b standard
reports and P17c exógena reuse :func:`export` for their ``format`` parameter.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape

MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
NUMERIC = ("money", "decimal", "integer")
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def _cell(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    label = row.get(f"{key}__label")
    return f"{label}" if label else str(value)


def to_csv(result: Mapping[str, Any]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    cols = list(result.get("columns") or [])
    writer.writerow([c.get("label", c["key"]) for c in cols])
    for row in result.get("rows") or []:
        writer.writerow([_cell(row, c["key"]) for c in cols])
    return ("﻿" + out.getvalue()).encode("utf-8")


def _col_name(n: int) -> str:
    name = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def to_xlsx(result: Mapping[str, Any], sheet: str = "Reporte") -> bytes:
    cols = list(result.get("columns") or [])
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']  # fmt: skip

    def row_xml(r: int, values: list[tuple[str, str]]) -> str:
        cells = []
        for c, (kind, text) in enumerate(values):
            ref = f"{_col_name(c)}{r}"
            if kind == "n" and text not in ("", "-"):
                cells.append(f'<c r="{ref}"><v>{escape(text)}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        return f'<row r="{r}">' + "".join(cells) + "</row>"

    lines.append(row_xml(1, [("s", str(c.get("label", c["key"]))) for c in cols]))
    for n, row in enumerate(result.get("rows") or [], start=2):
        values = []
        for c in cols:
            numeric = c.get("type") in NUMERIC and row.get(c["key"]) is not None and not row.get(f"{c['key']}__label")
            values.append(("n" if numeric else "s", _cell(row, c["key"])))
        lines.append(row_xml(n, values))
    lines.append("</sheetData></worksheet>")
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": "".join(lines),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content.encode("utf-8"))
    return buf.getvalue()


def to_pdf(result: Mapping[str, Any], title: str) -> bytes:
    from documents.renderer import render_body

    cols = list(result.get("columns") or [])
    widths = ",".join("1" for _ in cols) or "1"
    body = (
        "# {{ title }}\n> {{ subtitle }}\n---\n"
        f"@cols {widths}\n"
        "|!{% for c in columns %}{{ c }}|{% endfor %}\n"
        "{% for r in rows %}|{% for v in r %}{{ v }}|{% endfor %}\n{% endfor %}"
        "---\n{% for t in totals %}**{{ t.label }}: {{ t.value }}**\n{% endfor %}"
    )
    data = {
        "title": title,
        "subtitle": f"Reporte {result.get('report', '')} · datos sintéticos (DEV)",
        "columns": [str(c.get("label", c["key"])) for c in cols],
        "rows": [[_cell(r, c["key"]) for c in cols] for r in result.get("rows") or []],
        "totals": [{"label": k, "value": str(v)} for k, v in sorted((result.get("totals") or {}).items())],
    }
    return render_body(body, "reporting.export", "1.0.0", data).content


def export(result: Mapping[str, Any], fmt: str, title: str) -> tuple[bytes, str, str]:
    """``(content, media type, file extension)`` of ``result`` in ``fmt`` (csv | xlsx | pdf)."""
    if fmt == "csv":
        return to_csv(result), MEDIA["csv"], "csv"
    if fmt == "xlsx":
        return to_xlsx(result), MEDIA["xlsx"], "xlsx"
    if fmt == "pdf":
        return to_pdf(result, title), MEDIA["pdf"], "pdf"
    raise ValueError(f"unknown export format {fmt}")
