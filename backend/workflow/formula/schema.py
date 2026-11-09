"""Declared input schema of a formula: typed inputs and the lookup tables it may read.

JSON form (``Formula.inputs`` in the contract)::

    {"fields": {"base": {"type": "money", "required": true},
                "segment": {"type": "string", "default": "A"}},
     "tables": {"tarifas": {"key": "string", "value": "percent"}}}

A flat mapping ``{"base": "money"}`` is accepted as ``fields`` without tables.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from workflow.formula.errors import (
    FormulaBudgetExceeded,
    FormulaSyntaxError,
    FormulaTypeError,
    FormulaUnknownIdentifier,
)
from workflow.formula.functions import BOOLEAN, DATE, NUMBER, STRING, parse_date
from workflow.formula.lexer import KEYWORDS, MAX_IDENT_CHARS, RESERVED
from workflow.formula.limits import MAX_LITERAL_DIGITS, MAX_MAGNITUDE, MAX_STRING_CHARS
from workflow.formula.nodes import Value

#: Declared field/result types → DSL type.
FIELD_TYPES: dict[str, str] = {
    "decimal": NUMBER,
    "money": NUMBER,
    "quantity": NUMBER,
    "percent": NUMBER,
    "integer": NUMBER,
    "unit_cost": NUMBER,
    "fx_rate": NUMBER,
    "boolean": BOOLEAN,
    "string": STRING,
    "date": DATE,
}
RESULT_TYPES = ("decimal", "money", "quantity", "percent", "boolean", "date")

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_FIELDS = 200
MAX_TABLES = 50


def check_name(name: Any, what: str = "input") -> str:
    if (
        not isinstance(name, str)
        or not NAME_RE.match(name)
        or "__" in name
        or len(name) > MAX_IDENT_CHARS
        or name.lower() in RESERVED
        or name.lower() in KEYWORDS
    ):
        raise FormulaSyntaxError(f"invalid {what} name {str(name)[:40]!r}")
    return name


def check_table_name(name: Any) -> str:
    if not isinstance(name, str) or not TABLE_RE.match(name) or "__" in name:
        raise FormulaSyntaxError(f"invalid table name {str(name)[:70]!r} (lower-case letters, digits, '_')")
    return name


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: str  # declared type (FIELD_TYPES key)
    required: bool = True
    default: Value | None = None

    @property
    def dsl_type(self) -> str:
        return FIELD_TYPES[self.type]


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    key_type: str
    value_type: str

    @property
    def dsl_key(self) -> str:
        return FIELD_TYPES[self.key_type]

    @property
    def dsl_value(self) -> str:
        return FIELD_TYPES[self.value_type]


def coerce(value: Any, declared: str, what: str) -> Value:
    """Convert an external value to the DSL value of ``declared`` type (floats refused)."""
    dsl = FIELD_TYPES[declared]
    if dsl == NUMBER:
        if isinstance(value, (bool, float)):
            raise FormulaTypeError(f"{what}: {type(value).__name__} is not a decimal (use a string or Decimal)")
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, str):
            text = value.strip()
            if len(text) > MAX_LITERAL_DIGITS + 2:
                raise FormulaBudgetExceeded(f"{what}: numeric text too long", budget="literal")
            try:
                d = Decimal(text)
            except InvalidOperation as exc:
                raise FormulaTypeError(f"{what}: {text[:40]!r} is not a decimal") from exc
        else:
            raise FormulaTypeError(f"{what}: expected a decimal")
        if not d.is_finite():
            raise FormulaTypeError(f"{what}: non-finite values are not allowed")
        if d and d.adjusted() >= MAX_MAGNITUDE:
            raise FormulaBudgetExceeded(f"{what}: magnitude above 10^{MAX_MAGNITUDE}", budget="magnitude")
        if declared == "integer" and d != d.to_integral_value():
            raise FormulaTypeError(f"{what}: expected a whole number")
        return d
    if dsl == BOOLEAN:
        if not isinstance(value, bool):
            raise FormulaTypeError(f"{what}: expected true/false")
        return value
    if dsl == STRING:
        if not isinstance(value, str):
            raise FormulaTypeError(f"{what}: expected text")
        if len(value) > MAX_STRING_CHARS:
            raise FormulaBudgetExceeded(f"{what}: text longer than {MAX_STRING_CHARS}", budget="string")
        if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value if c not in "\n\t"):
            raise FormulaTypeError(f"{what}: control or format characters are not allowed")
        return value
    # DATE
    if isinstance(value, dt.datetime):
        raise FormulaTypeError(f"{what}: expected a date, not a timestamp")
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return parse_date(value)
    raise FormulaTypeError(f"{what}: expected a date")


@dataclass(frozen=True, slots=True)
class InputSchema:
    fields: Mapping[str, FieldSpec] = field(default_factory=dict)
    tables: Mapping[str, TableSpec] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> InputSchema:
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise FormulaTypeError("the input schema must be an object")
        if "fields" in data or "tables" in data:
            extra = set(data) - {"fields", "tables"}
            if extra:
                raise FormulaTypeError(f"unknown input schema keys: {sorted(extra)}")
            raw_fields = data.get("fields") or {}
            raw_tables = data.get("tables") or {}
        else:
            raw_fields, raw_tables = data, {}
        if not isinstance(raw_fields, Mapping) or not isinstance(raw_tables, Mapping):
            raise FormulaTypeError("'fields' and 'tables' must be objects")
        if len(raw_fields) > MAX_FIELDS or len(raw_tables) > MAX_TABLES:
            raise FormulaBudgetExceeded("too many inputs or tables", budget="schema")
        fields: dict[str, FieldSpec] = {}
        for name, spec in raw_fields.items():
            check_name(name)
            if isinstance(spec, str):
                spec = {"type": spec}
            if not isinstance(spec, Mapping):
                raise FormulaTypeError(f"input {name}: spec must be a type name or an object")
            ftype = spec.get("type")
            if ftype not in FIELD_TYPES:
                raise FormulaTypeError(f"input {name}: unknown type {str(ftype)[:20]!r}")
            unknown = set(spec) - {"type", "required", "default", "description"}
            if unknown:
                raise FormulaTypeError(f"input {name}: unknown keys {sorted(unknown)}")
            default = spec.get("default")
            coerced = None if default is None else coerce(default, ftype, f"input {name} default")
            required = bool(spec.get("required", default is None))
            fields[name] = FieldSpec(name, ftype, required, coerced)
        tables: dict[str, TableSpec] = {}
        for name, spec in raw_tables.items():
            check_table_name(name)
            if not isinstance(spec, Mapping):
                raise FormulaTypeError(f"table {name}: spec must be an object with 'key' and 'value'")
            k, v = spec.get("key", "string"), spec.get("value", "decimal")
            if k not in ("string", "integer", "decimal") or v not in FIELD_TYPES:
                raise FormulaTypeError(f"table {name}: unsupported key/value types")
            tables[name] = TableSpec(name, k, v)
        return cls(fields, tables)

    def to_json(self) -> dict[str, Any]:
        out_fields: dict[str, Any] = {}
        for f in self.fields.values():
            spec: dict[str, Any] = {"type": f.type, "required": f.required}
            if f.default is not None:
                spec["default"] = str(f.default) if not isinstance(f.default, bool) else f.default
            out_fields[f.name] = spec
        return {
            "fields": out_fields,
            "tables": {t.name: {"key": t.key_type, "value": t.value_type} for t in self.tables.values()},
        }

    def bind(self, inputs: Mapping[str, Any] | None, *, used: frozenset[str] | None = None) -> dict[str, Value]:
        """Validate and coerce caller inputs; unknown keys are refused (no smuggling)."""
        inputs = dict(inputs or {})
        unknown = set(inputs) - set(self.fields)
        if unknown:
            raise FormulaUnknownIdentifier(f"inputs not declared in the schema: {sorted(unknown)[:10]}")
        out: dict[str, Value] = {}
        for name, spec in self.fields.items():
            if name in inputs and inputs[name] is not None:
                out[name] = coerce(inputs[name], spec.type, f"input {name}")
            elif spec.default is not None:
                out[name] = spec.default
            elif spec.required and (used is None or name in used):
                raise FormulaTypeError(f"input {name} is required", input=name)
        return out
