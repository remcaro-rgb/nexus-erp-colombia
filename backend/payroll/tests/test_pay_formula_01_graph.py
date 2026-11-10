"""T-PAY-FORMULA-01 — user-defined earning and deduction concepts evaluated in topological order;
a dependency cycle is rejected on save (D-025, F-PAY-FORMULA)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from typing import Any

import pytest

from kernel.errors import DomainError
from kernel.testing.tenancy import bound
from payroll.concepts import service
from payroll.models import PayrollConcept
from payroll.testing.fixtures import DEC, MONEY, standard_user_concepts

pytestmark = pytest.mark.django_db

JAN = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def _calculated(world: Any, salary: str = "2000000", bono: str | None = "100000") -> tuple[Any, Any, Any]:
    built = standard_user_concepts(world)
    period = world.period(*JAN, code="2026-01")
    emp = world.employee(salary)
    if bono is not None:
        world.novelty(period, built["concepts"]["BONO"], emp, amount=bono)
    run = world.calculate(world.run(period))
    return built, run, world.payslips(run)[0]


def test_T_PAY_FORMULA_01_user_concepts_evaluated_in_topological_order(payroll_world: Any) -> None:
    built, run, slip = _calculated(payroll_world)
    order = run.graph_order
    # dependencies first, ties broken by code (deterministic); AHORRO sorts first but depends on SALARIO
    assert order.index("SALARIO") < order.index("AHORRO")
    assert order.index("SALARIO") < order.index("SALUD_EMP") and order.index("BONO") < order.index("SALUD_EMP")
    assert order == ["AUX_TRANSPORTE", "BONO", "SALARIO", "AHORRO", "PENSION_EMP", "PENSION_PATRONAL", "SALUD_EMP"]
    lines = {ln.concept_code: ln for ln in slip.lines.all()}
    assert [ln.concept_code for ln in slip.lines.all()] == order  # sequence = topological position
    # values (2026 seed: SMMLV 1,750,905, aux. transporte 249,095, health/pension 4 %, employer pension 12 %)
    assert lines["SALARIO"].amount == D("2000000.0000")
    assert lines["BONO"].amount == D("100000.0000")
    assert lines["AUX_TRANSPORTE"].amount == D("249095.0000")
    assert lines["SALUD_EMP"].amount == D("84000.0000")  # 4 % of SALARIO + BONO (dependency results)
    assert lines["PENSION_EMP"].amount == D("80000.0000")
    assert lines["AHORRO"].amount == D("40000.0000")  # user-defined deduction (company rate)
    assert lines["PENSION_PATRONAL"].amount == D("240000.0000")
    assert slip.earnings == D("2349095.0000") and slip.deductions == D("204000.0000")
    assert slip.net_pay == D("2145095.0000") and slip.employer_cost == D("240000.0000")
    # the explain trace of a dependent line shows the dependency values it read
    trace = " ".join(t["expr"] for t in lines["SALUD_EMP"].trace)
    assert "SALARIO" in trace and "BONO" in trace
    assert lines["SALUD_EMP"].formula_hash == built["formulas"]["SALUD_EMP"].hash
    assert lines["SALUD_EMP"].formula_version == 1


def test_T_PAY_FORMULA_01_cycle_rejected_on_save(payroll_world: Any) -> None:
    w = payroll_world
    a = w.formula("CONC_A", "salary", {"salary": MONEY}, [{"inputs": {"salary": "1"}, "expected": "1"}])
    b = w.formula("CONC_B", "CONC_A + 1", {}, [{"inputs": {"salary": "1"}, "expected": "2"}], depends_on=["CONC_A"])
    ca = w.concept("CONC_A", "DEVENGO", formula=a)
    w.concept("CONC_B", "DEVENGO", formula=b)
    # A → B → A: the save that closes the cycle is rejected and nothing changes
    with pytest.raises(DomainError) as exc:
        w.uow(lambda u: service.update(u, w.company_id, ca.id, {"depends_on": ["CONC_B"]}, ca.etag))
    assert exc.value.code == "formula_dependency_cycle"
    assert "CONC_A" in exc.value.detail and "CONC_B" in exc.value.detail
    with bound(w.author):
        assert PayrollConcept.objects.get(id=ca.id).depends_on == []
    # a self-dependency and a three-node cycle on create are rejected too
    with pytest.raises(DomainError) as self_dep:
        w.concept("CONC_C", "DEDUCCION", depends_on=["CONC_C"])
    assert self_dep.value.code == "formula_dependency_cycle"
    w.concept("CONC_D", "DEDUCCION", depends_on=["CONC_B"])
    with pytest.raises(DomainError) as three:
        w.uow(lambda u: service.update(u, w.company_id, ca.id, {"depends_on": ["CONC_D"]}, ca.etag))
    assert three.value.code == "formula_dependency_cycle"
    with bound(w.author):
        assert not PayrollConcept.objects.filter(code="CONC_C").exists()


def test_T_PAY_FORMULA_01_unknown_dependency_rejected(payroll_world: Any) -> None:
    with pytest.raises(DomainError) as exc:
        payroll_world.concept("HUERFANO", "DEVENGO", depends_on=["NO_EXISTE"])
    assert exc.value.code == "formula_unknown_identifier"


def test_T_PAY_FORMULA_01_validate_graph_reports_order(payroll_world: Any) -> None:
    standard_user_concepts(payroll_world)
    w = payroll_world
    result = w.uow(lambda u: service.validate_graph(u.ctx, w.company_id, dt.date(2026, 1, 31)))
    assert result["status"] == "valid"
    assert result["data"]["order"][:3] == ["AUX_TRANSPORTE", "BONO", "SALARIO"]
    params = {p["input"]: p for p in result["data"]["parameters"]}
    assert params["param_smmlv"]["code"] == "smmlv" and params["param_smmlv"]["label"] == "NOT LEGALLY VERIFIED"


def test_T_PAY_FORMULA_01_formula_must_be_active_to_calculate(payroll_world: Any) -> None:
    w = payroll_world
    draft = w.formula("PRIMA_X", "salary", {"salary": MONEY}, [{"inputs": {"salary": "1"}, "expected": "1"}],
                      activate=False)  # fmt: skip
    w.concept("PRIMA_X", "DEVENGO", formula=draft)  # saving with a DRAFT formula is allowed
    period = w.period(*JAN)
    w.employee("1500000")
    run = w.run(period)
    with pytest.raises(DomainError) as exc:
        w.calculate(run)
    assert exc.value.code == "formula_not_approved"


def test_T_PAY_FORMULA_01_inputs_outside_the_payroll_catalogue_rejected(payroll_world: Any) -> None:
    w = payroll_world
    bad = w.formula("RARO", "tenant_secret", {"tenant_secret": DEC},
                    [{"inputs": {"tenant_secret": "1"}, "expected": "1"}], result_type="decimal")  # fmt: skip
    with pytest.raises(DomainError) as exc:
        w.concept("RARO", "DEVENGO", formula=bad)
    assert exc.value.code == "formula_unknown_identifier"
