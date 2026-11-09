"""P22a harness self-tests: scenario ledger dump + invariant-1 balance helper."""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest
from qa_harness.ledger_dump import COLUMNS, account_balances, dump_csv, unbalanced_entries

LINES = [
    {
        "entry_id": "E1",
        "account": "AR",
        "debit": "119000.00",
        "credit": "0",
        "currency": "COP",
        "debit_functional": "119000.00",
        "credit_functional": "0",
    },
    {
        "entry_id": "E1",
        "account": "REVENUE",
        "debit": "0",
        "credit": Decimal("100000.00"),
        "currency": "COP",
        "debit_functional": "0",
        "credit_functional": "100000.00",
    },
    {
        "entry_id": "E1",
        "account": "VAT_PAYABLE",
        "debit": "0",
        "credit": "19000.00",
        "currency": "COP",
        "debit_functional": "0",
        "credit_functional": "19000.00",
    },
]


def test_p22a_balanced_entry_has_no_findings():
    assert unbalanced_entries(LINES) == {}
    assert account_balances(LINES)["AR"] == Decimal("119000.00")


def test_p22a_unbalanced_entry_is_reported_per_currency_and_functional():
    broken = [*LINES, {"entry_id": "E1", "account": "ROUNDING", "debit": "0.01", "credit": "0", "currency": "COP"}]
    assert unbalanced_entries(broken) == {"E1": {"COP": Decimal("0.01")}}
    fx = [
        {"entry_id": "E2", "account": "BANK", "debit": "10", "currency": "USD", "debit_functional": "40000"},
        {"entry_id": "E2", "account": "AR", "credit": "10", "currency": "USD", "credit_functional": "39999.99"},
    ]
    assert unbalanced_entries(fx) == {"E2": {"functional": Decimal("0.01")}}


def test_p22a_floats_are_rejected():
    with pytest.raises(TypeError):
        unbalanced_entries([{"entry_id": "E", "account": "A", "debit": 1.5}])


def test_p22a_dump_csv_for_contador_review(tmp_path):
    out = dump_csv("S1", LINES, tmp_path / "S1-ledger.csv")
    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert tuple(rows[0]) == COLUMNS
    assert [r["line"] for r in rows] == ["1", "2", "3"]
    assert rows[1]["credit"] == "100000.00" and rows[0]["scenario"] == "S1"
