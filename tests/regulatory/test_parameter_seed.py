"""Regulatory seed checks (C1-9, shared/04): statuses, sources, fail-closed years, no invented values.

These read the seed files as data so a reviewer can change a value only together
with its status, source and verification record.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "tax" / "fixtures"
VERIFICATION = ROOT / "docs" / "compliance" / "parameter-verification.md"
STATUSES = {"VERIFIED-PRIMARY", "VERIFIED-SEARCH", "VERIFIED-SECONDARY", "UNKNOWN", "DISPUTED"}

# shared/04 rows (as re-verified on 2026-11-09) that the seed must carry exactly.
EXPECTED = {
    "uvt": ("52374", "VERIFIED-PRIMARY", "S-08"),
    "smmlv": ("1750905", "DISPUTED", "S-15"),
    "aux_transporte": ("249095", "VERIFIED-SECONDARY", "S-15"),
    "iva.general": ("19", "VERIFIED-PRIMARY", "S-10"),
    "iva.reducido": ("5", "VERIFIED-PRIMARY", "S-10"),
    "reteiva.general": ("15", "VERIFIED-PRIMARY", "S-10"),
    "ss.salud.empleador": ("8.5", "VERIFIED-PRIMARY", "S-36"),
    "ss.pension.empleador": ("12", "VERIFIED-PRIMARY", "S-36"),
    "parafiscal.ccf": ("4", "VERIFIED-PRIMARY", "S-38"),
    "parafiscal.sena": ("2", "VERIFIED-PRIMARY", "S-38"),
    "parafiscal.icbf": ("3", "VERIFIED-PRIMARY", "S-39"),
}
ANNUAL = ("uvt", "smmlv", "aux_transporte")


def _seed(name: str = "parameters_2026.yaml") -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def _rows(code: str) -> list[dict[str, Any]]:
    return [r for r in _seed()["parameters"] if r["code"] == code]


def test_P06_regulatory_every_row_has_status_and_known_source() -> None:
    data = _seed()
    sources = {s["register_id"] for s in data["sources"]}
    for row in data["parameters"]:
        assert row["status"] in STATUSES, row["code"]
        assert row["source"] in sources, row["code"]
        assert len(row["code"]) <= 40
        assert "legal_reviewed_by" not in row  # EG-7 stamp is never pre-filled
    for src in data["sources"]:
        assert src["url"].startswith(("https://", "http://")) and src["method"]
        dt.datetime.fromisoformat(str(src["accessed_at"]))


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_P06_regulatory_shared04_values_and_statuses(code: str) -> None:
    value, status, source = EXPECTED[code]
    rows = [r for r in _rows(code) if r["status"] != "UNKNOWN"]
    assert len(rows) == 1
    assert (str(rows[0]["value"]), rows[0]["status"], rows[0]["source"]) == (value, status, source)


def test_P06_regulatory_2027_has_no_annual_rows() -> None:
    for code in ANNUAL:
        for row in _rows(code):
            assert row["valid_to"] == dt.date(2026, 12, 31), code
    assert all(row["valid_from"].year == 2026 for row in _seed()["parameters"])


def test_P06_regulatory_unknown_rows_carry_no_value() -> None:
    unknown = [r for r in _seed()["parameters"] if r["status"] == "UNKNOWN"]
    assert unknown
    for row in unknown:
        assert row["value_type"] == "unknown" and row["value"] == {}, row["code"]


def test_P06_regulatory_decreto_572_history() -> None:
    rows = sorted(_rows("retefuente.servicios.base_uvt"), key=lambda r: r["valid_from"])
    assert [(r["valid_from"].isoformat(), r["status"]) for r in rows] == [
        ("2026-01-01", "DISPUTED"), ("2026-05-07", "UNKNOWN"), ("2026-07-01", "DISPUTED")
    ]
    assert [e["event"] for e in rows[2]["legal_history"]] == ["published", "suspended", "suspension_revoked",
                                                              "applies_again"]  # fmt: skip


def test_P06_regulatory_art383_table_is_continuous() -> None:
    (row,) = _rows("ret_lab.art383.tabla")
    table = row["value"]["rows"]
    for prev, cur in zip(table, table[1:], strict=False):
        assert prev["to_uvt"] == cur["from_uvt"]
        # continuity of the marginal table: tax at the upper bound of a range = fixed part of the next one
        tax_at_bound = (Decimal(prev["to_uvt"]) - Decimal(prev["from_uvt"])) * Decimal(prev["rate_pct"]) / 100
        tax_at_bound += Decimal(prev["plus_uvt"])
        assert abs(tax_at_bound - Decimal(cur["plus_uvt"])) <= Decimal("1"), cur  # the law rounds to whole UVT
    assert table[-1]["to_uvt"] is None


def test_P06_regulatory_synthetic_fixture_is_labelled() -> None:
    data = _seed("parameters_synthetic_dev.yaml")
    assert data["synthetic"] is True
    assert all(r["code"].startswith("syn.") for r in data["parameters"])
    assert all(c["name"].startswith("SINTÉTICO") for c in data["synthetic_concepts"])
    assert "NOT LEGALLY VERIFIED" in (FIXTURES / "parameters_synthetic_dev.yaml").read_text(encoding="utf-8")


def test_P06_regulatory_verification_record_covers_every_source() -> None:
    text = VERIFICATION.read_text(encoding="utf-8")
    for src in _seed()["sources"]:
        assert src["register_id"] in text, src["register_id"]
    assert "S-08" in text and "52.374" in text  # G1 acceptance: UVT row re-opened and confirmed


@pytest.mark.django_db
def test_P06_regulatory_seed_loads_and_resolves(seeded: Any, company: Any) -> None:
    from kernel import tenancy
    from tax.parameters.store import DbParameterProvider

    with tenancy.activate(seeded):
        provider = DbParameterProvider()
        for code, (value, status, _source) in EXPECTED.items():
            got = provider.get(code, dt.date(2026, 7, 1), company)
            assert got.status.value == status
            expected = Decimal(value) / 100 if code not in ANNUAL else Decimal(value)
            assert got.value == expected, code
            assert got.legal_review_stamp is None and got.synthetic is False
