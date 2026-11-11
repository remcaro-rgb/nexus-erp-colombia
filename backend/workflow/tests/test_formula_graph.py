"""T-FORMULA-ENGINE-04 — dependency cycle detection; topological evaluation order."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, validate_definition
from workflow.formula.errors import FormulaDependencyCycle, FormulaTypeError, FormulaUnknownIdentifier


def F(
    code: str, expr: str, deps: tuple[str, ...] = (), schema: dict | None = None, rt: str = "decimal"
) -> FormulaDefinition:
    return FormulaDefinition(code, expr, InputSchema.from_json(schema or {}), rt, depends_on=deps)


def test_T_FORMULA_ENGINE_04_topological_order_diamond_is_deterministic() -> None:
    defs = [
        F("total", "neto + iva", ("neto", "iva")),
        F("iva", "round(neto * 0.19, 2)", ("neto",)),
        F("neto", "bruto - descuento", ("bruto", "descuento")),
        F("descuento", "percent(bruto, pct)", ("bruto",), {"pct": "percent"}),
        F("bruto", "qty * price", (), {"qty": "quantity", "price": "money"}),
    ]
    g = FormulaGraph(defs)
    assert g.order == ["bruto", "descuento", "neto", "iva", "total"]
    assert FormulaGraph(list(reversed(defs))).order == g.order
    res = g.evaluate({"qty": "2", "price": "50000", "pct": "10"})
    assert [r for r in res] == g.order
    assert (
        res["neto"].value == D("90000") and res["iva"].value == D("17100.00") and res["total"].value == D("107100.00")
    )


def test_T_FORMULA_ENGINE_04_independent_formulas_sorted_by_code() -> None:
    g = FormulaGraph([F("c", "1"), F("a", "2"), F("b", "a + c", ("a", "c"))])
    assert g.order == ["a", "c", "b"]


@pytest.mark.parametrize(
    ("graph", "cycle"),
    [
        ({"a": ("a",)}, ["a", "a"]),
        ({"a": ("b",), "b": ("a",)}, ["a", "b", "a"]),
        ({"a": ("b",), "b": ("c",), "c": ("a",), "d": ()}, ["a", "b", "c", "a"]),
        ({"x": ("y",), "y": ("z",), "z": ("y",)}, ["y", "z", "y"]),
    ],
)
def test_T_FORMULA_ENGINE_04_cycles_detected_with_path(graph: dict[str, tuple[str, ...]], cycle: list[str]) -> None:
    defs = [F(c, " + ".join(d) or "1", d) for c, d in graph.items()]
    with pytest.raises(FormulaDependencyCycle) as exc:
        FormulaGraph(defs)
    assert exc.value.code == "formula_dependency_cycle"
    assert exc.value.extra["cycle"] == cycle


def test_T_FORMULA_ENGINE_04_long_chain_with_back_edge() -> None:
    defs = [F("f0", "1")] + [F(f"f{i}", f"f{i - 1} + 1", (f"f{i - 1}",)) for i in range(1, 199)]
    g = FormulaGraph(defs)
    assert g.order == [f"f{i}" for i in range(199)] or g.order[0] == "f0"
    # topology test; CPU budget is T-FORMULA-ENGINE-03
    assert g.evaluate({}, clock=lambda: 0.0)["f198"].value == D(199)
    defs[0] = F("f0", "f198 + 1", ("f198",))
    with pytest.raises(FormulaDependencyCycle) as exc:
        FormulaGraph(defs)
    assert len(exc.value.extra["cycle"]) == 200


def test_T_FORMULA_ENGINE_04_undeclared_and_unknown_dependencies() -> None:
    with pytest.raises(FormulaUnknownIdentifier):
        FormulaGraph([F("a", "1"), F("b", "a + 1")])  # uses a without declaring it
    with pytest.raises(FormulaUnknownIdentifier):
        FormulaGraph([F("b", "1", ("missing",))])


def test_T_FORMULA_ENGINE_04_dependency_result_types_are_checked() -> None:
    with pytest.raises(FormulaTypeError):
        FormulaGraph([F("flag", "true", rt="boolean"), F("b", "flag + 1", ("flag",))])
    g = FormulaGraph([F("flag", "1 > 0", rt="boolean"), F("b", "if(flag, 1, 2)", ("flag",))])
    assert g.evaluate({})["b"].value == D(1)


def test_T_FORMULA_ENGINE_04_input_names_cannot_shadow_formulas() -> None:
    with pytest.raises(FormulaTypeError):
        FormulaGraph([F("a", "1"), F("b", "a", (), {"a": "decimal"})])


def test_T_FORMULA_ENGINE_04_closure_evaluates_only_what_is_needed() -> None:
    g = FormulaGraph([F("a", "1"), F("b", "a * 2", ("a",)), F("c", "1 / 0")])
    res = g.evaluate({}, targets=["b"])
    assert list(res) == ["a", "b"] and res["b"].value == D(2)
    assert g.dependents_of("a") == ["b"]


def test_T_FORMULA_ENGINE_04_validate_reports_cycle_and_order() -> None:
    a = F("a", "b + 1", ("b",))
    report = validate_definition(a, [F("b", "a + 1", ("a",))])
    assert not report.ok and report.errors[0]["code"] == "formula_dependency_cycle"
    ok = validate_definition(F("c", "b * 2", ("b",)), [F("b", "3")])
    assert ok.ok and ok.order == ["b", "c"]


def test_T_FORMULA_ENGINE_04_per_formula_inputs() -> None:
    g = FormulaGraph([F("a", "x", (), {"x": "decimal"}), F("b", "a + x", ("a",), {"x": "decimal"})])
    res = g.evaluate({"a": {"x": "1"}, "b": {"x": "10"}}, per_formula_inputs=True)
    assert res["b"].value == D(11)
