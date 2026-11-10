"""CSV export hardening (OWASP CSV injection): formula-leading cells are neutralised, other cells are unchanged."""

from __future__ import annotations

import csv
import io

from ledger.dimensions import csv_export


def test_safe_cell_neutralises_formula_prefixes() -> None:
    for raw in ("=SUM(A1:A2)", "+1", "-1", "@cmd", "\tx", "\rx"):
        assert csv_export.safe_cell(raw) == "'" + raw
    assert csv_export.safe_cell("CC-01") == "CC-01"
    assert csv_export.safe_cell(None) == ""
    assert csv_export.safe_cell("2026-01-01") == "2026-01-01"


def test_render_round_trips_through_csv_reader() -> None:
    text = csv_export.render(("code", "description"), [("A1", "Nodo, con coma"), ("B2", "=HYPERLINK(1)")])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows == [["code", "description"], ["A1", "Nodo, con coma"], ["B2", "'=HYPERLINK(1)"]]
