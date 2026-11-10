"""Regulatory payroll cases with the P06 2026 parameter seed (fixture values, NOT LEGALLY VERIFIED).

Legal golden cases stay a registered blocker until EG-7 (shared/11 §Payroll): these cases prove the
code paths and the fail-closed behaviour with the seed's statuses, never legal compliance.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from typing import Any

import pytest

from kernel.errors import DomainError
from payroll.testing.fixtures import standard_user_concepts

pytestmark = pytest.mark.django_db

JAN = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
SMMLV_2026 = D("1750905")  # P06 seed, status DISPUTED (R-LB-01)


def _lines(world: Any, salary: D) -> dict[str, D]:
    standard_user_concepts(world)
    world.employee(str(salary))
    run = world.calculate(world.run(world.period(*JAN)))
    return {ln.concept_code: ln.amount for ln in world.payslips(run)[0].lines.all()}


@pytest.mark.parametrize(
    ("salary", "transport"),
    [(2 * SMMLV_2026, D("249095")), (2 * SMMLV_2026 + 1, D("0")), (SMMLV_2026, D("249095"))],
    ids=["exactly-2-SMMLV", "2-SMMLV-plus-1-peso", "1-SMMLV"],
)
def test_T_PAY_FORMULA_03_transport_allowance_threshold_from_parameters(
    payroll_world: Any, salary: D, transport: D
) -> None:
    lines = _lines(payroll_world, salary)
    assert lines.get("AUX_TRANSPORTE", D(0)) == transport


def test_T_PAY_FORMULA_02_contributions_from_the_parameter_store(payroll_world: Any) -> None:
    lines = _lines(payroll_world, D("3000000"))
    assert lines["SALUD_EMP"] == D("120000.0000")  # ss.salud.empleado 4 % (VERIFIED-PRIMARY, no review stamp)
    assert lines["PENSION_EMP"] == D("120000.0000")
    assert lines["PENSION_PATRONAL"] == D("360000.0000")  # ss.pension.empleador 12 %


@pytest.mark.parametrize("year", [2027, 2028])
def test_T_PAY_FORMULA_03_dates_without_parameters_fail_closed(payroll_world: Any, year: int) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.run(w.period(dt.date(year, 3, 1), dt.date(year, 3, 31)))
    with pytest.raises(DomainError) as exc:
        w.calculate(run)
    assert exc.value.code == "payroll_parameter_unknown"
