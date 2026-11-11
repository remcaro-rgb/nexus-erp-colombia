"""Semantic query → parameterized SQL (ADR-009). The only reporting module allowed raw SQL (ADR-004 allow-list).

Inputs are a validated :class:`Plan` (names already resolved against the semantic registry by
:mod:`reporting.engine`); every user value is a bound parameter, every identifier/SQL fragment comes from the
registry. The compiled statement always carries: the tenant and company predicates, the entity's default filters,
the policy row-scope subquery (``PolicyService.scope_queryset`` compiled by Django), and the hierarchy joins of
the dimension closure table (effective-dated: current hierarchy or *as of* a date — DIMENSIONS-ADDENDUM DIM-R06/R07).

Execution runs inside the caller's bound transaction (RLS context), with ``statement_timeout`` set for the
statement and restored afterwards; ``explain`` returns PostgreSQL's estimated total cost (query cost guard).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import connection

from reporting.semantic.registry import Entity, Field, Measure

CLOSURE = "ledger_dimension_closure"
NODE = "ledger_dimension_node"
OPS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "between", "is_null", "not_null", "contains",
       "in_subtree")  # fmt: skip
GRAIN_SQL = {"day": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}
AGG_SQL = {
    "sum": "COALESCE(SUM({e}), 0)",
    "avg": "AVG({e})",
    "count": "COUNT({e})",
    "count_distinct": "COUNT(DISTINCT {e})",
    "min": "MIN({e})",
    "max": "MAX({e})",
}


@dataclass(frozen=True, slots=True)
class Dim:
    key: str
    field: Field
    grain: str | None = None
    level: str | None = None  # None (the value itself) | "root" | "parent" (hierarchy fields)


@dataclass(frozen=True, slots=True)
class Cond:
    field: Field
    op: str
    value: Any = None


@dataclass
class Plan:
    entity: Entity
    tenant_id: uuid.UUID
    companies: list[uuid.UUID]
    measures: list[Measure]
    dimensions: list[Dim]
    filters: list[Cond]
    sort: list[tuple[str, str]]
    limit: int
    as_of: dt.date | None = None
    policy_sql: tuple[str, tuple[Any, ...]] | None = None
    k_anonymity: bool = False
    extra_where: list[tuple[str, list[Any]]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Compiled:
    sql: str
    params: list[Any]
    keys: list[str]  # output column keys in SELECT order


def _validity(alias: str, as_of: dt.date | None, params: list[Any]) -> str:
    if as_of is None:
        return f"{alias}.valid_to IS NULL"
    params.extend([as_of, as_of])
    return f"{alias}.valid_from <= %s AND ({alias}.valid_to IS NULL OR {alias}.valid_to >= %s)"


def _hierarchy_join(n: int, d: Dim, plan: Plan, params: list[Any]) -> tuple[str, str]:
    """LEFT JOIN of the closure row selecting the leaf's root (or parent) node in the effective hierarchy."""
    c, x, node = f"hc{n}", f"hx{n}", f"hn{n}"
    params.append(d.field.hierarchy)
    sql = (
        f" LEFT JOIN {CLOSURE} {c} ON {c}.descendant_id = {d.field.sql} AND {c}.dimension = %s"
        f" AND {c}.company_id = {plan.entity.alias}.company_id AND " + _validity(c, plan.as_of, params)
    )
    if d.level == "parent":
        sql += f" AND {c}.distance = 1"
    else:  # root: the ancestor that has no proper ancestor itself in the same effective hierarchy
        sql += (
            f" AND NOT EXISTS (SELECT 1 FROM {CLOSURE} {x} WHERE {x}.descendant_id = {c}.ancestor_id"  # noqa: S608  (identifiers from the semantic registry; values are bound parameters)
            f" AND {x}.dimension = {c}.dimension AND {x}.company_id = {c}.company_id AND {x}.distance > 0 AND "
            + _validity(x, plan.as_of, params)
            + ")"
        )
    sql += f" LEFT JOIN {NODE} {node} ON {node}.id = {c}.ancestor_id"
    return sql, f"{node}.id"


def _dim_sql(d: Dim) -> str:
    if d.grain:
        return f"(date_trunc('{GRAIN_SQL[d.grain]}', {d.field.sql}))::date"
    return d.field.sql


