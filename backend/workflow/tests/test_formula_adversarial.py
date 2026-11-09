"""T-FORMULA-ENGINE-02 — adversarial corpus is rejected; no Python eval/exec anywhere (static scan)."""

from __future__ import annotations

import ast
from decimal import Decimal as D
from pathlib import Path

import pytest

from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, evaluate_expression, validate_definition
from workflow.formula.errors import FORMULA_ERROR_CODES, FormulaDependencyCycle, FormulaError

SCHEMA = {"x": "decimal", "y": "decimal", "s": "string"}
INPUTS = {"x": D(1), "y": D(2), "s": "a"}

CORPUS: list[tuple[str, str]] = [
    # imports and builtins
    ("__import__('os').system('id')", "formula_syntax_error"),
    ("__import__", "formula_syntax_error"),
    ("import os", "formula_syntax_error"),
    ("from os import system", "formula_syntax_error"),
    ("eval('1+1')", "formula_syntax_error"),
    ("exec('x=1')", "formula_syntax_error"),
    ("compile('1', 'f', 'eval')", "formula_syntax_error"),
    ("open('/etc/passwd')", "formula_syntax_error"),
    ("globals()", "formula_syntax_error"),
    ("locals()", "formula_syntax_error"),
    ("vars()", "formula_syntax_error"),
    ("getattr(x, 'real')", "formula_syntax_error"),
    ("setattr(x, 'a', 1)", "formula_syntax_error"),
    ("type(x)", "formula_syntax_error"),
    ("print(x)", "formula_syntax_error"),
    ("breakpoint()", "formula_syntax_error"),
    ("os.system('ls')", "formula_syntax_error"),
    ("sys.exit()", "formula_syntax_error"),
    ("subprocess", "formula_syntax_error"),
    ("range(10)", "formula_unknown_identifier"),
    ("sum(x, y)", "formula_unknown_identifier"),
    ("pow(10, 1000)", "formula_unknown_identifier"),
    ("system('ls')", "formula_unknown_identifier"),
    ("sqrt(x)", "formula_unknown_identifier"),
    # attribute access / dunder names
    ("x.real", "formula_syntax_error"),
    ("x.__class__", "formula_syntax_error"),
    ("s.upper()", "formula_syntax_error"),
    ("''.join(s)", "formula_syntax_error"),
    ("().__class__.__bases__[0].__subclasses__()", "formula_syntax_error"),
    ("(1).__add__(2)", "formula_syntax_error"),
    ("__builtins__", "formula_syntax_error"),
    ("__class__", "formula_syntax_error"),
    ("x__y", "formula_syntax_error"),
    ("_private", "formula_syntax_error"),
    ("_", "formula_syntax_error"),
    ("dunder__", "formula_syntax_error"),
    # lambda, comprehension, statements, python syntax
    ("lambda: 1", "formula_syntax_error"),
    ("(lambda x: x)(1)", "formula_syntax_error"),
    ("[i for i in range(10)]", "formula_syntax_error"),
    ("(i for i in x)", "formula_syntax_error"),
    ("{i: i for i in x}", "formula_syntax_error"),
    ("{1, 2}", "formula_syntax_error"),
    ("x if y else s", "formula_syntax_error"),
    ("x = 1", "formula_syntax_error"),
    ("x := 1", "formula_syntax_error"),
    ("x; y", "formula_syntax_error"),
    ("x[0]", "formula_syntax_error"),
    ("s[::-1]", "formula_syntax_error"),
    ("f'{x}'", "formula_syntax_error"),
    ("b'x'", "formula_syntax_error"),
    ("x # comment", "formula_syntax_error"),
    ("@x", "formula_syntax_error"),
    ("...", "formula_syntax_error"),
    ("None", "formula_syntax_error"),
    ("while true", "formula_syntax_error"),
    ("def f(): 1", "formula_syntax_error"),
    ("class A: pass", "formula_syntax_error"),
    ("yield x", "formula_syntax_error"),
    ("await x", "formula_syntax_error"),
    ("x & y", "formula_syntax_error"),
    ("x | y", "formula_syntax_error"),
    ("~x", "formula_syntax_error"),
    ("x ^ y", "formula_syntax_error"),
    ("x // y", "formula_syntax_error"),
    ("`x`", "formula_syntax_error"),
    ("$x", "formula_syntax_error"),
    ("x ? 1 : 2", "formula_syntax_error"),
    ("abs(x)(1)", "formula_syntax_error"),
    ("1 < x < 3", "formula_syntax_error"),
    ("select * from auth_user", "formula_syntax_error"),
    ("x union y", "formula_syntax_error"),
    ("", "formula_syntax_error"),
    ("   ", "formula_syntax_error"),
    ("(", "formula_syntax_error"),
    ("x +", "formula_syntax_error"),
    ("abs(", "formula_syntax_error"),
    ("'unterminated", "formula_syntax_error"),
    ("'bad \\x41 escape'", "formula_syntax_error"),
    ("max(" + ", ".join(["1"] * 17) + ")", "formula_syntax_error"),
    # huge exponents / magnitudes
    ("10 ** 1000", "formula_syntax_error"),
    ("9 ** 9 ** 9", "formula_syntax_error"),
    ("1e308", "formula_syntax_error"),
    ("1E5", "formula_syntax_error"),
    ("1.5e-3", "formula_syntax_error"),
    ("0x41", "formula_syntax_error"),
    ("1_000", "formula_syntax_error"),
    ("1.", "formula_syntax_error"),
    ("9" * 41, "formula_budget_exceeded"),
    ("999999999999999999999999999999 * 999999999999999999999999999999", "formula_budget_exceeded"),
    ("100000000000000000000 * 100000000000000000000", "formula_budget_exceeded"),
    ("round(x, 1000)", "formula_type_error"),
    ("round(x, -1000)", "formula_type_error"),
    ("round(x, 0.5)", "formula_type_error"),
    ("round(x, 2, 'BANKERS')", "formula_type_error"),
    ("round(x, 2, s)", "formula_type_error"),
    ("add_days(date('2026-01-01'), 99999999)", "formula_type_error"),
    # deep nesting
    ("(" * 33 + "1" + ")" * 33, "formula_budget_exceeded"),
    ("(" * 5000 + "1" + ")" * 5000, "formula_budget_exceeded"),
    ("-" * 40 + "1", "formula_budget_exceeded"),
    ("not " * 40 + "true", "formula_budget_exceeded"),
    ("abs(" * 40 + "1" + ")" * 40, "formula_budget_exceeded"),
    ("if(true, " * 40 + "1" + ", 0)" * 40, "formula_budget_exceeded"),
    (" + ".join(["1"] * 300), "formula_budget_exceeded"),  # > 500 AST nodes
    # long strings / long expressions
    ("'" + "a" * 1001 + "'", "formula_budget_exceeded"),
    ("x + " * 1100 + "1", "formula_budget_exceeded"),  # > 4000 characters
    ("a" * 65, "formula_syntax_error"),
    # unicode tricks
    ("１ + 1", "formula_syntax_error"),  # full-width digit
    ("ｘ", "formula_syntax_error"),  # full-width letter
    ("х + 1", "formula_syntax_error"),  # Cyrillic 'х' homoglyph of x
    ("xх", "formula_syntax_error"),  # Latin + Cyrillic mix
    ("x​ + 1", "formula_syntax_error"),  # zero-width space
    ("x﻿", "formula_syntax_error"),  # BOM
    ("1 + 1", "formula_syntax_error"),  # no-break space
    ("−1", "formula_syntax_error"),  # unicode minus
    ("x ∕ y", "formula_syntax_error"),  # division slash
    ("𝐱 + 1", "formula_syntax_error"),  # mathematical bold x
    ("é", "formula_syntax_error"),  # combining accent
    ("s == '‮abc'", "formula_syntax_error"),  # RTL override in string
    ("s == 'a​b'", "formula_syntax_error"),  # zero-width in string
    ("s == 'a\x00b'", "formula_syntax_error"),  # NUL in string
    ("s == 'a b'", "formula_syntax_error"),  # line separator in string
    ("x\x00", "formula_syntax_error"),
    # type confusion
    ("s + 1", "formula_type_error"),
    ("x and true", "formula_type_error"),
    ("not x", "formula_type_error"),
    ("-s", "formula_type_error"),
    ("if(x, 1, 2)", "formula_type_error"),
    ("if(true, 1, 'a')", "formula_type_error"),
    ("x == s", "formula_type_error"),
    ("true < false", "formula_type_error"),
    ("days_between(x, y)", "formula_type_error"),
    ("abs", "formula_type_error"),
    ("abs()", "formula_type_error"),
    ("abs(1, 2)", "formula_type_error"),
    ("date(s) > 1", "formula_type_error"),
    # identifiers outside the schema
    ("z", "formula_unknown_identifier"),
    ("tenant_id", "formula_unknown_identifier"),
    ("settings", "formula_unknown_identifier"),
    ("request", "formula_unknown_identifier"),
    ("X", "formula_unknown_identifier"),  # names are case-sensitive
]


