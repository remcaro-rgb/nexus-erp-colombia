"""T-FORMULA-ENGINE-01 — golden evaluation suite (≥ 200 cases) incl. rounding modes and Decimal precision."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, evaluate_expression
from workflow.formula.errors import FormulaDivisionByZero

MODES = ["HALF_UP", "HALF_EVEN", "HALF_DOWN", "UP", "DOWN", "CEILING", "FLOOR", "05UP"]
# value, places, expected per mode (in MODES order); reference: IEEE 754 / General Decimal Arithmetic.
ROUNDING_MATRIX = [
    ("2.5", 0, ["3", "2", "2", "3", "2", "3", "2", "2"]),
    ("-2.5", 0, ["-3", "-2", "-2", "-3", "-2", "-2", "-3", "-2"]),
    ("1.005", 2, ["1.01", "1.00", "1.00", "1.01", "1.00", "1.01", "1.00", "1.01"]),
    ("2.675", 2, ["2.68", "2.68", "2.67", "2.68", "2.67", "2.68", "2.67", "2.67"]),
    ("0.125", 2, ["0.13", "0.12", "0.12", "0.13", "0.12", "0.13", "0.12", "0.12"]),
    ("-0.125", 2, ["-0.13", "-0.12", "-0.12", "-0.13", "-0.12", "-0.12", "-0.13", "-0.12"]),
    ("1.15", 1, ["1.2", "1.2", "1.1", "1.2", "1.1", "1.2", "1.1", "1.1"]),
    ("-1.15", 1, ["-1.2", "-1.2", "-1.1", "-1.2", "-1.1", "-1.1", "-1.2", "-1.1"]),
    ("7.45", 1, ["7.5", "7.4", "7.4", "7.5", "7.4", "7.5", "7.4", "7.4"]),
    ("0.051", 2, ["0.05", "0.05", "0.05", "0.06", "0.05", "0.06", "0.05", "0.06"]),
]
ROUNDING_CASES = [
    (f"round(x, {places}, '{mode}')", {"x": D(value)}, D(expected[i]))
    for value, places, expected in ROUNDING_MATRIX
    for i, mode in enumerate(MODES)
]

N = D
GOLDEN: list[tuple[str, dict[str, object], object]] = [
    # literals and arithmetic
    ("0", {}, N("0")),
    ("42", {}, N("42")),
    ("3.14159", {}, N("3.14159")),
    ("1 + 2", {}, N("3")),
    ("1 + 2 * 3", {}, N("7")),
    ("(1 + 2) * 3", {}, N("9")),
    ("10 - 4 - 3", {}, N("3")),
    ("100 / 4 / 5", {}, N("5")),
    ("2 * 3 + 4 * 5", {}, N("26")),
    ("-5 + 3", {}, N("-2")),
    ("--5", {}, N("5")),
    ("-(2 + 3)", {}, N("-5")),
    ("+7", {}, N("7")),
    ("7 % 3", {}, N("1")),
    ("-7 % 3", {}, N("-1")),
    ("7.5 % 2", {}, N("1.5")),
    ("0.1 + 0.2", {}, N("0.3")),
    ("1.10 + 2.205", {}, N("3.305")),
    ("1.5 * 1.5", {}, N("2.25")),
    ("0.07 * 100", {}, N("7.00")),
    ("1000000 * 1000000", {}, N("1000000000000")),
    ("123456789.123456789 + 0.000000001", {}, N("123456789.123456790")),
    ("10 / 4", {}, N("2.5")),
    ("1 / 8", {}, N("0.125")),
    ("2 - 2", {}, N("0")),
    ("0 * 99999", {}, N("0")),
    ("5 - 10", {}, N("-5")),
    ("3 * -2", {}, N("-6")),
    ("-3 * -2", {}, N("6")),
    ("12 / -4", {}, N("-3")),
    # Decimal precision 28
    ("1 / 3", {}, N("0.3333333333333333333333333333")),
    ("2 / 3", {}, N("0.6666666666666666666666666667")),
    ("1 / 7", {}, N("0.1428571428571428571428571429")),
    ("1 / 3 * 3", {}, N("0.9999999999999999999999999999")),
    ("10 / 3", {}, N("3.333333333333333333333333333")),
    ("100 / 7", {}, N("14.28571428571428571428571429")),
    ("1 / 9", {}, N("0.1111111111111111111111111111")),
    ("22 / 7", {}, N("3.142857142857142857142857143")),
    ("1 / 1024", {}, N("0.0009765625")),
    ("99999999999999999999999999.99 + 0.01", {}, N("100000000000000000000000000.00")),
    ("round(1 / 3, 10)", {}, N("0.3333333333")),
    ("round(2 / 3, 4)", {}, N("0.6667")),
    ("round(2 / 3, 0)", {}, N("1")),
    # comparisons and booleans
    ("1 < 2", {}, True),
    ("2 < 1", {}, False),
    ("2 <= 2", {}, True),
    ("3 >= 4", {}, False),
    ("5 > 4", {}, True),
    ("1 == 1.000", {}, True),
    ("0.1 + 0.2 == 0.3", {}, True),
    ("1 != 2", {}, True),
    ("1 <> 1", {}, False),
    ("true", {}, True),
    ("false", {}, False),
    ("TRUE", {}, True),
    ("not true", {}, False),
    ("not not true", {}, True),
    ("true and false", {}, False),
    ("true or false", {}, True),
    ("false or false", {}, False),
    ("1 < 2 and 2 < 3", {}, True),
    ("1 > 2 or 2 > 3", {}, False),
    ("not (1 > 2)", {}, True),
    ("true and not false", {}, True),
    ("false and (1 / 0 > 1)", {}, False),  # short-circuit: the division never runs
    ("true or (1 / 0 > 1)", {}, True),
    ("'abc' == 'abc'", {}, True),
    ("'abc' != 'abd'", {}, True),
    ("'a' < 'b'", {}, True),
    ("\"x\" == 'x'", {}, True),
    ("'it\\'s' == \"it's\"", {}, True),
    ("'Bogotá' == 'Bogotá'", {}, True),
    # if()
    ("if(true, 1, 2)", {}, N("1")),
    ("if(false, 1, 2)", {}, N("2")),
    ("if(1 > 2, 'a', 'b')", {}, "b"),
    ("if(x > 100, x * 0.1, 0)", {"x": N("150")}, N("15.0")),
    ("if(x > 100, x * 0.1, 0)", {"x": N("50")}, N("0")),
    ("if(x == 0, 0, 10 / x)", {"x": N("0")}, N("0")),  # lazy branch avoids division by zero
    ("if(x == 0, 0, 10 / x)", {"x": N("4")}, N("2.5")),
    ("if(a, if(b, 1, 2), 3)", {"a": True, "b": False}, N("2")),
    ("if(a, if(b, 1, 2), 3)", {"a": False, "b": True}, N("3")),
    ("IF(true, 5, 6)", {}, N("5")),
    # min/max/abs/clamp/floor/ceil/percent
    ("min(3, 1, 2)", {}, N("1")),
    ("max(3, 1, 2)", {}, N("3")),
    ("min(5)", {}, N("5")),
    ("max(-1, -5)", {}, N("-1")),
    ("min(0.1, 0.01)", {}, N("0.01")),
    ("abs(-4.5)", {}, N("4.5")),
    ("abs(4.5)", {}, N("4.5")),
    ("abs(0)", {}, N("0")),
    ("clamp(5, 0, 10)", {}, N("5")),
    ("clamp(-5, 0, 10)", {}, N("0")),
    ("clamp(50, 0, 10)", {}, N("10")),
    ("clamp(x, 1000, 5000)", {"x": N("4999.99")}, N("4999.99")),
    ("floor(2.7)", {}, N("2")),
    ("floor(-2.1)", {}, N("-3")),
    ("floor(3)", {}, N("3")),
    ("ceil(2.1)", {}, N("3")),
    ("ceil(-2.7)", {}, N("-2")),
    ("ceil(4.0)", {}, N("4")),
    ("percent(1000, 19)", {}, N("190")),
    ("percent(250000, 0.4)", {}, N("1000")),
    ("percent(100, 2.5)", {}, N("2.5")),
    ("percent(0, 50)", {}, N("0")),
    ("percent(x, p)", {"x": N("1234.56"), "p": N("10")}, N("123.456")),
    ("max(min(x, 100), 10)", {"x": N("500")}, N("100")),
    ("floor(10 / 3)", {}, N("3")),
    ("ceil(10 / 3)", {}, N("4")),
    # round defaults, negative places (rounding to hundreds / thousands of pesos)
    ("round(2.345)", {}, N("2")),
    ("round(2.5)", {}, N("3")),
    ("round(2.345, 2)", {}, N("2.35")),
    ("round(-2.345, 2)", {}, N("-2.35")),
    ("round(1234.5, -2)", {}, N("1200")),
    ("round(1250, -2)", {}, N("1300")),
    ("round(1250, -2, 'HALF_EVEN')", {}, N("1200")),
    ("round(987654, -3)", {}, N("988000")),
    ("round(987654, -3, 'DOWN')", {}, N("987000")),
    ("round(x, 2, 'half_up')", {"x": N("0.005")}, N("0.01")),
    ("round(123.456789, 6)", {}, N("123.456789")),
    ("round(1.23, 4)", {}, N("1.2300")),
    ("round(0, 2)", {}, N("0.00")),
    ("round(x, n)", {"x": N("9.8765"), "n": N("1")}, N("9.9")),
    # inputs of every type
    ("a + b", {"a": N("1.25"), "b": N("2.75")}, N("4.00")),
    ("a * b", {"a": 3, "b": N("0.5")}, N("1.5")),
    ("qty * price", {"qty": N("2.500000"), "price": N("1999.99")}, N("4999.97500000")),
    ("subtotal - discount", {"subtotal": N("100000"), "discount": N("2500.50")}, N("97499.50")),
    ("flag", {"flag": True}, True),
    ("name == 'VIP'", {"name": "VIP"}, True),
    ("segment != 'A'", {"segment": "B"}, True),
    ("d1 < d2", {"d1": dt.date(2026, 1, 1), "d2": dt.date(2026, 1, 2)}, True),
    ("d1 == d2", {"d1": dt.date(2026, 1, 1), "d2": dt.date(2026, 1, 1)}, True),
    # dates
    ("days_between(date('2026-01-01'), date('2026-12-31'))", {}, N("364")),
    ("days_between(date('2024-02-28'), date('2024-03-01'))", {}, N("2")),  # leap year
    ("days_between(date('2025-02-28'), date('2025-03-01'))", {}, N("1")),
    ("days_between(date('2026-03-01'), date('2026-01-01'))", {}, N("-59")),
    ("days_between(d, d)", {"d": dt.date(2026, 11, 9)}, N("0")),
    ("year(date('2026-11-09'))", {}, N("2026")),
    ("month(date('2026-11-09'))", {}, N("11")),
    ("day(date('2026-11-09'))", {}, N("9")),
    ("month(d) == 12", {"d": dt.date(2026, 12, 15)}, True),
    ("add_days(date('2026-12-30'), 3) == date('2027-01-02')", {}, True),
    ("add_days(d, -1)", {"d": dt.date(2026, 3, 1)}, dt.date(2026, 2, 28)),
    ("date('2026-01-01') < date('2026-01-02')", {}, True),
    (
        "if(days_between(d1, d2) > 30, 'vencida', 'al dia')",
        {"d1": dt.date(2026, 1, 1), "d2": dt.date(2026, 3, 1)},
        "vencida",
    ),
    # realistic business formulas (synthetic rates, not legal parameters)
    ("round(base * 0.19, 2)", {"base": N("1000000")}, N("190000.00")),
    ("round(base * tasa / 1000, 0)", {"base": N("2500000"), "tasa": N("9.66")}, N("24150")),
    ("if(monto >= 5000000, 'GERENCIA', if(monto >= 1000000, 'JEFE', 'AUTO'))", {"monto": N("1500000")}, "JEFE"),
    ("if(monto >= 5000000, 'GERENCIA', if(monto >= 1000000, 'JEFE', 'AUTO'))", {"monto": N("999999.99")}, "AUTO"),
    ("if(monto >= 5000000, 'GERENCIA', if(monto >= 1000000, 'JEFE', 'AUTO'))", {"monto": N("5000000")}, "GERENCIA"),
    ("costo * (1 + margen / 100)", {"costo": N("80000"), "margen": N("25")}, N("100000")),
    ("round(costo / (1 - margen / 100), 0, 'CEILING')", {"costo": N("80000"), "margen": N("30")}, N("114286")),
    ("salario / 30 * dias", {"salario": N("3000000"), "dias": N("15")}, N("1500000")),
    ("round(salario / 240 * horas * 1.25, 0)", {"salario": N("2400000"), "horas": N("10")}, N("125000")),
    ("max(0, ingreso - deducciones)", {"ingreso": N("100"), "deducciones": N("150")}, N("0")),
    ("monto > 1000000 and centro == 'CC-100'", {"monto": N("2000000"), "centro": "CC-100"}, True),
    ("monto > 1000000 and centro == 'CC-100'", {"monto": N("2000000"), "centro": "CC-200"}, False),
    ("not (monto > 1000000) or urgente", {"monto": N("2000000"), "urgente": True}, True),
    (
        "round(precio * cantidad * (1 - descuento / 100), 2)",
        {"precio": N("12345.67"), "cantidad": N("3"), "descuento": N("7.5")},
        N("34259.23"),
    ),
    ("clamp(round(x * 1.1, 2), 0, 1000)", {"x": N("999")}, N("1000")),
    ("abs(a - b) <= 0.01", {"a": N("100.004"), "b": N("100")}, True),
    ("percent(round(base, -3), 4)", {"base": N("1234567")}, N("49400")),
    ("(a + b + c) / 3", {"a": N("1"), "b": N("2"), "c": N("4")}, N("2.333333333333333333333333333")),
    ("1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10", {}, N("55")),
    ("1 * 2 * 3 * 4 * 5 * 6 * 7 * 8 * 9 * 10", {}, N("3628800")),
    ("((((((1 + 1) * 2) + 1) * 2) + 1) * 2)", {}, N("22")),
    ("   1   +\n  2\t", {}, N("3")),
    ("max(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)", {}, N("16")),
]

ALL_CASES = GOLDEN + ROUNDING_CASES


def test_T_FORMULA_ENGINE_01_suite_has_at_least_200_cases() -> None:
    assert len(ALL_CASES) >= 200
    assert len({(e, tuple(sorted((k, str(v)) for k, v in i.items()))) for e, i, _ in ALL_CASES}) == len(ALL_CASES)


@pytest.mark.parametrize(("expression", "inputs", "expected"), ALL_CASES, ids=[c[0][:60] for c in ALL_CASES])
def test_T_FORMULA_ENGINE_01_golden(expression: str, inputs: dict[str, object], expected: object) -> None:
    got = evaluate_expression(expression, inputs)
    assert type(got) is type(expected)
    assert got == expected
    if isinstance(expected, D):
        # Same digits, not only the same value (scale matters for money).
        assert str(got) == str(expected) or got.normalize() == expected.normalize()


@pytest.mark.parametrize(("value", "places", "expected"), ROUNDING_MATRIX)
def test_T_FORMULA_ENGINE_01_rounding_modes_keep_scale(value: str, places: int, expected: list[str]) -> None:
    for mode, want in zip(MODES, expected, strict=True):
        got = evaluate_expression(f"round(x, {places}, '{mode}')", {"x": D(value)})
        assert str(got) == want, mode


def test_T_FORMULA_ENGINE_01_division_by_zero_is_explicit_never_nan() -> None:
    for expr in ("1 / 0", "5 % 0", "x / (y - y)", "round(1 / zero, 2)"):
        with pytest.raises(FormulaDivisionByZero) as exc:
            evaluate_expression(expr, {"x": D(1), "y": D(2), "zero": D(0)})
        assert exc.value.code == "formula_division_by_zero"
        assert exc.value.extra["position"] >= 0


@pytest.mark.parametrize(
    ("result_type", "raw", "expected"),
    [
        ("money", "1.23456", "1.2346"),
        ("money", "2.00005", "2.0000"),  # half-even at 4 places (C2-2)
        ("quantity", "1.23456789", "1.234568"),
        ("percent", "19", "19.000000"),
        ("decimal", "1.23456789", "1.23456789"),
    ],
)
def test_T_FORMULA_ENGINE_01_output_precision_by_result_type(result_type: str, raw: str, expected: str) -> None:
    d = FormulaDefinition("f", "x", InputSchema.from_json({"x": "decimal"}), result_type=result_type)
    got = FormulaGraph([d]).evaluate({"x": D(raw)})["f"].value
    assert str(got) == expected


def test_T_FORMULA_ENGINE_01_explicit_output_places_and_rounding() -> None:
    d = FormulaDefinition(
        "precio",
        "costo * 1.3",
        InputSchema.from_json({"costo": "money"}),
        result_type="money",
        output_places=0,
        output_rounding="CEILING",
    )
    res = FormulaGraph([d]).evaluate({"costo": D("1000.01")})["precio"]
    assert res.value == D("1301") and str(res.raw) == "1300.013"


def test_T_FORMULA_ENGINE_01_floats_are_refused_as_inputs() -> None:
    from workflow.formula.errors import FormulaTypeError

    with pytest.raises(FormulaTypeError):
        evaluate_expression("x + 1", {"x": 0.1}, schema={"x": "decimal"})


def test_T_FORMULA_ENGINE_01_explain_trace_and_snapshot() -> None:
    d = FormulaDefinition(
        "total", "round(qty * price, 2)", InputSchema.from_json({"qty": "quantity", "price": "money"}), "money"
    )
    res = FormulaGraph([d]).evaluate({"qty": "3", "price": "10.555"})["total"]
    assert res.value == D("31.6700")
    snap = res.snapshot()
    assert snap["result"] == "31.6700" and snap["hash"] == d.hash and snap["inputs"] == {"price": "10.555", "qty": "3"}
    exprs = [t["expr"] for t in snap["trace"]]
    assert "qty * price" in exprs and "round(qty * price, 2)" in exprs
