"""T-PAY-FORMULA-04 — the adversarial corpus of F-FORMULA-ENGINE (T-FORMULA-ENGINE-02) is rejected in
the payroll domain: on concept save (service and API) and in the payroll calculation path."""

from __future__ import annotations

import ast
import datetime as dt
import re
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from kernel import ids
from kernel.errors import DomainError
from payroll.concepts.graph import ConceptSpec, FormulaRef, check_expression, compile_graph
from payroll.concepts.parameters import ParameterSet
from payroll.engine import PeriodInfo, calculate_employee
from payroll.interfaces import PayrollEmployee
from tax.testing.fixtures import ApiClient
from workflow.formula.errors import FORMULA_ERROR_CODES
from workflow.tests.test_formula_adversarial import CORPUS

#: The corpus is written against inputs x, y, s; in payroll they are salary, days_worked, contract_type.
RENAME = {"x": "salary", "y": "days_worked", "s": "contract_type"}
PAYROLL_SCHEMA = {"salary": "money", "days_worked": "decimal", "contract_type": "string"}
PAYROLL_CODES = {*FORMULA_ERROR_CODES, "payroll_parameter_unknown"}


def to_payroll(expression: str) -> str:
    return re.sub(r"\b([xys])\b", lambda m: RENAME[m.group(1)], expression)


def _evaluate(expression: str) -> None:
    ref = FormulaRef("f", "ADV", 1, "h", expression, {"fields": PAYROLL_SCHEMA}, "decimal", None, "HALF_EVEN")
    compiled = compile_graph([ConceptSpec("c", "ADV", "DEVENGO", (), ref)])
    employee = PayrollEmployee(ids.new_id(), ids.new_id(), "Sintético", ids.new_id(), "INDEFINIDO", D("1"))
    period = PeriodInfo(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    calculate_employee(compiled, employee, period, {}, ParameterSet(employee.company_id, period.end))


def test_T_PAY_FORMULA_04_corpus_is_the_formula_engine_corpus() -> None:
    assert len(CORPUS) >= 140


@pytest.mark.parametrize(("expression", "code"), CORPUS, ids=[repr(c[0])[:50] for c in CORPUS])
def test_T_PAY_FORMULA_04_adversarial_rejected_on_save(expression: str, code: str) -> None:
    payroll_expression = to_payroll(expression)
    try:
        check_expression(payroll_expression, {"fields": PAYROLL_SCHEMA}, (), "decimal")
    except DomainError as exc:
        assert exc.code in PAYROLL_CODES, exc.detail
        assert exc.status == 422
        return
    # accepted statically (only evaluation-time attacks): the calculation path must refuse it
    with pytest.raises(DomainError) as run_exc:
        _evaluate(payroll_expression)
    assert run_exc.value.code == code, run_exc.value.detail


@pytest.mark.parametrize(("expression", "code"), CORPUS, ids=[repr(c[0])[:50] for c in CORPUS])
def test_T_PAY_FORMULA_04_adversarial_rejected_in_payroll_calculation(expression: str, code: str) -> None:
    with pytest.raises(DomainError) as exc:
        _evaluate(to_payroll(expression))
    assert exc.value.code == code, exc.value.detail
    assert exc.value.status == 422


@pytest.mark.django_db
def test_T_PAY_FORMULA_04_adversarial_formulas_rejected_by_the_concepts_api(payroll_world: Any) -> None:
    w = payroll_world
    api = ApiClient(w.author)
    from workflow.formula.lifecycle import FormulaService

    for i, (expression, _code) in enumerate(CORPUS[:60]):
        draft = FormulaService().create(w.author, w.company_id, {
            "domain": "payroll", "code": f"ADV{i}", "expression": to_payroll(expression),
            "inputs": {"fields": PAYROLL_SCHEMA}, "result_type": "decimal", "tests": [],
        })  # fmt: skip
        resp = api.command(
            "post", "/api/v1/payroll/concepts",
            {"code": f"ADV{i}", "name": "adversarial", "concept_type": "DEVENGO", "formula_id": str(draft.id)},
            **{"X-Company-Id": str(w.company_id)},
        )  # fmt: skip
        assert resp.status_code == 422, (expression, resp.json())
        assert resp.json()["code"] in PAYROLL_CODES, (expression, resp.json())
    listing = api.get("/api/v1/payroll/concepts", HTTP_X_COMPANY_ID=str(w.company_id)).json()
    assert listing["items"] == []


@pytest.mark.django_db
def test_T_PAY_FORMULA_04_formula_of_another_domain_or_company_rejected(payroll_world: Any) -> None:
    w = payroll_world
    from workflow.formula.lifecycle import FormulaService

    pricing = FormulaService().create(w.author, w.company_id, {
        "domain": "pricing", "code": "PRECIO", "expression": "1", "inputs": {}, "result_type": "decimal",
    })  # fmt: skip
    with pytest.raises(DomainError) as exc:
        w.concept("PRECIO", "DEVENGO", formula=pricing)
    assert exc.value.code == "validation_failed"
    with pytest.raises(DomainError):
        w.concept("AJENO", "DEVENGO", formula_id=str(ids.new_id()))


def test_T_PAY_FORMULA_04_static_scan_no_eval_exec_in_payroll() -> None:
    root = Path(__file__).resolve().parents[1]
    banned = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars"}
    problems = []
    for path in sorted(p for p in root.rglob("*.py") if "tests" not in p.relative_to(root).parts):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
                problems.append(f"{path.name}:{node.lineno} {node.func.id}()")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                problems += [f"{path.name}: imports {n}" for n in names if n.split(".")[0] in {"subprocess", "pickle", "marshal", "ctypes"}]
    assert problems == []
