"""Static type checker: every identifier, function, table and type is resolved
before a formula can be saved or evaluated."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from workflow.formula.errors import FormulaSyntaxError, FormulaTypeError, FormulaUnknownIdentifier
from workflow.formula.functions import ANY, BOOLEAN, DATE, FUNCTIONS, NUMBER, ROUNDING_MODES, STRING
from workflow.formula.nodes import Binary, Bool, Call, Ident, Node, Num, Str, Unary
from workflow.formula.schema import InputSchema, check_table_name

ARITH = frozenset({"+", "-", "*", "/", "%"})
ORDER = frozenset({"<", "<=", ">", ">="})
EQUALITY = frozenset({"==", "!="})
LOGIC = frozenset({"and", "or"})


@dataclass
class CheckResult:
    type: str
    inputs: set[str] = field(default_factory=set)
    dependencies: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    functions: set[str] = field(default_factory=set)


class _Checker:
    def __init__(self, source: str, schema: InputSchema, dependency_types: Mapping[str, str]) -> None:
        self.source = source
        self.schema = schema
        self.deps = dependency_types
        self.result = CheckResult(type=ANY)

    def type_error(self, msg: str, node: Node) -> FormulaTypeError:
        return FormulaTypeError(msg, position=node.pos, source=self.source)

    def check(self, node: Node) -> str:
        if isinstance(node, Num):
            return NUMBER
        if isinstance(node, Str):
            return STRING
        if isinstance(node, Bool):
            return BOOLEAN
        if isinstance(node, Ident):
            if node.name in self.schema.fields:
                self.result.inputs.add(node.name)
                return self.schema.fields[node.name].dsl_type
            if node.name in self.deps:
                self.result.dependencies.add(node.name)
                return self.deps[node.name]
            if node.name.lower() in FUNCTIONS:
                raise self.type_error(f"'{node.name}' is a function; call it with parentheses", node)
            raise FormulaUnknownIdentifier(
                f"unknown identifier '{node.name}' (not in the input schema or dependencies)",
                position=node.pos,
                source=self.source,
            )
        if isinstance(node, Unary):
            t = self.check(node.operand)
            want = BOOLEAN if node.op == "not" else NUMBER
            if t != want:
                raise self.type_error(f"'{node.op}' needs a {want}, got {t}", node)
            return want
        if isinstance(node, Binary):
            lt, rt = self.check(node.left), self.check(node.right)
            if node.op in ARITH:
                if lt != NUMBER or rt != NUMBER:
                    raise self.type_error(f"'{node.op}' needs numbers, got {lt} and {rt}", node)
                return NUMBER
            if node.op in LOGIC:
                if lt != BOOLEAN or rt != BOOLEAN:
                    raise self.type_error(f"'{node.op}' needs booleans, got {lt} and {rt}", node)
                return BOOLEAN
            if lt != rt:
                raise self.type_error(f"cannot compare {lt} with {rt}", node)
            if node.op in ORDER and lt not in (NUMBER, DATE, STRING):
                raise self.type_error(f"'{node.op}' cannot order {lt} values", node)
            return BOOLEAN
        if isinstance(node, Call):
            return self.check_call(node)
        raise self.type_error("unsupported expression", node)

    def check_call(self, node: Call) -> str:
        sig = FUNCTIONS[node.name]
        self.result.functions.add(node.name)
        n = len(node.args)
        max_args = None if sig.variadic else len(sig.params)
        if n < sig.min_args or (max_args is not None and n > max_args):
            expected = (
                f"{sig.min_args}+"
                if sig.variadic
                else (str(sig.min_args) if sig.min_args == len(sig.params) else f"{sig.min_args}-{len(sig.params)}")
            )
            raise self.type_error(f"{node.name}() takes {expected} arguments, got {n}", node)
        if node.name == "if":
            ct = self.check(node.args[0])
            if ct != BOOLEAN:
                raise self.type_error(f"if() condition must be boolean, got {ct}", node.args[0])
            a, b = self.check(node.args[1]), self.check(node.args[2])
            if a != b:
                raise self.type_error(f"if() branches must have the same type ({a} vs {b})", node)
            return a
        if node.name == "lookup":
            return self.check_lookup(node)
        for i, arg in enumerate(node.args):
            want = sig.params[min(i, len(sig.params) - 1)]
            got = self.check(arg)
            if got != want:
                raise self.type_error(f"{node.name}() argument {i + 1} must be {want}, got {got}", arg)
        if node.name == "round" and n == 3:
            mode = node.args[2]
            if not isinstance(mode, Str):
                raise self.type_error("round() mode must be a literal such as 'HALF_UP'", mode)
            if mode.value.upper() not in ROUNDING_MODES:
                raise self.type_error(f"unknown rounding mode '{mode.value}' (use {', '.join(ROUNDING_MODES)})", mode)
        return sig.returns

    def check_lookup(self, node: Call) -> str:
        table_arg = node.args[0]
        if not isinstance(table_arg, Str):
            raise self.type_error("lookup() table must be a literal table name", table_arg)
        try:
            check_table_name(table_arg.value)
        except FormulaSyntaxError as exc:
            raise FormulaUnknownIdentifier(exc.detail, position=table_arg.pos, source=self.source) from None
        spec = self.schema.tables.get(table_arg.value)
        if spec is None:
            raise FormulaUnknownIdentifier(
                f"table '{table_arg.value}' is not declared in the input schema",
                position=table_arg.pos,
                source=self.source,
            )
        self.result.tables.add(spec.name)
        kt = self.check(node.args[1])
        if kt != spec.dsl_key:
            raise self.type_error(f"lookup() key of '{spec.name}' must be {spec.dsl_key}, got {kt}", node.args[1])
        if len(node.args) == 3:
            dt_ = self.check(node.args[2])
            if dt_ != spec.dsl_value:
                raise self.type_error(f"lookup() default must be {spec.dsl_value}, got {dt_}", node.args[2])
        return spec.dsl_value


def check(source: str, root: Node, schema: InputSchema, dependency_types: Mapping[str, str]) -> CheckResult:
    c = _Checker(source, schema, dependency_types)
    c.result.type = c.check(root)
    return c.result
