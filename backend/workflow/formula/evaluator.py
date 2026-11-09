"""Whitelisted tree-walking evaluator over Decimal (precision 28, D-008).

Every node visit is charged to a :class:`~workflow.formula.limits.Budget`;
division by zero is an explicit error (never NaN/Infinity); every numeric
result is bounded in magnitude. The evaluator only dispatches on node classes
and on the fixed :data:`~workflow.formula.functions.FUNCTIONS` table.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from workflow.formula.errors import (
    FormulaBudgetExceeded,
    FormulaDivisionByZero,
    FormulaError,
    FormulaTypeError,
    FormulaUnknownIdentifier,
)
from workflow.formula.functions import FUNCTIONS
from workflow.formula.limits import DECIMAL_PRECISION, MAX_MAGNITUDE, MAX_TRACE_ENTRIES, Budget
from workflow.formula.nodes import Binary, Bool, Call, Ident, Node, Num, Str, Unary, Value
from workflow.formula.tables import TableResolver


def decimal_context() -> Context:
    return Context(
        prec=DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emax=MAX_MAGNITUDE * 4,
        Emin=-MAX_MAGNITUDE * 4,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def render(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


@dataclass
class TraceEntry:
    expr: str
    value: str
    pos: int


@dataclass
class Evaluator:
    source: str
    env: Mapping[str, Value]
    budget: Budget
    tables: TableResolver
    trace: list[TraceEntry] = field(default_factory=list)
    explain: bool = True

    def run(self, root: Node) -> Value:
        self.budget.start()
        try:
            with localcontext(decimal_context()):
                return self.eval(root)
        except FormulaError:
            raise
        except (DecimalException, ArithmeticError) as exc:
            # Overflow/invalid operations of the Decimal context: bounded, never NaN.
            raise FormulaBudgetExceeded(f"numeric limits exceeded ({type(exc).__name__})", budget="magnitude") from None
        except RecursionError:
            raise FormulaBudgetExceeded("nesting too deep", budget="nesting") from None

    def _record(self, node: Node, value: Value) -> Value:
        if self.explain and len(self.trace) < MAX_TRACE_ENTRIES:
            self.trace.append(TraceEntry(self.source[node.pos : node.end], render(value), node.pos))
        return value

    def _num(self, value: Decimal, node: Node) -> Decimal:
        if value and value.adjusted() >= MAX_MAGNITUDE:
            raise FormulaBudgetExceeded(
                f"intermediate value outside ±10^{MAX_MAGNITUDE}",
                budget="magnitude",
                position=node.pos,
                source=self.source,
            )
        return value

    def eval(self, node: Node) -> Value:
        self.budget.tick()
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Str):
            return node.value
        if isinstance(node, Bool):
            return node.value
        if isinstance(node, Ident):
            if node.name not in self.env:
                raise FormulaUnknownIdentifier(f"'{node.name}' has no value", position=node.pos, source=self.source)
            return self._record(node, self.env[node.name])
        if isinstance(node, Unary):
            v = self.eval(node.operand)
            if node.op == "not":
                return self._record(node, not v)
            if not isinstance(v, Decimal):
                raise FormulaTypeError("unary operator needs a number", position=node.pos, source=self.source)
            return self._record(node, -v if node.op == "-" else +v)
        if isinstance(node, Binary):
            return self._record(node, self.eval_binary(node))
        if isinstance(node, Call):
            return self._record(node, self.eval_call(node))
        raise FormulaTypeError("unsupported expression", position=node.pos, source=self.source)

    def eval_binary(self, node: Binary) -> Value:
        op = node.op
        if op == "and":
            return bool(self.eval(node.left)) and bool(self.eval(node.right))
        if op == "or":
            return bool(self.eval(node.left)) or bool(self.eval(node.right))
        a, b = self.eval(node.left), self.eval(node.right)
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op in ("<", "<=", ">", ">="):
            if type(a) is not type(b) or isinstance(a, bool):
                raise FormulaTypeError("cannot order these values", position=node.pos, source=self.source)
            if op == "<":
                return a < b  # type: ignore[operator]
            if op == "<=":
                return a <= b  # type: ignore[operator]
            if op == ">":
                return a > b  # type: ignore[operator]
            return a >= b  # type: ignore[operator]
        if not isinstance(a, Decimal) or not isinstance(b, Decimal):
            raise FormulaTypeError(f"'{op}' needs numbers", position=node.pos, source=self.source)
        if op == "+":
            return self._num(a + b, node)
        if op == "-":
            return self._num(a - b, node)
        if op == "*":
            return self._num(a * b, node)
        if b == 0:
            raise FormulaDivisionByZero(
                f"division by zero in '{self.source[node.pos : node.end]}'", position=node.right.pos, source=self.source
            )
        if op == "/":
            return self._num(a / b, node)
        return self._num(a % b, node)

    def eval_call(self, node: Call) -> Value:
        name = node.name
        if name == "if":
            cond = self.eval(node.args[0])
            return self.eval(node.args[1] if cond else node.args[2])
        if name == "lookup":
            return self.eval_lookup(node)
        sig = FUNCTIONS[name]
        args = [self.eval(a) for a in node.args]
        if sig.impl is None:
            raise FormulaTypeError(f"{name}() is not callable", position=node.pos, source=self.source)
        try:
            result = sig.impl(*args)
        except FormulaError as exc:
            raise type(exc)(exc.detail, position=node.pos, source=self.source) from None
        if isinstance(result, Decimal):
            return self._num(result, node)
        return result  # type: ignore[no-any-return]

    def eval_lookup(self, node: Call) -> Value:
        table_node = node.args[0]
        if not isinstance(table_node, Str):
            raise FormulaTypeError("lookup() table must be a literal", position=node.pos, source=self.source)
        try:
            table = self.tables.get(table_node.value)
        except FormulaError as exc:
            raise type(exc)(exc.detail, position=table_node.pos, source=self.source) from None
        key = self.eval(node.args[1])
        if key in table.rows:
            return table.rows[key]
        if len(node.args) == 3:
            return self.eval(node.args[2])
        raise FormulaTypeError(
            f"key {render(key)[:40]!r} not found in table '{table.name}' v{table.version}",
            position=node.args[1].pos,
            source=self.source,
        )
