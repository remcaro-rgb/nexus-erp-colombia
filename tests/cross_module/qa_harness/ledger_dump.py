"""Scenario ledger dumps (prompt P22 "Required outputs per gate"): journal lines per scenario as CSV
for the contador's human review at EG-8, plus balance checks used by the scenario assertions.

Input rows are mappings with at least ``entry_id``, ``account`` (role or PUC code), ``debit``,
``credit`` (``Decimal`` or decimal strings), ``currency``; optional ``document``, ``posting_date``,
``company``, ``debit_functional`` / ``credit_functional`` (COP), ``dimension`` and ``description``.
Money stays ``Decimal`` end to end (C2-2); floats are rejected.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

COLUMNS = (
    "scenario",
    "company",
    "document",
    "entry_id",
    "posting_date",
    "line",
    "account",
    "description",
    "dimension",
    "currency",
    "debit",
    "credit",
    "debit_functional",
    "credit_functional",
)

ZERO = Decimal("0")


def to_decimal(value: Any, name: str = "amount") -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{name}: float is forbidden for money (C2-2)")
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name}: not a decimal: {value!r}") from exc


def unbalanced_entries(lines: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Decimal]]:
    """Invariant 1: per entry, Σdebit − Σcredit per transaction currency and in functional currency.

    Returns only the entries/currencies whose difference is not exactly zero.
    """
    sums: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for ln in lines:
        entry = str(ln["entry_id"])
        cur = str(ln.get("currency") or "COP")
        sums[entry][cur] += to_decimal(ln.get("debit"), "debit") - to_decimal(ln.get("credit"), "credit")
        if "debit_functional" in ln or "credit_functional" in ln:
            sums[entry]["functional"] += to_decimal(ln.get("debit_functional"), "debit_functional") - to_decimal(
                ln.get("credit_functional"), "credit_functional"
            )
    return {e: {c: v for c, v in by.items() if v != ZERO} for e, by in sums.items() if any(v != ZERO for v in by.values())}


def account_balances(lines: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for ln in lines:
        out[str(ln["account"])] += to_decimal(ln.get("debit")) - to_decimal(ln.get("credit"))
    return dict(sorted(out.items()))


def dump_csv(scenario: str, lines: Iterable[Mapping[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for i, ln in enumerate(lines, start=1):
            row = {k: ln.get(k, "") for k in COLUMNS}
            row["scenario"] = scenario
            row["line"] = ln.get("line", i)
            for k in ("debit", "credit", "debit_functional", "credit_functional"):
                if k in ln:
                    row[k] = f"{to_decimal(ln[k], k):f}"
            w.writerow(row)
    return path
