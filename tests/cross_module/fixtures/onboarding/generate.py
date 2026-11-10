"""Deterministic generator of the S11 onboarding pack (shared/11 S11). SINTÉTICO - no real party data.

Run: ``python3 tests/cross_module/fixtures/onboarding/generate.py`` (stdlib only; rewrites ``andina/``).
Customers ``CLI-0007``, ``-0014``, ``-0021``, ``-0028``, ``-0035`` carry a wrong check digit on purpose.
"""

from __future__ import annotations

import csv
import random
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from qa_harness.golden import nit_check_digit  # noqa: E402

OUT = HERE / "andina"
RNG = random.Random(20261031)
INVALID = {7, 14, 21, 28, 35}


def write(name: str, rows: list[dict[str, object]]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parties(prefix: str, n: int, base: int, invalid: set[int]) -> list[dict[str, object]]:
    rows = []
    for i in range(1, n + 1):
        number = str(base + i * 37)
        dv = nit_check_digit(number)
        if i in invalid:
            dv = (dv + 3) % 10
        rows.append({"code": f"{prefix}-{i:04d}", "name": f"{prefix} sintético {i:02d} S.A.S.", "nit": number,
                     "dv": dv, "city": RNG.choice(["11001", "76001", "05001"])})  # fmt: skip
    return rows


def split(total: Decimal, n: int) -> list[Decimal]:
    weights = [Decimal(RNG.randint(5, 20)) for _ in range(n)]
    s = sum(weights)
    parts = [(total * w / s).quantize(Decimal("1")) for w in weights[:-1]]
    return [*parts, total - sum(parts)]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    customers = parties("CLI", 50, 900100000, INVALID)
    suppliers = parties("PRO", 30, 800200000, set())
    write("customers.csv", customers)
    write("suppliers.csv", suppliers)
    write("items.csv", [{"code": f"ART-{i:04d}", "name": f"Artículo sintético {i:03d}", "qty": RNG.randint(1, 60),
                         "unit_cost": RNG.randint(10, 900) * 100} for i in range(1, 201)])  # fmt: skip
    write("open_ar.csv", [{"number": f"FV-H-{i:04d}", "customer": customers[i * 3]["code"], "amount": str(a),
                           "due": f"2026-11-{10 + i:02d}"} for i, a in enumerate(split(Decimal(45000000), 12))])  # fmt: skip
    write("open_ap.csv", [{"number": f"FC-H-{i:04d}", "supplier": suppliers[i * 2]["code"], "amount": str(a),
                           "due": f"2026-11-{12 + i:02d}"} for i, a in enumerate(split(Decimal(22000000), 8))])  # fmt: skip
    write("bank.csv", [{"account": "111005", "bank": "Banco sintético", "balance": "30000000"}])
    write("fixed_assets.csv", [{"code": f"AF-{i:03d}", "cost": c, "accumulated_depreciation": d}
                               for i, (c, d) in enumerate([(4800000, 1066666), (12000000, 3000000), (2500000, 500000),
                                                           (900000, 300000), (35000000, 7000000)], 1)])  # fmt: skip
    puc = [("1105", "Caja"), ("1110", "Bancos"), ("1305", "Clientes"), ("1435", "Mercancías"), ("1524", "Equipo"),
           ("1592", "Depreciación acumulada"), ("2205", "Proveedores"), ("2408", "IVA por pagar"),
           ("3105", "Capital"), ("4135", "Comercio"), ("5105", "Gastos de personal"), ("6135", "Costo de ventas")]
    rows = [{"code": c, "name": n, "origin": "puc-template"} for c, n in puc]
    rows += [{"code": f"5195{i:02d}", "name": f"Gasto diverso personalizado {i:02d}", "origin": "custom"}
             for i in range(1, 41)]  # fmt: skip
    write("chart_of_accounts.csv", rows)


if __name__ == "__main__":
    main()
