"""Public API of the formula engine (F-FORMULA-ENGINE).

* :func:`analyze` — parse + type check one expression against its schema.
* :class:`FormulaGraph` — a set of formulas with dependencies: DAG check
  (``formula_dependency_cycle``), deterministic topological order, evaluation
  with a per-formula budget, explain trace and snapshot data.
* :func:`validate_definition` — everything ``:validate``/``:submit`` need:
  parse, types, budgets, acyclic graph and passing test cases.

The engine is pure: no database, no network, no subprocess, no Python
``eval``/``exec``/``compile``. Callers store :meth:`EvaluationResult.snapshot`
inside their own unit of work (posted documents are never re-evaluated).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from kernel.money import AMOUNT, PCT, QTY
from workflow.formula.checker import CheckResult, check
from workflow.formula.errors import (
    FormulaBudgetExceeded,
    FormulaDependencyCycle,
    FormulaError,
    FormulaSyntaxError,
    FormulaTestCaseFailed,
    FormulaTypeError,
    FormulaUnknownIdentifier,
)
from workflow.formula.evaluator import Evaluator, TraceEntry, render
from workflow.formula.functions import BOOLEAN, DATE, NUMBER, ROUNDING_MODES
from workflow.formula.limits import DEFAULT_LIMITS, Budget, Limits
from workflow.formula.nodes import Node, Value
from workflow.formula.parser import parse
from workflow.formula.schema import RESULT_TYPES, InputSchema, check_name, coerce
from workflow.formula.tables import NoTables, TableResolver

RESULT_DSL = {
    "decimal": NUMBER,
    "money": NUMBER,
    "quantity": NUMBER,
    "percent": NUMBER,
    "boolean": BOOLEAN,
    "date": DATE,
}
RESULT_PLACES = {"money": AMOUNT, "quantity": QTY, "percent": PCT}
DOMAINS = ("approval", "pricing", "payroll")
MAX_TEST_CASES = 100


@dataclass(frozen=True, slots=True)
class Analyzed:
    source: str
    root: Node
    node_count: int
    info: CheckResult


def analyze(
    expression: str,
    schema: InputSchema,
    *,
    dependency_types: Mapping[str, str] | None = None,
    result_type: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> Analyzed:
    root, count = parse(expression, limits)
    info = check(expression, root, schema, dependency_types or {})
    if result_type is not None:
        if result_type not in RESULT_DSL:
            raise FormulaTypeError(f"unknown result type '{result_type}'")
        if RESULT_DSL[result_type] != info.type:
            raise FormulaTypeError(
                f"the expression returns {info.type} but the formula declares {result_type}",
                position=0,
                source=expression,
            )
    return Analyzed(expression, root, count, info)


@dataclass(frozen=True)
class TestCase:
    inputs: Mapping[str, Any]
    expected: Any
    name: str = ""
    expected_error: str | None = None  # a stable error code, e.g. formula_division_by_zero

    @classmethod
    def from_json(cls, data: Mapping[str, Any], index: int) -> TestCase:
        if not isinstance(data, Mapping):
            raise FormulaTypeError(f"test case {index + 1} must be an object")
        return cls(
            inputs=dict(data.get("inputs") or {}),
            expected=data.get("expected"),
            name=str(data.get("name") or f"case {index + 1}")[:120],
            expected_error=data.get("expected_error"),
        )


@dataclass(frozen=True)
class FormulaDefinition:
    code: str
    expression: str
    schema: InputSchema = field(default_factory=InputSchema)
    result_type: str = "decimal"
    depends_on: tuple[str, ...] = ()
    output_places: int | None = None
    output_rounding: str = "HALF_EVEN"
    test_cases: tuple[TestCase, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        check_name(self.code, "formula code")
        if self.result_type not in RESULT_TYPES:
            raise FormulaTypeError(f"unknown result type '{self.result_type}'")
        if self.output_rounding.upper() not in ROUNDING_MODES:
            raise FormulaTypeError(f"unknown rounding mode '{self.output_rounding}'")
        if self.output_places is not None and not 0 <= self.output_places <= 12:
            raise FormulaTypeError("output precision must be between 0 and 12")
        if len(self.test_cases) > MAX_TEST_CASES:
            raise FormulaBudgetExceeded(f"more than {MAX_TEST_CASES} test cases", budget="test_cases")
        for dep in self.depends_on:
            check_name(dep, "dependency")

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> FormulaDefinition:
        tests_raw = data.get("tests") or data.get("test_cases") or []
        if isinstance(tests_raw, Mapping):
            tests_raw = tests_raw.get("cases", [])
        if not isinstance(tests_raw, list):
            raise FormulaTypeError("tests must be a list of {inputs, expected}")
        return cls(
            code=str(data.get("code", "")),
            expression=data.get("expression", ""),
            schema=InputSchema.from_json(data.get("inputs")),
            result_type=str(data.get("result_type", "decimal")),
            depends_on=tuple(data.get("depends_on") or ()),
            output_places=data.get("output_places"),
            output_rounding=str(data.get("output_rounding") or "HALF_EVEN"),
            test_cases=tuple(TestCase.from_json(t, i) for i, t in enumerate(tests_raw)),
            version=int(data.get("version") or 1),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "expression": self.expression,
            "inputs": self.schema.to_json(),
            "result_type": self.result_type,
            "depends_on": sorted(self.depends_on),
            "output_places": self.output_places,
            "output_rounding": self.output_rounding.upper(),
        }

    @property
    def hash(self) -> str:
        raw = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def finish(self, value: Value) -> Value:
        """Coerce the raw result to the declared output type/precision."""
        if not isinstance(value, Decimal):
            return value
        places = self.output_places if self.output_places is not None else RESULT_PLACES.get(self.result_type)
        if places is None:
            return value
        rounding = ROUNDING_MODES[self.output_rounding.upper()] if self.output_places is not None else ROUND_HALF_EVEN
        return value.quantize(Decimal(1).scaleb(-places), rounding=rounding)


@dataclass
class EvaluationResult:
    code: str
    value: Value
    raw: Value
    trace: list[TraceEntry]
    visits: int
    inputs: dict[str, Value]
    formula_hash: str
    version: int
    tables: list[tuple[str, int]] = field(default_factory=list)

    def snapshot(self, formula_id: uuid.UUID | None = None) -> dict[str, Any]:
        """``FormulaEvaluation`` snapshot stored by the caller with its document line."""
        return {
            "formula_id": str(formula_id) if formula_id else None,
            "code": self.code,
            "version": self.version,
            "hash": self.formula_hash,
            "inputs": {k: render(v) for k, v in sorted(self.inputs.items())},
            "tables": [{"name": n, "version": v} for n, v in self.tables],
            "trace": [{"expr": t.expr, "value": t.value, "pos": t.pos} for t in self.trace],
            "result": render(self.value),
        }


def _topological(graph: Mapping[str, Iterable[str]]) -> list[str]:
    """Kahn's algorithm with sorted ties (deterministic); raises on cycles with the path."""
    nodes = sorted(graph)
    deps = {n: sorted(set(graph[n])) for n in nodes}
    for n, ds in deps.items():
        for d in ds:
            if d not in deps:
                raise FormulaUnknownIdentifier(f"formula '{n}' depends on unknown formula '{d}'")
    indeg = {n: len(deps[n]) for n in nodes}
    dependents: dict[str, list[str]] = {n: [] for n in nodes}
    for n, ds in deps.items():
        for d in ds:
            dependents[d].append(n)
    ready = sorted(n for n in nodes if indeg[n] == 0)
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(dependents[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()
    if len(order) != len(nodes):
        cycle = _find_cycle({n: deps[n] for n in nodes if n not in order})
        raise FormulaDependencyCycle("dependency cycle: " + " → ".join(cycle), cycle=cycle)
    return order


def _find_cycle(deps: Mapping[str, Sequence[str]]) -> list[str]:
    color: dict[str, int] = {}
    for start in sorted(deps):
        if color.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, i = stack.pop()
            if i == 0:
                color[node] = 1
                path.append(node)
            children = [c for c in deps.get(node, ()) if c in deps]
            if i < len(children):
                stack.append((node, i + 1))
                child = children[i]
                if color.get(child) == 1:
                    return [*path[path.index(child) :], child]
                if not color.get(child):
                    stack.append((child, 0))
            else:
                color[node] = 2
                path.pop()
    return sorted(deps)


class FormulaGraph:
    """Formulas evaluated together; identifiers may name other formulas' results."""

    def __init__(self, definitions: Iterable[FormulaDefinition], *, limits: Limits = DEFAULT_LIMITS) -> None:
        defs = list(definitions)
        if len(defs) > limits.max_graph_formulas:
            raise FormulaBudgetExceeded(
                f"more than {limits.max_graph_formulas} formulas in one dependency graph", budget="graph"
            )
        self.limits = limits
        self.definitions: dict[str, FormulaDefinition] = {}
        for d in defs:
            if d.code in self.definitions:
                raise FormulaTypeError(f"duplicate formula code '{d.code}'")
            self.definitions[d.code] = d
        for d in defs:
            clash = set(d.schema.fields) & set(self.definitions)
            if clash:
                raise FormulaTypeError(f"formula '{d.code}': inputs {sorted(clash)} clash with formula codes")
        # Cycles are detected on declared dependencies before any type check.
        self.order = _topological({c: d.depends_on for c, d in self.definitions.items()})
        self.analyzed: dict[str, Analyzed] = {}
        result_types: dict[str, str] = {}
        for code in self.order:
            d = self.definitions[code]
            dep_types = {dep: result_types[dep] for dep in d.depends_on}
            a = analyze(d.expression, d.schema, dependency_types=dep_types, result_type=d.result_type, limits=limits)
            self.analyzed[code] = a
            result_types[code] = RESULT_DSL[d.result_type]

    def dependents_of(self, code: str) -> list[str]:
        out, frontier = set(), [code]
        while frontier:
            c = frontier.pop()
            for other, d in self.definitions.items():
                if c in d.depends_on and other not in out:
                    out.add(other)
                    frontier.append(other)
        return sorted(out)

    def closure(self, targets: Iterable[str]) -> list[str]:
        need: set[str] = set()
        frontier = list(targets)
        while frontier:
            c = frontier.pop()
            if c not in self.definitions:
                raise FormulaUnknownIdentifier(f"unknown formula '{c}'")
            if c not in need:
                need.add(c)
                frontier.extend(self.definitions[c].depends_on)
        return [c for c in self.order if c in need]

    def evaluate(
        self,
        inputs: Mapping[str, Any] | Mapping[str, Mapping[str, Any]],
        *,
        targets: Iterable[str] | None = None,
        tables: TableResolver | None = None,
        per_formula_inputs: bool = False,
        clock: Callable[[], float] | None = None,
        explain: bool = True,
    ) -> dict[str, EvaluationResult]:
        """Evaluate ``targets`` (default: all) and their dependencies in topological order.

        ``inputs`` is one shared mapping, or ``{code: {...}}`` with ``per_formula_inputs``.
        """
        tables = tables or NoTables()
        codes = self.closure(targets) if targets is not None else list(self.order)
        results: dict[str, EvaluationResult] = {}
        for code in codes:
            d, a = self.definitions[code], self.analyzed[code]
            raw_inputs: Mapping[str, Any]
            if per_formula_inputs:
                raw_inputs = inputs.get(code, {})  # type: ignore[assignment]
            else:
                raw_inputs = {k: v for k, v in inputs.items() if k in d.schema.fields}
            bound = d.schema.bind(raw_inputs, used=frozenset(a.info.inputs))
            env: dict[str, Value] = dict(bound)
            for dep in d.depends_on:
                env[dep] = results[dep].value
            budget = Budget(self.limits) if clock is None else Budget(self.limits, clock=clock)
            accessed_before = len(tables.accessed)
            ev = Evaluator(a.source, env, budget, tables, explain=explain)
            raw = ev.run(a.root)
            accessed = list(tables.accessed[accessed_before:])
            results[code] = EvaluationResult(
                code=code,
                value=d.finish(raw),
                raw=raw,
                trace=ev.trace,
                visits=budget.visits,
                inputs=bound,
                formula_hash=d.hash,
                version=d.version,
                tables=accessed,
            )
        return results


def evaluate_expression(
    expression: str,
    inputs: Mapping[str, Any] | None = None,
    *,
    schema: InputSchema | Mapping[str, Any] | None = None,
    result_type: str | None = None,
    tables: TableResolver | None = None,
    limits: Limits = DEFAULT_LIMITS,
    clock: Callable[[], float] | None = None,
) -> Value:
    """Convenience: evaluate one expression. The schema defaults to the inputs' inferred types."""
    inputs = dict(inputs or {})
    if schema is None:
        schema = InputSchema.from_json({k: _infer_type(v) for k, v in inputs.items()})
    elif not isinstance(schema, InputSchema):
        schema = InputSchema.from_json(schema)
    d = (
        FormulaDefinition(code="expr", expression=expression, schema=schema, result_type=result_type or "decimal")
        if result_type
        else None
    )
    if d is not None:
        return FormulaGraph([d], limits=limits).evaluate(inputs, tables=tables, clock=clock)["expr"].value
    a = analyze(expression, schema, limits=limits)
    bound = schema.bind(inputs, used=frozenset(a.info.inputs))
    budget = Budget(limits) if clock is None else Budget(limits, clock=clock)
    return Evaluator(expression, bound, budget, tables or NoTables(), explain=False).run(a.root)


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (Decimal, int)):
        return "decimal"
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return "date"
    if isinstance(value, str):
        return "string"
    raise FormulaTypeError(f"cannot infer the type of {type(value).__name__} inputs (declare a schema)")


def _matches(definition: FormulaDefinition, got: Value, expected: Any) -> bool:
    if expected is None:
        return False
    try:
        want = coerce(
            expected, definition.result_type if definition.result_type != "percent" else "decimal", "expected"
        )
    except FormulaError:
        return False
    if isinstance(got, Decimal) and isinstance(want, Decimal):
        return got == definition.finish(want)
    return bool(got == want)


@dataclass
class TestCaseOutcome:
    name: str
    passed: bool
    got: str | None
    expected: str | None
    error: str | None = None


def run_test_cases(graph: FormulaGraph, code: str, *, tables: TableResolver | None = None) -> list[TestCaseOutcome]:
    definition = graph.definitions[code]
    outcomes: list[TestCaseOutcome] = []
    for case in definition.test_cases:
        try:
            value = graph.evaluate(case.inputs, targets=[code], tables=tables, explain=False)[code].value
        except FormulaError as exc:
            passed = case.expected_error == exc.code
            outcomes.append(TestCaseOutcome(case.name, passed, None, case.expected_error, exc.code))
            continue
        passed = case.expected_error is None and _matches(definition, value, case.expected)
        outcomes.append(
            TestCaseOutcome(case.name, passed, render(value), None if case.expected is None else str(case.expected))
        )
    return outcomes


@dataclass
class ValidationReport:
    ok: bool
    errors: list[dict[str, Any]]
    tests: list[TestCaseOutcome]
    order: list[str]
    hash: str | None

    def raise_for_errors(self) -> None:
        if self.ok:
            return
        first = self.errors[0]
        cls = {
            "formula_test_case_failed": FormulaTestCaseFailed,
        }.get(first["code"])
        if cls is not None:
            raise cls(first["detail"], failed=[t.name for t in self.tests if not t.passed])
        raise _ERROR_BY_CODE.get(first["code"], FormulaSyntaxError)(
            first["detail"], **{k: v for k, v in first.items() if k not in ("code", "detail")}
        )


def _error_dict(exc: FormulaError) -> dict[str, Any]:
    return {"code": exc.code, "detail": exc.detail, **exc.extra}


def validate_definition(
    definition: FormulaDefinition,
    others: Iterable[FormulaDefinition] = (),
    *,
    tables: TableResolver | None = None,
    limits: Limits = DEFAULT_LIMITS,
    require_tests: bool = False,
) -> ValidationReport:
    """Parse, type check, budgets, acyclic dependencies and test cases (``:validate``)."""
    defs = {d.code: d for d in others if d.code != definition.code}
    defs[definition.code] = definition
    try:
        graph = FormulaGraph(defs.values(), limits=limits)
    except FormulaError as exc:
        return ValidationReport(False, [_error_dict(exc)], [], [], None)
    outcomes = run_test_cases(graph, definition.code, tables=tables)
    errors: list[dict[str, Any]] = []
    if require_tests and not definition.test_cases:
        errors.append({"code": "formula_test_case_failed", "detail": "at least one test case is required"})
    failed = [o for o in outcomes if not o.passed]
    if failed:
        errors.append(
            {
                "code": "formula_test_case_failed",
                "detail": f"{len(failed)} of {len(outcomes)} test cases failed: "
                + ", ".join(o.name for o in failed[:5]),
            }
        )
    return ValidationReport(not errors, errors, outcomes, graph.closure([definition.code]), definition.hash)


_ERROR_BY_CODE: dict[str, type[FormulaError]] = {
    cls.code: cls
    for cls in (
        FormulaSyntaxError,
        FormulaUnknownIdentifier,
        FormulaTypeError,
        FormulaBudgetExceeded,
        FormulaDependencyCycle,
        FormulaTestCaseFailed,
    )
}