def test_T_FORMULA_ENGINE_02_corpus_is_large_and_codes_are_stable() -> None:
    assert len(CORPUS) >= 140
    assert {c for _, c in CORPUS} <= set(FORMULA_ERROR_CODES)


@pytest.mark.parametrize(("expression", "code"), CORPUS, ids=[repr(c[0])[:50] for c in CORPUS])
def test_T_FORMULA_ENGINE_02_adversarial_rejected(expression: str, code: str) -> None:
    with pytest.raises(FormulaError) as exc:
        evaluate_expression(expression, INPUTS, schema=SCHEMA)
    assert exc.value.code == code, exc.value.detail
    assert exc.value.status == 422


@pytest.mark.parametrize(("expression", "code"), CORPUS[:40], ids=[repr(c[0])[:50] for c in CORPUS[:40]])
def test_T_FORMULA_ENGINE_02_adversarial_rejected_on_validate(expression: str, code: str) -> None:
    d = FormulaDefinition("f", expression, InputSchema.from_json(SCHEMA), "decimal")
    report = validate_definition(d)
    assert not report.ok and report.errors[0]["code"] in FORMULA_ERROR_CODES


def test_T_FORMULA_ENGINE_02_syntax_errors_carry_positions() -> None:
    with pytest.raises(FormulaError) as exc:
        evaluate_expression("x +\n  y.real", INPUTS, schema=SCHEMA)
    assert exc.value.extra["position"] == 7
    assert (exc.value.extra["line"], exc.value.extra["column"]) == (2, 4)


