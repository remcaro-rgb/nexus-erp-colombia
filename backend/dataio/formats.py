"""File formats of the import/export pipeline: CSV, XLSX, JSON Lines and ZIP packages (P18 Capabilities, Security).

Reading is defensive (CWE-400/409/776/22):

* size limit per file (:data:`MAX_FILE_BYTES`) and row limit (:data:`MAX_ROWS`);
* XLSX and ZIP: member count, uncompressed size and compression-ratio limits (zip bombs), member names must be
  relative and free of ``..`` (zip-slip), and every XML part is refused when it declares a DTD or an entity
  (XML bombs / XXE — the stdlib parser is only reached with DTD-free documents, the same rule ``defusedxml``
  enforces; see CCR P18-1 for adding ``defusedxml``/``openpyxl``);
* CSV: UTF-8 (BOM tolerated), ``;`` or ``,`` auto-detected.

Writing neutralises spreadsheet formulas (CWE-1236): a text cell starting with ``= + - @`` (or TAB/CR) gets a
leading ``'`` in CSV and XLSX. JSON Lines is data, not a spreadsheet, and is written verbatim.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET  # noqa: S405 - only DTD-free documents reach the parser (_safe_xml)
from xml.sax.saxutils import escape

from dataio.errors import ImportFileRejected

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_ZIP_MEMBERS = 200
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_RATIO = 200
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
FORMATS = ("csv", "xlsx", "jsonl")
CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "jsonl": "application/x-ndjson",
}
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass
class Table:
    """A parsed sheet: header row and data rows (all cells as text, ``""`` when empty)."""

    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)
    sheet: str = ""


def safe_cell(value: object) -> str:
    """Text of ``value`` with a leading formula character neutralised by ``'`` (OWASP CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def detect_format(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data[:2] == b"PK":
        return "zip" if name.endswith(".zip") else "xlsx"
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".xlsx") or name.endswith(".zip"):
        raise ImportFileRejected("the file is not a valid ZIP/XLSX container", reason="type")
    if data.lstrip()[:1] == b"{":
        return "jsonl"
    return "csv"


def parse(filename: str, data: bytes, entity: str = "") -> list[Table]:
    """Every table of the upload (one per CSV/JSONL file, one per XLSX sheet, recursively for ZIP packages)."""
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileRejected(f"file larger than {MAX_FILE_BYTES} bytes", reason="size")
    if not data:
        raise ImportFileRejected("empty file", reason="empty")
    fmt = detect_format(filename, data)
    if fmt == "csv":
        return [parse_csv(data)]
    if fmt == "jsonl":
        return [parse_jsonl(data)]
    if fmt == "xlsx":
        return parse_xlsx(data)
    return parse_zip(data, entity)


# --------------------------------------------------------------------------- CSV / JSONL


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileRejected("the file must be UTF-8", reason="encoding") from None


def parse_csv(data: bytes) -> Table:
    text = _text(data)
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = [r for r in reader]
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        raise ImportFileRejected("the file has no header row", reason="empty")
    headers = [h.strip() for h in rows[0]]
    body = [[c for c in r] for r in rows[1:] if any(c.strip() for c in r)]
    _check_rows(len(body))
    return Table(headers, body)


def parse_jsonl(data: bytes) -> Table:
    headers: list[str] = []
    records: list[dict[str, object]] = []
    for n, line in enumerate(_text(data).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line, parse_float=str, parse_int=str)
        except ValueError:
            raise ImportFileRejected(f"line {n} is not valid JSON", reason="format") from None
        if not isinstance(obj, dict):
            raise ImportFileRejected(f"line {n} is not a JSON object", reason="format")
        for k in obj:
            if k not in headers:
                headers.append(str(k))
        records.append(obj)
        _check_rows(len(records))
    if not headers:
        raise ImportFileRejected("the file has no records", reason="empty")
    rows = [[_json_cell(r.get(h)) for h in headers] for r in records]
    return Table(headers, rows)


def _json_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _check_rows(n: int) -> None:
    if n > MAX_ROWS:
        raise ImportFileRejected(f"more than {MAX_ROWS} rows", reason="rows")


# --------------------------------------------------------------------------- ZIP containers


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportFileRejected("invalid ZIP container", reason="type") from None
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ImportFileRejected("too many members in the archive", reason="zip_bomb")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
            raise ImportFileRejected(f"unsafe member path {info.filename!r}", reason="zip_slip")
        total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise ImportFileRejected("archive expands beyond the size limit", reason="zip_bomb")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ImportFileRejected("suspicious compression ratio", reason="zip_bomb")
    return zf


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    with zf.open(name) as fh:
        out = fh.read(MAX_UNCOMPRESSED + 1)
    if len(out) > MAX_UNCOMPRESSED:
        raise ImportFileRejected("archive member expands beyond the size limit", reason="zip_bomb")
    return out


