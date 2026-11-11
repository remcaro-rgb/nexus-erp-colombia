"""Consolidated statements data (balance, income statement), eliminations and IC reconciliation reports.

Shapes follow ``ReportResult`` (contracts/openapi/root.yaml). Posted runs report from the stored consolidation
book and the run summary (immutable); draft runs are computed live (nothing persisted). Amounts are strings with
4 decimals; balances are shown with their natural sign (debit nature positive for assets/expenses, credit nature
positive for liabilities/equity/income). ``synthetic`` is true in dev (synthetic ledgers, NOT LEGALLY VERIFIED).
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from decimal import Decimal
from typing import Any

from consolidation import engine
from consolidation.models import ConsolidationRun, RunStatus

ZERO = Decimal(0)
CREDIT_NATURE = ("liability", "equity", "income")
TYPE_LABEL = {
    "asset": "Activo",
    "liability": "Pasivo",
    "equity": "Patrimonio",
    "income": "Ingresos",
    "expense": "Gastos y costos",
    "memo": "Orden",
}
COLUMNS = [
    {"key": "account", "label": "Cuenta del grupo", "type": "string"},
    {"key": "name", "label": "Nombre", "type": "string"},
    {"key": "type", "label": "Tipo", "type": "string"},
    {"key": "aggregated", "label": "Suma de compañías", "type": "money"},
    {"key": "eliminations", "label": "Eliminaciones y ajustes", "type": "money"},
    {"key": "consolidated", "label": "Consolidado", "type": "money"},
]


def _s(value: Decimal) -> str:
    return str(engine.q4(value))


class RunData:
    """Aggregated / elimination amounts of a run (stored when posted, computed when draft)."""

    def __init__(
        self,
        run: ConsolidationRun,
        accounts: dict[str, tuple[str, str]],
        aggregated: dict[str, Decimal],
        eliminations: dict[str, Decimal],
        entries: list[dict[str, Any]],
        ic_differences: list[dict[str, Any]],
    ) -> None:
        self.run = run
        self.accounts = accounts
        self.aggregated = aggregated
        self.eliminations = eliminations
        self.entries = entries
        self.ic_differences = ic_differences

    @classmethod
    def from_posted(cls, run: ConsolidationRun) -> RunData:
        summary = run.summary or {}
        accounts = {k: (v[0], v[1]) for k, v in (summary.get("accounts") or {}).items()}
        aggregated: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for per in (summary.get("by_company") or {}).values():
            for acc, amt in per.items():
                aggregated[acc] += Decimal(amt)
        eliminations: dict[str, Decimal] = defaultdict(lambda: ZERO)
        entries = []
        for e in run.entries.order_by("entry_no").prefetch_related("lines"):
            lines = []
            for ln in e.lines.order_by("line_no"):
                eliminations[ln.group_account] += ln.debit - ln.credit
                lines.append(
                    {
                        "account": ln.group_account,
                        "company_id": str(ln.company_id) if ln.company_id else None,
                        "debit": ln.debit,
                        "credit": ln.credit,
                        "memo": ln.memo,
                    }
                )
            entries.append({"entry_no": e.entry_no, "kind": e.kind, "memo": e.memo, "lines": lines})
        return cls(
            run, accounts, dict(aggregated), dict(eliminations), entries, list(summary.get("ic_differences") or [])
        )

    @classmethod
    def from_result(cls, run: ConsolidationRun, result: engine.Result) -> RunData:
        entries = [
            {
                "entry_no": i,
                "kind": e.kind,
                "memo": e.memo,
                "lines": [
                    {
                        "account": ln.group_account,
                        "company_id": str(ln.company_id) if ln.company_id else None,
                        "debit": ln.debit,
                        "credit": ln.credit,
                        "memo": ln.memo,
                    }
                    for ln in e.lines
                ],
            }
            for i, e in enumerate(result.entries, start=1)
        ]
        diffs = [{k: str(v) for k, v in d.items()} for d in result.ic_differences]
        return cls(run, result.accounts, result.aggregated(), result.eliminations(), entries, diffs)

    def account(self, code: str) -> tuple[str, str]:
        return self.accounts.get(code, (code, engine._guess_type(code)))

    def rows(self, types: tuple[str, ...]) -> list[dict[str, Any]]:
        out = []
        for code in sorted(set(self.aggregated) | set(self.eliminations)):
            name, typ = self.account(code)
            if typ not in types:
                continue
            agg, elim = self.aggregated.get(code, ZERO), self.eliminations.get(code, ZERO)
            sign = -1 if typ in CREDIT_NATURE else 1
            if agg == ZERO and elim == ZERO:
                continue
            out.append(
                {
                    "account": code,
                    "name": name,
                    "type": TYPE_LABEL.get(typ, typ),
                    "type_code": typ,
                    "aggregated": _s(sign * agg),
                    "eliminations": _s(sign * elim),
                    "consolidated": _s(sign * (agg + elim)),
                }
            )
        return out

    def consolidated(self, code: str) -> Decimal:
        return self.aggregated.get(code, ZERO) + self.eliminations.get(code, ZERO)


def _params(run: ConsolidationRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "number": run.number,
        "group_id": str(run.group_id),
        "period": run.period,
        "currency": run.currency,
        "status": run.status,
        "posted": run.status == RunStatus.POSTED,
    }


def empty(report: str) -> dict[str, Any]:
    return {"report": report, "parameters": {}, "columns": COLUMNS, "rows": [], "totals": {}, "synthetic": True}


def balance(data: RunData) -> dict[str, Any]:
    rows = data.rows(("asset", "liability", "equity"))
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for code in set(data.aggregated) | set(data.eliminations):
        _n, typ = data.account(code)
        totals[typ] += data.consolidated(code)
    result = -(totals["income"] + totals["expense"])  # credit-positive current result (after NCI share)
    assets, liabilities, equity = totals["asset"], -totals["liability"], -totals["equity"]
    rows.append(
        {
            "account": "",
            "name": "Resultado del ejercicio atribuible a la controladora",
            "type": TYPE_LABEL["equity"],
            "type_code": "equity",
            "aggregated": "",
            "eliminations": "",
            "consolidated": _s(result),
        }
    )
    return {
        "report": "consolidation.balance",
        "parameters": _params(data.run),
        "columns": COLUMNS,
        "rows": rows,
        "totals": {
            "assets": _s(assets),
            "liabilities": _s(liabilities),
            "equity": _s(equity + result),
            "difference": _s(assets - liabilities - equity - result),
            "currency": data.run.currency,
        },
        "warnings": _warnings(data),
        "synthetic": True,
    }


def income_statement(data: RunData, nci_profit_account: str) -> dict[str, Any]:
    rows = data.rows(("income", "expense"))
    income = expense = ZERO
    for code in set(data.aggregated) | set(data.eliminations):
        _n, typ = data.account(code)
        if code == nci_profit_account:
            continue
        if typ == "income":
            income -= data.consolidated(code)
        elif typ == "expense":
            expense += data.consolidated(code)
    nci = data.consolidated(nci_profit_account)
    net = income - expense
    return {
        "report": "consolidation.income_statement",
        "parameters": _params(data.run),
        "columns": COLUMNS,
        "rows": rows,
        "totals": {
            "income": _s(income),
            "expenses": _s(expense),
            "net_income": _s(net),
            "non_controlling_interest": _s(nci),
            "attributable_to_parent": _s(net - nci),
            "currency": data.run.currency,
        },
        "warnings": _warnings(data),
        "synthetic": True,
    }


def eliminations(data: RunData) -> dict[str, Any]:
    rows = []
    debit = credit = ZERO
    for e in data.entries:
        for i, ln in enumerate(e["lines"], start=1):
            name, _t = data.account(ln["account"])
            rows.append(
                {
                    "entry_no": e["entry_no"],
                    "kind": e["kind"],
                    "memo": e["memo"],
                    "line_no": i,
                    "account": ln["account"],
                    "name": name,
                    "company_id": ln["company_id"],
                    "debit": _s(ln["debit"]),
                    "credit": _s(ln["credit"]),
                }
            )
            debit += ln["debit"]
            credit += ln["credit"]
    return {
        "report": "consolidation.eliminations",
        "parameters": _params(data.run),
        "columns": [
            {"key": "entry_no", "label": "Asiento", "type": "integer"},
            {"key": "kind", "label": "Tipo", "type": "string"},
            {"key": "memo", "label": "Concepto", "type": "string"},
            {"key": "account", "label": "Cuenta del grupo", "type": "string"},
            {"key": "name", "label": "Nombre", "type": "string"},
            {"key": "company_id", "label": "Compañía", "type": "string"},
            {"key": "debit", "label": "Débito", "type": "money"},
            {"key": "credit", "label": "Crédito", "type": "money"},
        ],
        "rows": rows,
        "totals": {"debit": _s(debit), "credit": _s(credit), "currency": data.run.currency},
        "warnings": _warnings(data),
        "synthetic": True,
    }


def _warnings(data: RunData) -> list[dict[str, str]]:
    return [
        {
            "code": "intercompany_mismatch",
            "message": (
                f"Diferencia intercompañía {d.get('difference')} entre {d.get('company_a_id')} "
                f"y {d.get('company_b_id')}"
            ),
        }
        for d in data.ic_differences
    ]


def reconciliation(rows: list[dict[str, Any]], until: str) -> dict[str, Any]:
    total = sum((abs(Decimal(r["difference"])) for r in rows), ZERO)
    return {
        "report": "intercompany.reconciliation",
        "parameters": {"until": until},
        "columns": [
            {"key": "agreement", "label": "Acuerdo", "type": "string"},
            {"key": "company_a_id", "label": "Vendedora (A)", "type": "string"},
            {"key": "company_b_id", "label": "Compradora (B)", "type": "string"},
            {"key": "a_receivable_net", "label": "Saldo por cobrar A", "type": "money"},
            {"key": "b_payable_net", "label": "Saldo por pagar B", "type": "money"},
            {"key": "difference", "label": "Diferencia", "type": "money"},
            {"key": "status", "label": "Estado", "type": "string"},
        ],
        "rows": rows,
        "totals": {"absolute_difference": _s(total), "mismatches": sum(1 for r in rows if r["status"] != "MIRRORED")},
        "warnings": [
            {"code": "intercompany_mismatch", "message": f"{r['agreement']}: diferencia {r['difference']}"}
            for r in rows
            if r["status"] != "MIRRORED"
        ],
        "synthetic": True,
    }


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # CSV formula-injection hardening: a leading = + - @ (or tab/CR) is neutralised, except plain numbers.
    if text and text[0] in "=+-@\t\r":
        try:
            Decimal(text)
        except ArithmeticError:
            return "'" + text
    return text


def to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    cols = report["columns"]
    writer.writerow([c["label"] for c in cols])
    for row in report["rows"]:
        writer.writerow([_cell(row.get(c["key"])) for c in cols])
    return buf.getvalue()