@pytest.mark.parametrize(
    "graph",
    [
        {"a": ["a"]},
        {"a": ["b"], "b": ["a"]},
        {"a": ["b"], "b": ["c"], "c": ["a"]},
        {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["b"]},
    ],
)
def test_T_FORMULA_ENGINE_02_recursion_via_dependencies_rejected(graph: dict[str, list[str]]) -> None:
    defs = [
        FormulaDefinition(code, " + ".join(deps) if deps else "1", depends_on=tuple(deps))
        for code, deps in graph.items()
    ]
    with pytest.raises(FormulaDependencyCycle):
        FormulaGraph(defs)


def test_T_FORMULA_ENGINE_02_schema_names_are_sanitized() -> None:
    for bad in ("__class__", "a.b", "lambda", "ünïcode", "x​", "1abc", "", "import"):
        with pytest.raises(FormulaError):
            InputSchema.from_json({bad: "decimal"})
    with pytest.raises(FormulaError):
        InputSchema.from_json({"x": {"type": "object"}})
    with pytest.raises(FormulaError):
        InputSchema.from_json({"x": {"type": "decimal", "eval": "1"}})


# --------------------------------------------------------------------------- static scan
WORKFLOW_DIR = Path(__file__).resolve().parents[1]
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr"}
BANNED_MODULES = {
    "ast",
    "builtins",
    "importlib",
    "pickle",
    "marshal",
    "subprocess",
    "ctypes",
    "code",
    "codeop",
    "runpy",
}


def _production_sources() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.rglob("*.py") if "tests" not in p.relative_to(WORKFLOW_DIR).parts)


def test_T_FORMULA_ENGINE_02_static_scan_no_eval_exec_anywhere_in_workflow() -> None:
    sources = _production_sources()
    assert any(p.parent.name == "formula" for p in sources)
    problems: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in BANNED_CALLS:
                    problems.append(f"{path.name}:{node.lineno} calls {fn.id}()")
                if isinstance(fn, ast.Attribute) and fn.attr in {"literal_eval", "system", "popen", "__import__"}:
                    problems.append(f"{path.name}:{node.lineno} calls .{fn.attr}()")
            if isinstance(node, ast.Import):
                problems += [
                    f"{path.name}:{node.lineno} imports {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] in BANNED_MODULES
                ]
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in BANNED_MODULES:
                problems.append(f"{path.name}:{node.lineno} imports from {node.module}")
    assert problems == []


def test_T_FORMULA_ENGINE_02_static_scan_engine_has_no_io() -> None:
    io_modules = {"os", "socket", "requests", "httpx", "urllib", "http", "shutil", "tempfile", "threading", "django"}
    for path in (WORKFLOW_DIR / "formula").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] not in io_modules, f"{path.name} imports {name}"
            if isinstance(node, ast.Name):
                assert node.id != "open", f"{path.name} uses open()"