def _cond(c: Cond, plan: Plan, params: list[Any]) -> str:
    expr = c.field.sql
    op, value = c.op, c.value
    if op == "eq":
        params.append(value)
        return f"{expr} = %s"
    if op == "ne":
        params.append(value)
        return f"{expr} IS DISTINCT FROM %s"
    if op in ("in", "not_in"):
        params.append(list(value))
        return f"{expr} = ANY(%s)" if op == "in" else f"NOT ({expr} = ANY(%s))"
    if op in ("gt", "gte", "lt", "lte"):
        params.append(value)
        return f"{expr} {({'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='})[op]} %s"
    if op == "between":
        params.extend([value[0], value[1]])
        return f"{expr} BETWEEN %s AND %s"
    if op == "is_null":
        return f"{expr} IS NULL"
    if op == "not_null":
        return f"{expr} IS NOT NULL"
    if op == "contains":
        params.append("%" + str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return f"{expr}::text ILIKE %s"
    if op == "in_subtree":
        # set semantics: selecting a group and one of its children counts each leaf once (DIM-R07)
        params.extend([c.field.hierarchy, list(value), plan.companies])
        sub = (
            f"SELECT hs.descendant_id FROM {CLOSURE} hs WHERE hs.dimension = %s AND hs.ancestor_id = ANY(%s)"  # noqa: S608  (identifiers from the semantic registry; values are bound parameters)
            " AND hs.company_id = ANY(%s) AND " + _validity("hs", plan.as_of, params)
        )
        return f"{expr} IN ({sub})"
    raise ValueError(f"unknown operator {op}")


def compile_plan(plan: Plan) -> Compiled:
    e = plan.entity
    params: list[Any] = []
    select: list[str] = []
    keys: list[str] = []
    joins_needed: list[str] = []

    def need(join: str | None) -> None:
        if join and join not in joins_needed:
            joins_needed.append(join)

    for d in plan.dimensions:
        need(d.field.join)
    for m in plan.measures:
        need(m.join)
    for c in plan.filters:
        need(c.field.join)
    used = {d.field.name for d in plan.dimensions} | {c.field.name for c in plan.filters}
    defaults = [df for df in e.default_filters if not (set(df.unless) & used)]
    for df in defaults:
        need(df.join)

    hierarchy_sql: list[str] = []
    group_exprs: list[str] = []
    for n, d in enumerate(plan.dimensions):
        if d.level:
            join_sql, expr = _hierarchy_join(n, d, plan, params)
            hierarchy_sql.append(join_sql)
        else:
            expr = _dim_sql(d)
        select.append(f"{expr} AS d{n}")
        group_exprs.append(expr)
        keys.append(d.key)
    for n, m in enumerate(plan.measures):
        select.append(AGG_SQL[m.agg].format(e=m.sql) + f" AS m{n}")
        keys.append(m.name)
    if plan.k_anonymity and e.k_subject:
        select.append(f"COUNT(DISTINCT {e.k_subject}) AS k_subjects")
        keys.append("__k")

    sql = f"SELECT {', '.join(select)} FROM {e.table} {e.alias}"  # noqa: S608  (identifiers from the semantic registry; values are bound parameters)
    for name in joins_needed:
        j = e.joins[name]
        sql += f" {j.kind} JOIN {j.table} {j.alias} ON {j.on}"
    # hierarchy params were appended while building SELECT; keep them in statement order
    sql += "".join(hierarchy_sql)

    where: list[str] = [f"{e.alias}.tenant_id = %s", f"{e.alias}.company_id = ANY(%s)"]
    params.extend([plan.tenant_id, plan.companies])
    where.extend(df.sql for df in defaults)
    for c in plan.filters:
        where.append(_cond(c, plan, params))
    if plan.policy_sql is not None:
        sub, sub_params = plan.policy_sql
        where.append(f"{e.alias}.id IN ({sub})")
        params.extend(sub_params)
    for extra, extra_params in plan.extra_where:
        where.append(extra)
        params.extend(extra_params)
    sql += " WHERE " + " AND ".join(f"({w})" for w in where)
    if group_exprs:
        sql += " GROUP BY " + ", ".join(group_exprs)
    order: list[str] = []
    for key, direction in plan.sort:
        idx = keys.index(key)
        col = f"d{idx}" if idx < len(plan.dimensions) else f"m{idx - len(plan.dimensions)}"
        order.append(f"{col} {'DESC' if direction == 'desc' else 'ASC'} NULLS LAST")
    if not order:
        order = [f"d{n} ASC NULLS LAST" for n in range(len(plan.dimensions))]
    if order:
        sql += " ORDER BY " + ", ".join(order)
    sql += " LIMIT %s"
    params.append(plan.limit + 1)  # one extra row tells the engine the result was truncated
    return Compiled(sql, params, keys)


def execute(compiled: Compiled, *, timeout_ms: int) -> list[tuple[Any, ...]]:
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('statement_timeout')")
        previous = cur.fetchone()[0]
        cur.execute("SELECT set_config('statement_timeout', %s, true)", [f"{int(timeout_ms)}ms"])
        try:
            cur.execute(compiled.sql, compiled.params)
            return list(cur.fetchall())
        finally:
            cur.execute("SELECT set_config('statement_timeout', %s, true)", [previous])


def explain(compiled: Compiled) -> dict[str, Any]:
    """PostgreSQL's plan estimate (no execution): total cost and estimated rows."""
    with connection.cursor() as cur:
        cur.execute("EXPLAIN (FORMAT JSON) " + compiled.sql, compiled.params)
        raw = cur.fetchone()[0]
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    return {"total_cost": plan.get("Total Cost"), "plan_rows": plan.get("Plan Rows"), "node": plan.get("Node Type")}


def freshness(entity: Entity, companies: list[uuid.UUID]) -> str:
    """Data-version probe of the entity (cache key part): e.g. count + max(created_at) of the source rows."""
    if not entity.freshness:
        return ""
    with connection.cursor() as cur:
        cur.execute(entity.freshness, {"companies": companies})
        row = cur.fetchone()
    return "|".join(str(v) for v in (row or ()))


def queryset_sql(queryset: Any) -> tuple[str, tuple[Any, ...]] | None:
    """SQL of a Django queryset's primary keys (the policy row scope); ``None`` when it is provably empty."""
    from django.core.exceptions import EmptyResultSet

    try:
        sql, params = queryset.values("pk").query.sql_with_params()
    except EmptyResultSet:
        return None
    return sql, tuple(params)
