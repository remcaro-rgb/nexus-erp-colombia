"""Pytest fixtures for payroll tests (imported by ``backend/payroll/tests`` and ``tests/regulatory/payroll``).

Everything is synthetic: random tenant/company/employee ids, the P06 2026 parameter seed (statuses as
recorded by P06, no legal-review stamp → NOT LEGALLY VERIFIED), formulas authored by one principal
and approved by another (SoD), the fake HR source and the PostingService test double.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

PAYROLL_PERMISSIONS = tuple(
    f"payroll.{res}.{act}"
    for res, acts in {
        "concept": ("read", "create", "update", "delete"),
        "period": ("read", "create", "update"),
        "novelty": ("read", "create"),
        "run": ("read", "create", "update", "approve", "post"),
        "payslip": ("read", "export"),
        "electronic_payroll": ("read", "create", "update"),
    }.items()
    for act in acts
)
FORMULA_PERMISSIONS = ("workflow.formula.read", "workflow.formula.create", "workflow.formula.update")
PAYSLIP_FIELDS = ("employee_id", "employee_name", "earnings", "deductions", "net_pay", "employer_cost", "lines")


@dataclass
class PayrollWorld:
    """A synthetic tenant with an author (payroll admin), an approver and a company."""

    author: Any
    approver: Any
    company_id: uuid.UUID
    policy: Any
    employees: Any
    posting: Any
    formulas: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ formulas (P07 lifecycle)
    def formula(
        self,
        code: str,
        expression: str,
        inputs: Mapping[str, Any],
        tests: Sequence[Mapping[str, Any]],
        *,
        depends_on: Sequence[str] = (),
        result_type: str = "money",
        activate: bool = True,
        effective_from: dt.date | None = dt.date(2026, 1, 1),
    ) -> Any:
        from workflow.formula.lifecycle import FormulaService

        svc = FormulaService()
        row = svc.create(
            self.author,
            self.company_id,
            {
                "domain": "payroll",
                "code": code,
                "expression": expression,
                "inputs": dict(inputs),
                "result_type": result_type,
                "depends_on": list(depends_on),
                "tests": list(tests),
                "output_places": 2,
                "output_rounding": "HALF_UP",
                "effective_from": effective_from.isoformat() if effective_from else None,
            },
        )
        if activate:
            svc.submit(self.author, row.id)
            row = svc.approve(self.approver, row.id, reason="payroll admin review (synthetic)")
        self.formulas[code] = row
        return row

    # ------------------------------------------------------------------ payroll services
    def uow(self, fn: Any, ctx: Any = None) -> Any:
        from kernel.uow import run_in_unit_of_work

        return run_in_unit_of_work(fn, ctx or self.author)

    def concept(self, code: str, concept_type: str, *, formula: Any = None, **extra: Any) -> Any:
        from payroll.concepts import service

        data = {"code": code, "name": extra.pop("name", code.title()), "concept_type": concept_type, **extra}
        if formula is not None:
            data["formula_id"] = str(formula.id)
        return self.uow(lambda u: service.create(u, self.company_id, data))

    def period(self, start: dt.date, end: dt.date, code: str | None = None) -> Any:
        from payroll import runs

        data = {"start_date": start.isoformat(), "end_date": end.isoformat(), "frequency": "MONTHLY"}
        if code:
            data["code"] = code
        return self.uow(lambda u: runs.create_period(u, self.company_id, data))

    def employee(self, salary: str, **over: Any) -> Any:
        from kernel import ids
        from payroll.interfaces import PayrollEmployee

        emp = PayrollEmployee(
            employee_id=over.pop("employee_id", ids.new_id()),
            company_id=self.company_id,
            full_name=over.pop("full_name", "Empleado Sintético"),
            contract_id=ids.new_id(),
            contract_type=over.pop("contract_type", "INDEFINIDO"),
            salary=Decimal(salary),
            start_date=over.pop("start_date", dt.date(2025, 1, 1)),
            **over,
        )
        return self.employees.add(emp)

    def novelty(
        self, period: Any, concept: Any, employee: Any, amount: str | None = None, quantity: str | None = None
    ) -> Any:
        from payroll import runs

        data = {"employee_id": str(employee.employee_id), "concept_id": str(concept.id), "period_id": str(period.id)}
        if amount is not None:
            data["amount"] = amount
        if quantity is not None:
            data["quantity"] = quantity
        return self.uow(lambda u: runs.create_novelty(u, self.company_id, data))

    def run(self, period: Any) -> Any:
        from payroll import runs

        return self.uow(lambda u: runs.create_run(u, self.company_id, {"period_id": str(period.id)}))

    def calculate(self, run: Any) -> Any:
        from payroll import runs

        return self.uow(lambda u: runs.calculate(u, self.company_id, run.id))

    def approve_and_post(self, run: Any) -> Any:
        from payroll import runs

        self.uow(lambda u: runs.approve(u, self.company_id, run.id), self.approver)
        return self.uow(lambda u: runs.post(u, self.company_id, run.id), self.approver)

    def payslips(self, run: Any) -> list[Any]:
        from kernel.testing.tenancy import bound
        from payroll.models import Payslip

        with bound(self.author):
            return list(Payslip.objects.filter(run_id=run.id).prefetch_related("lines").order_by("employee_id"))


def _grant_world(policy: Any, *ctxs: Any) -> None:
    for ctx in ctxs:
        policy.grant(ctx.principal_id, *PAYROLL_PERMISSIONS, *FORMULA_PERMISSIONS)
        policy.allow_fields(ctx.principal_id, "payroll.payslip", *PAYSLIP_FIELDS)


@pytest.fixture
def payroll_world(db: Any, settings: Any) -> Iterator[PayrollWorld]:
    from kernel import services
    from kernel.interfaces.policy import PolicyService
    from kernel.interfaces.posting import PostingService
    from kernel.testing.fakes import FakePostingService
    from kernel.testing.tenancy import make_context
    from kernel.uow import run_in_unit_of_work
    from payroll.interfaces import PayrollEmployeeSource
    from payroll.testing.fakes import FakeEmployeeSource, FieldPolicy
    from tax.parameters import seed as parameter_seed

    # API tests authenticate through the P06 test resolver (X-Test-Context → TenantContext)
    settings.ERP_TENANT_CONTEXT_RESOLVER = "tax.testing.fixtures.resolve_context"
    author = make_context()
    approver = make_context(tenant_id=author.tenant_id, company_ids=author.company_ids)
    company_id = sorted(author.company_ids)[0]
    policy = FieldPolicy()
    _grant_world(policy, author, approver)
    policy.grant(approver.principal_id, "workflow.formula.approve")
    employees: PayrollEmployeeSource = FakeEmployeeSource()
    as_policy: PolicyService = policy
    posting: PostingService = FakePostingService()
    with (
        services.override(PolicyService, as_policy),
        services.override(PayrollEmployeeSource, employees),
        services.override(PostingService, posting),
    ):
        run_in_unit_of_work(lambda uow: parameter_seed.load_defaults(uow), author)
        yield PayrollWorld(author, approver, company_id, policy, employees, posting)


MONEY = {"type": "money"}
DEC = {"type": "decimal"}


def standard_user_concepts(world: PayrollWorld) -> dict[str, Any]:
    """User-defined concepts: earnings, deductions and an employer contribution (formulas + concepts).

    Formula codes equal concept codes (the P07 graph of domain ``payroll`` mirrors the concept graph).
    The company-policy rate of AHORRO (2 %) is user data; every legal rate is a ``param_*`` input.
    ``AHORRO`` sorts before ``SALARIO`` alphabetically but must be evaluated after it (topological order).
    """
    base = {"salary": "3000000", "days_worked": "30", "period_days": "30", "novelty_amount": "0"}
    f: dict[str, Any] = {}
    f["SALARIO"] = world.formula(
        "SALARIO",
        "round(salary * days_worked / period_days, 2, 'HALF_UP')",
        {"salary": MONEY, "days_worked": DEC, "period_days": DEC},
        [{"inputs": {**base, "days_worked": "15"}, "expected": "1500000"}],
    )
    f["BONO"] = world.formula(
        "BONO",
        "novelty_amount",
        {"novelty_amount": {"type": "money", "default": "0"}},
        [{"inputs": {"novelty_amount": "100"}, "expected": "100"}],
    )
    f["AUX_TRANSPORTE"] = world.formula(
        "AUX_TRANSPORTE",
        "if(salary <= 2 * param_smmlv, round(param_aux_transporte * days_worked / period_days, 2, 'HALF_UP'), 0)",
        {"salary": MONEY, "days_worked": DEC, "period_days": DEC, "param_smmlv": DEC, "param_aux_transporte": DEC},
        [
            {
                "inputs": {**base, "salary": "1000", "param_smmlv": "1000", "param_aux_transporte": "100"},
                "expected": "100",
            },
            {
                "inputs": {**base, "salary": "2001", "param_smmlv": "1000", "param_aux_transporte": "100"},
                "expected": "0",
            },
        ],
    )
    f["SALUD_EMP"] = world.formula(
        "SALUD_EMP",
        "round(percent(SALARIO + BONO, param_salud_empleado), 2, 'HALF_UP')",
        {"param_salud_empleado": {"type": "percent"}},
        [{"inputs": {**base, "novelty_amount": "0", "param_salud_empleado": "4"}, "expected": "120000"}],
        depends_on=["SALARIO", "BONO"],
    )
    f["PENSION_EMP"] = world.formula(
        "PENSION_EMP",
        "round(percent(SALARIO, param_pension_empleado), 2, 'HALF_UP')",
        {"param_pension_empleado": {"type": "percent"}},
        [{"inputs": {**base, "param_pension_empleado": "4"}, "expected": "120000"}],
        depends_on=["SALARIO"],
    )
    f["AHORRO"] = world.formula(
        "AHORRO",
        "round(percent(SALARIO, 2), 2, 'HALF_UP')",
        {},
        [{"inputs": base, "expected": "60000"}],
        depends_on=["SALARIO"],
    )
    f["PENSION_PATRONAL"] = world.formula(
        "PENSION_PATRONAL",
        "round(percent(SALARIO, param_pension_empleador), 2, 'HALF_UP')",
        {"param_pension_empleador": {"type": "percent"}},
        [{"inputs": {**base, "param_pension_empleador": "12"}, "expected": "360000"}],
        depends_on=["SALARIO"],
    )
    types = {
        "SALARIO": "DEVENGO",
        "BONO": "DEVENGO",
        "AUX_TRANSPORTE": "DEVENGO",
        "SALUD_EMP": "DEDUCCION",
        "PENSION_EMP": "DEDUCCION",
        "AHORRO": "DEDUCCION",
        "PENSION_PATRONAL": "APORTE",
    }
    epay = {
        "SALARIO": "Basico",
        "BONO": "Bonificacion",
        "AUX_TRANSPORTE": "Transporte",
        "SALUD_EMP": "Salud",
        "PENSION_EMP": "FondoPension",
        "AHORRO": "OtraDeduccion",
    }
    concepts = {
        code: world.concept(code, types[code], formula=f[code], electronic_payroll_code=epay.get(code))
        for code in ("SALARIO", "BONO", "AUX_TRANSPORTE", "SALUD_EMP", "PENSION_EMP", "AHORRO", "PENSION_PATRONAL")
    }
    return {"formulas": f, "concepts": concepts}
