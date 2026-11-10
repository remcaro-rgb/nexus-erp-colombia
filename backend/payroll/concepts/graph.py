"""Payroll concept graph on the shared formula engine (F-PAY-FORMULA, D-025, D-008).

Every concept becomes one node of a :class:`workflow.formula.FormulaGraph` whose code is the concept
code, so a formula reads earlier concepts' results by their codes:

* formula concept — expression, input schema, result type and rounding of the ACTIVE
  ``workflow.Formula`` (domain ``payroll``) effective on the calculation date, with the concept's
  ``depends_on``;
* manual concept — the built-in expression ``novelty_amount`` (sum of the employee's novelties).

The engine gives the DAG check (``formula_dependency_cycle``), the deterministic topological
order, the budgets of D-008 and the explain trace; nothing here evaluates Python.

Inputs a payroll formula may declare (anything else is refused on save):

* :data:`STANDARD_INPUTS` (contract and period data, employee attributes — restricted data);
* ``hours_<type>`` — approved timekeeping hours by type (decimal);
* ``param_<name>`` — legal parameters of :mod:`payroll.concepts.parameters` (fail closed).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from payroll.concepts.parameters import PARAMETER_INPUTS, is_parameter_input, parameter_code
from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, analyze
from workflow.formula.errors import FormulaTypeError, FormulaUnknownIdentifier
from workflow.formula.functions import NUMBER
from workflow.formula.schema import check_name

#: name → declared type expected for it (the formula schema must declare the same type).
STANDARD_INPUTS: Mapping[str, str] = {
    "salary": "money",
    "days_worked": "decimal",
    "period_days": "decimal",
    "integral_salary": "boolean",
    "exonerated": "boolean",
    "arl_risk_class": "string",
    "contract_type": "string",
    "novelty_amount": "money",
    "novelty_quantity": "quantity",
    "period_start": "date",
    "period_end": "date",
}
HOURS_RE = re.compile(r"^hours_[a-z][a-z0-9_]{0,40}$")
NUMERIC_RESULTS = ("money", "decimal")
MANUAL_EXPRESSION = "novelty_amount"
MANUAL_SCHEMA = {"fields": {"novelty_amount": {"type": "money", "default": "0"}}}
NUMERIC_TYPES = ("decimal", "money", "quantity", "percent", "integer")


@dataclass(frozen=True)
class FormulaRef:
    """The ACTIVE workflow formula version a concept evaluates (snapshot identity)."""

    id: str
    code: str
    version: int
    hash: str
    expression: str
    inputs: Mapping[str, Any]
    result_type: str
    output_places: int | None
    output_rounding: str


@dataclass(frozen=True)
class ConceptSpec:
    concept_id: str
    code: str
    concept_type: str
    depends_on: tuple[str, ...]
    formula: FormulaRef | None = None
    rounding_places: int = 2


def check_concept_code(code: str) -> str:
    check_name(code, "concept code")
    if code in STANDARD_INPUTS or is_parameter_input(code) or HOURS_RE.match(code):
        raise FormulaTypeError(f"concept code {code!r} clashes with a payroll input name")
    return code


def check_input_schema(inputs: Mapping[str, Any] | None) -> InputSchema:
    """The formula's declared inputs must all be payroll inputs (unknown ``param_*`` fail closed)."""
    schema = InputSchema.from_json(inputs or {})
    if schema.tables:
        raise FormulaUnknownIdentifier(
            "payroll formulas read legal tables only through param_* inputs (no lookup tables)"
        )
    for name, spec in schema.fields.items():
        if is_parameter_input(name):
            parameter_code(name)  # payroll_parameter_unknown when not in the catalogue
            expected: str | tuple[str, ...] = ("decimal", "percent")
        elif name in STANDARD_INPUTS:
            expected = STANDARD_INPUTS[name]
            if expected in NUMERIC_TYPES:
                expected = NUMERIC_TYPES
        elif HOURS_RE.match(name):
            expected = NUMERIC_TYPES
        else:
            raise FormulaUnknownIdentifier(
                f"input {name!r} is not a payroll input (standard inputs, hours_<type>, param_<name>)", input=name
            )
        allowed = (expected,) if isinstance(expected, str) else expected
        if spec.type not in allowed:
            raise FormulaTypeError(f"input {name!r} must be declared as {' or '.join(allowed)}", input=name)
    return schema


def check_expression(
    expression: str, inputs: Mapping[str, Any] | None, depends_on: Iterable[str], result_type: str
) -> None:
    """Parse + type check a payroll formula on save (D-008 budgets; adversarial text is rejected).

    The schema must hold only payroll inputs; dependency results are numbers.
    """
    if result_type not in NUMERIC_RESULTS:
        raise FormulaTypeError("a payroll formula must return money or decimal")
    schema = check_input_schema(inputs)
    analyze(expression, schema, dependency_types={d: NUMBER for d in depends_on}, result_type=result_type)


def definition_for(spec: ConceptSpec) -> FormulaDefinition:
    if spec.formula is None:
        return FormulaDefinition(
            code=spec.code,
            expression=MANUAL_EXPRESSION,
            schema=InputSchema.from_json(MANUAL_SCHEMA),
            result_type="money",
            depends_on=(),
        )
    f = spec.formula
    if f.result_type not in NUMERIC_RESULTS:
        raise FormulaTypeError(f"concept {spec.code}: a payroll formula must return money or decimal")
    return FormulaDefinition(
        code=spec.code,
        expression=f.expression,
        schema=check_input_schema(f.inputs),
        result_type=f.result_type,
        depends_on=spec.depends_on,
        output_places=f.output_places,
        output_rounding=f.output_rounding,
        version=f.version,
    )


def structural_order(nodes: Mapping[str, Iterable[str]]) -> list[str]:
    """DAG check of declared dependencies only (on save): cycle → ``formula_dependency_cycle``."""
    defs = [FormulaDefinition(code=c, expression="0", depends_on=tuple(sorted(set(d)))) for c, d in nodes.items()]
    return list(FormulaGraph(defs).order)


@dataclass
class CompiledGraph:
    specs: dict[str, ConceptSpec]
    graph: FormulaGraph
    order: list[str] = field(default_factory=list)

    @property
    def parameter_inputs(self) -> list[str]:
        names: set[str] = set()
        for d in self.graph.definitions.values():
            names.update(n for n in d.schema.fields if is_parameter_input(n))
        return sorted(names)

    def fields_of(self, code: str) -> Sequence[str]:
        return list(self.graph.definitions[code].schema.fields)


def compile_graph(specs: Iterable[ConceptSpec]) -> CompiledGraph:
    by_code = {s.code: s for s in specs}
    graph = FormulaGraph([definition_for(s) for s in by_code.values()])
    return CompiledGraph(by_code, graph, list(graph.order))


def round_amount(value: Any, places: int) -> Decimal:
    if not isinstance(value, Decimal):
        raise FormulaTypeError("payroll concepts must evaluate to a number")
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


__all__ = [
    "PARAMETER_INPUTS",
    "STANDARD_INPUTS",
    "CompiledGraph",
    "ConceptSpec",
    "FormulaRef",
    "check_concept_code",
    "check_expression",
    "check_input_schema",
    "compile_graph",
    "definition_for",
    "round_amount",
    "structural_order",
]
