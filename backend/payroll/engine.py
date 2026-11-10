"""Payroll calculation (deterministic; pure over its inputs — no database, no I/O).

``calculate_employee`` evaluates the compiled concept graph for one employee in topological
order (D-025) and returns one line per concept with the formula version hash, the bound inputs,
the explain trace and the snapshots of the legal parameters it read. Amounts are Decimal,
rounded ROUND_HALF_UP to the concept's places (COP 2, D-001). Negative results are refused.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel.errors import ValidationFailed
from payroll.concepts.graph import HOURS_RE, CompiledGraph, round_amount
from payroll.concepts.parameters import NOT_LEGALLY_VERIFIED, ParameterSet, is_parameter_input
from payroll.interfaces import PayrollEmployee

PERIOD_DAYS = {"MONTHLY": Decimal(30), "BIWEEKLY": Decimal(15), "WEEKLY": Decimal(7)}
ZERO = Decimal(0)
EMPLOYER_TYPES = ("APORTE", "PROVISION")


@dataclass(frozen=True)
class PeriodInfo:
    start: dt.date
    end: dt.date
    frequency: str = "MONTHLY"

    @property
    def days(self) -> Decimal:
        return PERIOD_DAYS.get(self.frequency, Decimal((self.end - self.start).days + 1))


@dataclass(frozen=True)
class NoveltyTotals:
    amount: Decimal = ZERO
    quantity: Decimal = ZERO


@dataclass
class LineResult:
    concept_code: str
    concept_type: str
    sequence: int
    amount: Decimal
    quantity: Decimal | None
    formula_id: str | None
    formula_version: int | None
    formula_hash: str
    inputs: dict[str, Any]
    trace: list[dict[str, Any]]
    parameters: list[dict[str, Any]]


@dataclass
class EmployeeResult:
    employee: PayrollEmployee
    lines: list[LineResult] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)

    def total(self, *types: str) -> Decimal:
        return sum((ln.amount for ln in self.lines if ln.concept_type in types), ZERO)

    @property
    def earnings(self) -> Decimal:
        return self.total("DEVENGO")

    @property
    def deductions(self) -> Decimal:
        return self.total("DEDUCCION")

    @property
    def employer_cost(self) -> Decimal:
        return self.total(*EMPLOYER_TYPES)

    @property
    def net_pay(self) -> Decimal:
        return self.earnings - self.deductions

    @property
    def legally_verified(self) -> bool:
        return all(p.get("legally_verified") for p in self.parameters.values())

    @property
    def legal_notice(self) -> str:
        return "" if self.legally_verified else NOT_LEGALLY_VERIFIED


def days_worked(employee: PayrollEmployee, period: PeriodInfo) -> Decimal:
    """Commercial days (30-day month convention) the contract covers in the period."""
    if employee.days_worked is not None:
        return employee.days_worked
    start = max(period.start, employee.start_date or period.start)
    end = min(period.end, employee.end_date or period.end)
    if end < start:
        return ZERO
    if start == period.start and end == period.end:
        return period.days
    return min(period.days, Decimal((end - start).days + 1))


def base_inputs(employee: PayrollEmployee, period: PeriodInfo) -> dict[str, Any]:
    return {
        "salary": employee.salary,
        "days_worked": days_worked(employee, period),
        "period_days": period.days,
        "integral_salary": employee.salary_type == "INTEGRAL",
        "exonerated": employee.exonerated,
        "arl_risk_class": employee.arl_risk_class,
        "contract_type": employee.contract_type,
        "period_start": period.start,
        "period_end": period.end,
    }


def calculate_employee(
    compiled: CompiledGraph,
    employee: PayrollEmployee,
    period: PeriodInfo,
    novelties: Mapping[str, NoveltyTotals],
    params: ParameterSet,
) -> EmployeeResult:
    base = base_inputs(employee, period)
    per_concept: dict[str, dict[str, Any]] = {}
    line_params: dict[str, list[dict[str, Any]]] = {}
    for code in compiled.order:
        names = compiled.fields_of(code)
        nov = novelties.get(code, NoveltyTotals())
        values: dict[str, Any] = {}
        for name in names:
            if name in base:
                values[name] = base[name]
            elif name == "novelty_amount":
                values[name] = nov.amount
            elif name == "novelty_quantity":
                values[name] = nov.quantity
            elif HOURS_RE.match(name):
                values[name] = employee.hours.get(name.removeprefix("hours_"), ZERO)
        param_values, snaps = params.inputs(n for n in names if is_parameter_input(n))
        values.update(param_values)
        per_concept[code] = values
        line_params[code] = snaps
    results = compiled.graph.evaluate(per_concept, per_formula_inputs=True, explain=True)
    out = EmployeeResult(employee)
    for seq, code in enumerate(compiled.order, start=1):
        spec = compiled.specs[code]
        res = results[code]
        amount = round_amount(res.value, spec.rounding_places)
        if amount < 0:
            raise ValidationFailed(
                f"concept {code} evaluated to a negative amount for employee {employee.employee_id}",
                concept=code,
            )
        snap = res.snapshot()
        nov = novelties.get(code, NoveltyTotals())
        out.lines.append(
            LineResult(
                concept_code=code,
                concept_type=spec.concept_type,
                sequence=seq,
                amount=amount,
                quantity=nov.quantity if nov.quantity else None,
                formula_id=spec.formula.id if spec.formula else None,
                formula_version=spec.formula.version if spec.formula else None,
                formula_hash=spec.formula.hash if spec.formula else res.formula_hash,
                inputs=snap["inputs"],
                trace=snap["trace"],
                parameters=line_params[code],
            )
        )
        for p in line_params[code]:
            out.parameters[p["code"]] = p
    return out


def journal_lines(results: Sequence[EmployeeResult], roles: Mapping[str, str]) -> list[dict[str, Any]]:
    """Summarised posting lines (shared/07 §4) by account role, side and cost center.

    ``roles`` maps concept code → account role (concept ``account_role`` or the type default).
    DEVENGO: Dr expense · DEDUCCION: Cr deduction liability · net pay: Cr ``PAYROLL_NET_PAYABLE`` ·
    APORTE/PROVISION: Dr expense, Cr ``PAYROLL_CONTRIBUTIONS_PAYABLE`` / ``PAYROLL_PROVISIONS_LIABILITY``.
    """
    acc: dict[tuple[str, str, str], Decimal] = {}

    def add(role: str, side: str, cost_center: str, amount: Decimal) -> None:
        if amount:
            key = (role, side, cost_center)
            acc[key] = acc.get(key, ZERO) + amount

    for r in results:
        cc = str(r.employee.cost_center_id or "")
        for ln in r.lines:
            role = roles[ln.concept_code]
            if ln.concept_type == "DEVENGO":
                add(role, "DEBIT", cc, ln.amount)
            elif ln.concept_type == "DEDUCCION":
                add(role, "CREDIT", cc, ln.amount)
            elif ln.concept_type == "APORTE":
                add(role, "DEBIT", cc, ln.amount)
                add(CREDIT_ROLE["APORTE"], "CREDIT", cc, ln.amount)
            else:
                add(role, "DEBIT", cc, ln.amount)
                add(CREDIT_ROLE["PROVISION"], "CREDIT", cc, ln.amount)
        if r.net_pay < 0:
            raise ValidationFailed(f"net pay of employee {r.employee.employee_id} is negative")
        add(NET_PAY_ROLE, "CREDIT", cc, r.net_pay)
    return [
        {"account_role": role, "side": side, "cost_center_id": cc or None, "amount": amount}
        for (role, side, cc), amount in sorted(acc.items())
    ]


NET_PAY_ROLE = "PAYROLL_NET_PAYABLE"
DEFAULT_ROLE = {
    "DEVENGO": "PAYROLL_EXPENSE",
    "DEDUCCION": "PAYROLL_DEDUCTIONS_PAYABLE",
    "APORTE": "PAYROLL_CONTRIBUTIONS_EXPENSE",
    "PROVISION": "PAYROLL_PROVISIONS_EXPENSE",
}
CREDIT_ROLE = {"APORTE": "PAYROLL_CONTRIBUTIONS_PAYABLE", "PROVISION": "PAYROLL_PROVISIONS_LIABILITY"}
