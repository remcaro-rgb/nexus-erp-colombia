"""P22c — T-PAY-FORMULA-05 feature e2e (gate G3): Talento › Nómina › Conceptos y fórmulas → create a concept with a
formula → simulate → run payroll → explain on the payslip (F-PAY-FORMULA, D-025).

Runner: ``qa_harness.e2e`` (real Django on loopback over a real PostgreSQL world, real P08 shell + P13b pages in system
Chrome). Spec: ``tests/e2e/features/T-PAY-FORMULA-05.concepts-explain.spec.ts``.

World (all real implementations, synthetic data, DEV only): the IAM synthetic world with the real P04 policy engine and
local OIDC bearer tokens; the real P07 formula lifecycle (domain ``payroll``: author ≠ approver); the real P06 parameter
store seeded with ``tax/fixtures/parameters_2026.yaml`` (no legal-review stamp → NOT LEGALLY VERIFIED); the real P13a
HR directory (``hr.directory.DbPayrollEmployeeSource``) over an approved synthetic contract; the P13b payroll services.
The base concepts are the payroll kit's ``standard_user_concepts`` (``payroll.testing.fixtures``); no parameter value is
written here. The run is calculated and approved (SoD, D-023); posting is out of the acceptance text and not exercised.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)

#: payroll admin (author of formulas/concepts, calculates the run) and the payroll approver (D-023)
AUTHOR_GRANTS = [
    "payroll.concept.*",
    "payroll.period.*",
    "payroll.novelty.*",
    "payroll.run.read",
    "payroll.run.create",
    "payroll.run.update",
    "payroll.payslip.read",
    "payroll.payslip.export",
    "workflow.formula.read",
    "workflow.formula.create",
    "workflow.formula.update",
    "hr.employee.read",
]
APPROVER_GRANTS = [
    "payroll.concept.read",
    "payroll.period.read",
    "payroll.run.read",
    "payroll.run.approve",
    "payroll.payslip.read",
    "workflow.formula.read",
    "workflow.formula.approve",
]


@dataclass
class PayWorld:
    """What ``qa_harness.e2e.tokens`` needs (``world``, ``issuer``) plus the payroll kit world."""

    world: Any
    issuer: Any
    kit: Any  # payroll.testing.fixtures.PayrollWorld over real contexts/services

    def company(self) -> uuid.UUID:
        return self.world.companies["ALFA"]


@contextmanager
def pay_world() -> Iterator[PayWorld]:
    from django.core.cache import cache

    from iam.oidc import verifier
    from iam.oidc.local import LocalIssuer
    from iam.testing.world import make_world
    from kernel.uow import run_in_unit_of_work
    from payroll.testing.fixtures import PayrollWorld
    from purchasing.requisitions.tests.kit import ISSUER
    from tax.parameters import seed as parameter_seed

    issuer = LocalIssuer(ISSUER)
    verifier.set_key_provider(issuer, ISSUER)
    cache.clear()
    try:
        world = make_world()
        world.add_role("nomina_admin_e2e", AUTHOR_GRANTS, max_cls="restricted-pii")
        world.add_role("nomina_aprobador_e2e", APPROVER_GRANTS, max_cls="restricted-pii")
        world.add_user("nomina", ["ALFA"], subject=str(uuid.uuid4()))
        world.add_user("jefe_nomina", ["ALFA"], subject=str(uuid.uuid4()))
        world.assign("nomina", "nomina_admin_e2e", "ALFA")
        world.assign("jefe_nomina", "nomina_aprobador_e2e", "ALFA")
        author = world.ctx(["ALFA"], principal=world.users["nomina"].id)
        approver = world.ctx(["ALFA"], principal=world.users["jefe_nomina"].id)
        run_in_unit_of_work(lambda uow: parameter_seed.load_defaults(uow), world.ctx(["ALFA"]))
        kit = PayrollWorld(author, approver, world.companies["ALFA"], policy=None, employees=None, posting=None)
        yield PayWorld(world, issuer, kit)
    finally:
        verifier.set_key_provider(None)
        cache.clear()


def _employee(w: PayWorld, code: str, name: str, salary: str, start: dt.date) -> Any:
    from hr.models import Employee, EmploymentContract
    from kernel import ids
    from kernel.uow import unit_of_work

    with unit_of_work(w.world.ctx(["ALFA"])):
        emp = Employee.objects.create(
            tenant_id=w.world.tenant_id,
            company_id=w.company(),
            code=code,
            full_name=name,
            document_type="CC",
            document_number=str(10_000_000 + ids.new_id().int % 9_000_000),
            email=f"{code.lower()}@example.invalid",
        )
        EmploymentContract.objects.create(
            tenant_id=w.world.tenant_id,
            company_id=w.company(),
            number=f"CT-{code}",
            document_date=start,
            employee=emp,
            contract_type="INDEFINIDO",
            salary=Decimal(salary),
            start_date=start,
        )
    return emp


def world_pay_formula(w: PayWorld) -> dict[str, Any]:
    """Kit concepts (SALARIO, BONO, AUX_TRANSPORTE, SALUD_EMP, PENSION_EMP, AHORRO, PENSION_PATRONAL; formulas approved
    by the approver), one synthetic employee with an approved contract and the period 2026-10 (user ``nomina``)."""
    from payroll.testing.fixtures import standard_user_concepts

    standard_user_concepts(w.kit)
    emp = _employee(w, "EMP-E2E-1", "Ana Sintética (SINTÉTICO)", "3000000", dt.date(2025, 1, 2))
    period = w.kit.period(dt.date(2026, 10, 1), dt.date(2026, 10, 31), code="2026-10")
    return {
        "user": "nomina",
        "tokens": e2e.tokens(w, "nomina", "jefe_nomina"),
        "data": {
            "company_id": str(w.company()),
            "employee_id": str(emp.id),
            "employee_name": emp.full_name,
            "period_id": str(period.id),
            "period_code": period.code,
            "salary": "3000000",
        },
    }


def test_T_PAY_FORMULA_05_ui_concept_formula_simulate_run_and_payslip_explain() -> None:
    e2e.run_feature(
        "T-PAY-FORMULA-05",
        world_pay_formula,
        augment=True,
        world=pay_world,
        known_gaps=("DEF-P22-060", "DEF-P22-061", "DEF-P22-062", "DEF-P22-063", "DEF-P22-064"),
    )
