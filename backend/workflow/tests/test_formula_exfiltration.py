"""T-FORMULA-ENGINE-05 — exfiltration attempts (tables outside the declared schema,
cross-company tables, undeclared inputs) are rejected."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal as D

import pytest

from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, TableRegistry, VersionedTable
from workflow.formula.errors import FormulaError, FormulaUnknownIdentifier

COMPANY_A = uuid.UUID("00000000-0000-7000-8000-00000000000a")
COMPANY_B = uuid.UUID("00000000-0000-7000-8000-00000000000b")
DAY = dt.date(2026, 11, 9)


@pytest.fixture
def registry() -> TableRegistry:
    r = TableRegistry()
    r.add(VersionedTable.build(COMPANY_A, "tarifas", {"A": "1.5", "B": "2.5"}, value_type="percent"))
    r.add(VersionedTable.build(COMPANY_B, "tarifas", {"A": "99", "B": "98"}, value_type="percent"))
    r.add(VersionedTable.build(COMPANY_B, "salarios_secretos", {"CEO": "90000000"}, value_type="money"))
    return r


def graph(expr: str, tables: dict | None = None, fields: dict | None = None) -> FormulaGraph:
    schema = InputSchema.from_json({"fields": fields or {"k": "string"}, "tables": tables or {}})
    return FormulaGraph([FormulaDefinition("f", expr, schema, "decimal")])


def test_T_FORMULA_ENGINE_05_own_company_table_works(registry: TableRegistry) -> None:
    g = graph("lookup('tarifas', k)", {"tarifas": {"key": "string", "value": "percent"}})
    res = g.evaluate({"k": "B"}, tables=registry.resolver(COMPANY_A, DAY))
    assert res["f"].value == D("2.5") and res["f"].tables == [("tarifas", 1)]


def test_T_FORMULA_ENGINE_05_cross_company_table_is_invisible(registry: TableRegistry) -> None:
    g = graph("lookup('salarios_secretos', k)", {"salarios_secretos": {"key": "string", "value": "money"}})
    with pytest.raises(FormulaUnknownIdentifier) as exc:
        g.evaluate({"k": "CEO"}, tables=registry.resolver(COMPANY_A, DAY))
    # Same message as a table that does not exist at all: no existence oracle.
    g2 = graph("lookup('no_existe', k)", {"no_existe": {"key": "string", "value": "money"}})
    with pytest.raises(FormulaUnknownIdentifier) as exc2:
        g2.evaluate({"k": "CEO"}, tables=registry.resolver(COMPANY_A, DAY))
    assert exc.value.detail.replace("salarios_secretos", "T") == exc2.value.detail.replace("no_existe", "T")


def test_T_FORMULA_ENGINE_05_same_name_table_resolves_to_own_company_only(registry: TableRegistry) -> None:
    g = graph("lookup('tarifas', k)", {"tarifas": {"key": "string", "value": "percent"}})
    assert g.evaluate({"k": "A"}, tables=registry.resolver(COMPANY_A, DAY))["f"].value == D("1.5")
    assert g.evaluate({"k": "A"}, tables=registry.resolver(COMPANY_B, DAY))["f"].value == D("99")


@pytest.mark.parametrize(
    "table",
    ["tarifas", "auth_user", "kernel_outbox", "iam_company", "pg_shadow", "salarios_secretos"],
)
def test_T_FORMULA_ENGINE_05_lookup_outside_declared_schema_rejected_statically(
    registry: TableRegistry, table: str
) -> None:
    with pytest.raises(FormulaUnknownIdentifier) as exc:
        graph(f"lookup('{table}', k)")
    assert "not declared" in exc.value.detail


@pytest.mark.parametrize(
    "table",
    ["public.auth_user", "../tarifas", "Tarifas", "tarifas;drop", "tarifas--", "tárifas", "", "x" * 80, "a__b"],
)
def test_T_FORMULA_ENGINE_05_malformed_table_names_rejected(table: str) -> None:
    with pytest.raises(FormulaError):
        graph(f"lookup('{table}', k)")
    with pytest.raises(FormulaError):
        InputSchema.from_json({"fields": {}, "tables": {table: {"key": "string", "value": "decimal"}}})


def test_T_FORMULA_ENGINE_05_dynamic_table_names_rejected() -> None:
    tables = {"tarifas": {"key": "string", "value": "percent"}}
    for expr in ("lookup(k, k)", "lookup(if(true, 'tarifas', 'otra'), k)", "lookup(tarifas, k)"):
        with pytest.raises(FormulaError):
            graph(expr, tables)


def test_T_FORMULA_ENGINE_05_undeclared_inputs_cannot_be_smuggled(registry: TableRegistry) -> None:
    g = graph("1", fields={"k": "string"})
    with pytest.raises(FormulaUnknownIdentifier):
        g.evaluate({"f": {"k": "A", "company_id": str(COMPANY_B)}}, per_formula_inputs=True)
    for ident in ("company_id", "tenant_id", "user", "db", "registry", "tables"):
        with pytest.raises(FormulaUnknownIdentifier):
            graph(ident)


def test_T_FORMULA_ENGINE_05_table_versions_and_effective_dates(registry: TableRegistry) -> None:
    registry.add(
        VersionedTable.build(
            COMPANY_A, "tarifas", {"A": "3"}, value_type="percent", version=2, effective_from=dt.date(2027, 1, 1)
        )
    )
    g = graph("lookup('tarifas', k, 0)", {"tarifas": {"key": "string", "value": "percent"}})
    assert g.evaluate({"k": "A"}, tables=registry.resolver(COMPANY_A, DAY))["f"].value == D("1.5")
    res = g.evaluate({"k": "A"}, tables=registry.resolver(COMPANY_A, dt.date(2027, 2, 1)))["f"]
    assert res.value == D("3") and res.snapshot()["tables"] == [{"name": "tarifas", "version": 2}]
    assert g.evaluate({"k": "Z"}, tables=registry.resolver(COMPANY_A, DAY))["f"].value == D("0")


def test_T_FORMULA_ENGINE_05_no_tables_resolver_by_default() -> None:
    g = graph("lookup('tarifas', k)", {"tarifas": {"key": "string", "value": "percent"}})
    with pytest.raises(FormulaUnknownIdentifier):
        g.evaluate({"k": "A"})