def parse_zip(data: bytes, entity: str) -> list[Table]:
    """A package: the members named after ``entity`` (``<entity>.csv|xlsx|jsonl``), else every data member."""
    zf = _open_zip(data)
    names = [i.filename for i in zf.infolist() if not i.is_dir()]
    wanted = [n for n in names if n.rsplit("/", 1)[-1].rsplit(".", 1)[0] == entity] or names
    tables: list[Table] = []
    for name in wanted:
        if not name.lower().endswith((".csv", ".jsonl", ".ndjson", ".xlsx")):
            continue
        member = _read_member(zf, name)
        if member[:2] == b"PK":
            raise ImportFileRejected("nested archives are not accepted", reason="zip_bomb")
        for t in parse(name, member, entity):
            t.sheet = t.sheet or name
            tables.append(t)
    if not tables:
        raise ImportFileRejected("the package has no CSV, XLSX or JSONL member", reason="empty")
    return tables


# --------------------------------------------------------------------------- XLSX


def _safe_xml(raw: bytes) -> ET.Element:
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw.upper():
        raise ImportFileRejected("XML with DTD/entities is refused (XML bomb protection)", reason="xml_bomb")
    try:
        return ET.fromstring(raw)  # noqa: S314 - DTD-free (checked above)
    except ET.ParseError:
        raise ImportFileRejected("malformed XLSX part", reason="format") from None


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_xlsx(data: bytes) -> list[Table]:
    zf = _open_zip(data)
    names = set(zf.namelist())
    if "xl/workbook.xml" not in names:
        raise ImportFileRejected("not an XLSX workbook", reason="type")
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = _safe_xml(_read_member(zf, "xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rroot = _safe_xml(_read_member(zf, "xl/_rels/workbook.xml.rels"))
        for rel in rroot:
            target = rel.get("Target", "")
            rels[rel.get("Id", "")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    wb = _safe_xml(_read_member(zf, "xl/workbook.xml"))
    tables: list[Table] = []
    for i, sheet in enumerate(wb.findall("m:sheets/m:sheet", _NS), start=1):
        rid = sheet.get(f"{{{_REL_NS}}}id", "")
        path = rels.get(rid, f"xl/worksheets/sheet{i}.xml")
        if path not in names:
            continue
        grid = _sheet_rows(_safe_xml(_read_member(zf, path)), shared)
        while grid and not any(c.strip() for c in grid[-1]):
            grid.pop()
        if not grid:
            continue
        headers = [h.strip() for h in grid[0]]
        width = len(headers)
        body = [(r + [""] * width)[:width] for r in grid[1:] if any(c.strip() for c in r)]
        _check_rows(len(body))
        tables.append(Table(headers, body, sheet.get("name", f"Hoja{i}")))
    if not tables:
        raise ImportFileRejected("the workbook has no data sheet", reason="empty")
    return tables


def _sheet_rows(root: ET.Element, shared: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            idx = _col_index(c.get("r", ""))
            if c.get("r") is None:
                idx = len(cells)
            kind = c.get("t", "n")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            else:
                v = c.find("m:v", _NS)
                raw = v.text if v is not None and v.text is not None else ""
                if kind == "s":
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        raise ImportFileRejected("broken shared-string reference", reason="format") from None
                elif kind == "b":
                    value = "true" if raw == "1" else "false"
                else:
                    value = raw
            cells[idx] = value
        width = max(cells) + 1 if cells else 0
        out.append([cells.get(i, "") for i in range(width)])
    return out


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(sheets: Sequence[tuple[str, Sequence[str], Iterable[Sequence[object]]]], watermark: str = "") -> bytes:
    """Minimal OOXML workbook (inline strings); every text cell formula-neutralised; watermark in core properties."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        sheet_entries = []
        rel_entries = []
        ct_entries = []
        for i, (name, headers, rows) in enumerate(sheets, start=1):
            xml_rows = []
            for r_i, row in enumerate([list(headers), *[list(r) for r in rows]], start=1):
                cells = "".join(
                    f'<c r="{_col_letter(c_i)}{r_i}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(safe_cell(v))}</t></is></c>"
                    for c_i, v in enumerate(row)
                )
                xml_rows.append(f'<row r="{r_i}">{cells}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{_NS["m"]}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
            )
            sheet_entries.append(f'<sheet name="{escape(name[:31])}" sheetId="{i}" r:id="rId{i}"/>')
            rel_entries.append(
                f'<Relationship Id="rId{i}" Type="{_REL_NS}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            )
            ct_entries.append(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/'
            'vnd.openxmlformats-package.core-properties+xml"/>' + "".join(ct_entries) + "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
            'metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_NS["m"]}" xmlns:r="{_REL_NS}"><sheets>{"".join(sheet_entries)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rel_entries)
            + "</Relationships>",
        )
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        zf.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:creator>{escape(watermark or 'Nexus ERP')}</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>',
        )
    return buf.getvalue()


def write(fmt: str, headers: Sequence[str], rows: Iterable[Sequence[object]], *, sheet: str = "datos", watermark: str = "") -> bytes:
    """``headers`` + ``rows`` in ``fmt`` (csv/xlsx/jsonl); CSV/XLSX cells formula-neutralised."""
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow([safe_cell(h) for h in headers])
        for r in rows:
            w.writerow([safe_cell(v) for v in r])
        return out.getvalue().encode("utf-8")
    if fmt == "jsonl":
        lines = [
            json.dumps({h: ("" if v is None else str(v)) for h, v in zip(headers, r, strict=False)}, ensure_ascii=False)
            for r in rows
        ]
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    if fmt == "xlsx":
        return write_xlsx([(sheet, headers, rows)], watermark)
    raise ImportFileRejected(f"unknown format {fmt!r}", reason="type")
