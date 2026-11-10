"""CSV rendering for einvoice exports, hardened against spreadsheet formula injection (CWE-1236) and redacted."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from einvoice.secrets import redact

#: a cell starting with one of these is a formula in Excel / LibreOffice / Sheets
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """Redacted text; a leading formula character is neutralised with a single quote (OWASP CSV injection)."""
    text = "" if value is None else redact(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def render(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([safe_cell(h) for h in header])
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return buf.getvalue()
