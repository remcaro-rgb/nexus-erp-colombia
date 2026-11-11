"""Policy-aware semantic query engine (ADR-009) — the same path for UI, API, exports, scheduled reports, AI and MCP.

``run(ctx, company_ids, spec)`` validates a query ``{entity, measures[], dimensions[], filters[], sort[], limit,
time_grain, compare_to, as_of}`` against the semantic registry and then:

1. **Permission** — ``PolicyService.require(ctx, entity.policy_action)`` (deny by default, 403 ``policy_denied``).
2. **Field security** — a field classified confidential / PII / secret that is not in the principal's
   ``field_mask(resource_type).readable`` cannot be selected, filtered, grouped or sorted (403 ``field_forbidden``).
3. **Row security** — the entity's ``PolicyService.scope_queryset`` (company, branch, warehouse, cost-center
   subtree, owner…) is compiled into the statement as a subquery; the statement also runs under the RLS binding.
4. **Hierarchies** — ``in_subtree`` filters use set semantics over the effective closure (a group and its child
   selected together count each leaf once); ``<field>:root`` / ``<field>:parent`` group by the ancestor in the
   current hierarchy or ``as_of`` a date (totals never change, only grouping — DIM-R06/R07).
5. **k-anonymity** — when a sensitive measure is selected, groups with fewer than k distinct subjects are
   suppressed (values ``null``, ``suppressed: true``) and the totals only add the published rows.
6. **Cost guard** — row limit (≤ 10,000), ``statement_timeout``, and an EXPLAIN estimate above
   :data:`MAX_INTERACTIVE_COST` is refused for interactive use (``semantic_query_invalid``, reason
   ``too_expensive``, offer an async export).
7. **Cache** — results are cached per (policy_version, principal, companies, data freshness, query hash).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.core.cache import cache

from kernel import services, tenancy
from kernel.errors import DomainError, PermissionDenied
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.reporting import ReportQuery, ReportResult
from kernel.tenancy import TenantContext
from reporting import compiler
from reporting.compiler import OPS, Cond, Dim, Plan
from reporting.semantic.registry import TIME_GRAINS, Entity, Field, Measure, entities, entity

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000
MAX_DIMENSIONS = 8
MAX_MEASURES = 12
MAX_FILTERS = 30
MAX_LIST = 1000
MAX_INTERACTIVE_COST = 5_000_000
TIMEOUT_MS = 15_000
CACHE_TTL = 120
COMPARE = ("previous_period", "previous_year")


class SemanticQueryInvalid(DomainError):
    code, status, title = "semantic_query_invalid", 400, "Semantic query invalid"


class FieldForbidden(DomainError):
    code, status, title = "field_forbidden", 403, "Field not authorized"


@dataclass(frozen=True, slots=True)
class QueryOptions:
    interactive: bool = True
    use_cache: bool = True


def _policy() -> PolicyService:
    return services.get(PolicyService)


def _invalid(detail: str, pointer: str, reason: str = "invalid") -> SemanticQueryInvalid:
    return SemanticQueryInvalid(detail, pointer=pointer, reason=reason)


# --------------------------------------------------------------------------- introspection


def visible_entities(ctx: TenantContext) -> list[Entity]:
    policy = _policy()
    return [e for e in entities().values() if policy.authorize(ctx, e.policy_action).allowed]


def readable_fields(ctx: TenantContext, e: Entity) -> set[str]:
    granted = _policy().field_mask(ctx, e.resource_type).readable
    return {f.name for f in e.fields.values() if not f.protected or f.name in granted}


def describe(ctx: TenantContext) -> list[dict[str, Any]]:
    """Semantic model introspection filtered by permission: invisible entities and masked fields are absent."""
    out: list[dict[str, Any]] = []
    for e in sorted(visible_entities(ctx), key=lambda x: x.key):
        readable = readable_fields(ctx, e)
        out.append(
            {
                "entity": e.key,
                "label": e.label,
                "fields": [
                    {"key": f.name, "label": f.label, "type": f.type, "classification": f.classification,
                     "hierarchy": f.hierarchy, "time": f.time}
                    for f in e.fields.values() if f.name in readable
                ],
                "measures": [
                    {"key": m.name, "label": m.label, "type": m.type, "aggregation": m.agg, "sensitive": m.sensitive}
                    for m in e.measures.values()
                ],
                "time_grains": list(TIME_GRAINS),
                "k_anonymity": e.k_min if e.k_subject else None,
            }
        )  # fmt: skip
    return out


# --------------------------------------------------------------------------- validation


def _list(raw: Any, pointer: str, limit: int) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _invalid(f"{pointer} must be a list", pointer)
    if len(raw) > limit:
        raise _invalid(f"{pointer} has at most {limit} items", pointer, "too_many")
    return raw


def _field(e: Entity, name: Any, pointer: str, readable: set[str]) -> Field:
    if not isinstance(name, str):
        raise _invalid("field names are strings", pointer)
    f = e.field_named(name)
    if f is None:
        raise _invalid(f"unknown field {name} of {e.key}", pointer, "unknown_field")
    if f.name not in readable:
        # the same answer for selecting, filtering, grouping and sorting a masked field (no value oracle)
        raise FieldForbidden(f"field {name} is masked for this principal", pointer=pointer)
    return f


def _value(f: Field, raw: Any, pointer: str) -> Any:
    if raw is None:
        raise _invalid("filter values cannot be null (use is_null)", pointer)
    try:
        if f.type == "uuid":
            return uuid.UUID(str(raw))
        if f.type == "date":
            return dt.date.fromisoformat(str(raw))
        if f.type == "integer":
            if isinstance(raw, bool):
                raise ValueError("boolean")
            return int(str(raw))
        if f.type in ("money", "decimal"):
            if isinstance(raw, bool):
                raise ValueError("boolean")
            return Decimal(str(raw))
        if f.type == "boolean":
            if not isinstance(raw, bool):
                raise ValueError("not a boolean")
            return raw
        if not isinstance(raw, str | int) or len(str(raw)) > 200:
            raise ValueError("text too long")
        return str(raw)
    except (ValueError, ArithmeticError) as exc:
        raise _invalid(f"invalid value for {f.name} ({f.type})", pointer, "value") from exc


def _filters(e: Entity, raw: Any, readable: set[str]) -> list[Cond]:
    out: list[Cond] = []
    items: list[Any]
    if isinstance(raw, Mapping):  # {field: value} shorthand (kernel ReportQuery)
        items = [{"field": k, "op": "in" if isinstance(v, list) else "eq", "value": v} for k, v in raw.items()]
    else:
        items = _list(raw, "/filters", MAX_FILTERS)
    for n, item in enumerate(items):
        pointer = f"/filters/{n}"
        if not isinstance(item, Mapping):
            raise _invalid("a filter is an object {field, op, value}", pointer)
        f = _field(e, item.get("field"), f"{pointer}/field", readable)
        op = item.get("op", "eq")
        if op not in OPS:
            raise _invalid(f"unknown operator {op}", f"{pointer}/op", "operator")
        value = item.get("value")
        if op in ("is_null", "not_null"):
            out.append(Cond(f, op))
        elif op in ("in", "not_in", "in_subtree"):
            if op == "in_subtree" and not f.hierarchy:
                raise _invalid(f"{f.name} is not a hierarchical dimension", f"{pointer}/op", "operator")
            values = _list(value, f"{pointer}/value", MAX_LIST)
            if not values:
                raise _invalid("the value list is empty", f"{pointer}/value")
            out.append(Cond(f, op, tuple(_value(f, v, f"{pointer}/value/{i}") for i, v in enumerate(values))))
        elif op == "between":
            values = _list(value, f"{pointer}/value", 2)
            if len(values) != 2:
                raise _invalid("between needs [from, to]", f"{pointer}/value")
            out.append(Cond(f, op, (_value(f, values[0], pointer), _value(f, values[1], pointer))))
        else:
            out.append(Cond(f, op, _value(f, value, f"{pointer}/value")))
    return out


def _dimensions(e: Entity, raw: Any, grain: Any, readable: set[str]) -> list[Dim]:
    out: list[Dim] = []
    for n, item in enumerate(_list(raw, "/dimensions", MAX_DIMENSIONS)):
        pointer = f"/dimensions/{n}"
        if not isinstance(item, str):
            raise _invalid("a dimension is a field name (optionally field:root, field:parent or field:<grain>)",
                           pointer)  # fmt: skip
        name, _, modifier = item.partition(":")
        f = _field(e, name, pointer, readable)
        level = None
        dim_grain = None
        if modifier in ("root", "parent"):
            if not f.hierarchy:
                raise _invalid(f"{name} has no hierarchy", pointer, "hierarchy")
            level = modifier
        elif modifier:
            if modifier not in TIME_GRAINS or f.type != "date":
                raise _invalid(f"unknown modifier {modifier}", pointer)
            dim_grain = modifier
        elif f.time and grain is not None:
            if grain not in TIME_GRAINS:
                raise _invalid(f"time_grain must be one of {', '.join(TIME_GRAINS)}", "/time_grain")
            dim_grain = grain
        key = item if modifier else name
        if any(d.key == key for d in out):
            raise _invalid(f"dimension {key} repeated", pointer)
        out.append(Dim(key, f, dim_grain, level))
    return out


def _measures(e: Entity, raw: Any) -> list[Measure]:
    names = _list(raw, "/measures", MAX_MEASURES)
    if not names:
        raise _invalid("at least one measure is required", "/measures", "required")
    out: list[Measure] = []
    for n, name in enumerate(names):
        m = e.measures.get(name) if isinstance(name, str) else None
        if m is None:
            raise _invalid(f"unknown measure {name} of {e.key}", f"/measures/{n}", "unknown_measure")
        if m not in out:
            out.append(m)
    return out


@dataclass
class Spec:
    entity: Entity
    measures: list[Measure]
    dimensions: list[Dim]
    filters: list[Cond]
    sort: list[tuple[str, str]]
    limit: int
    as_of: dt.date | None
    compare_to: str | None
    raw: dict[str, Any]


def parse_spec(ctx: TenantContext, raw: Mapping[str, Any]) -> Spec:
    if not isinstance(raw, Mapping):
        raise _invalid("the query is an object", "/")
    allowed = {"entity", "measures", "dimensions", "filters", "sort", "limit", "time_grain", "compare_to", "as_of"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _invalid(f"unknown query properties: {', '.join(unknown)}", f"/{unknown[0]}")
    key = raw.get("entity")
    e = entity(key) if isinstance(key, str) else None
    if e is None:
        raise _invalid(f"unknown entity {key}", "/entity", "unknown_entity")
    _policy().require(ctx, e.policy_action)
    readable = readable_fields(ctx, e)
    measures = _measures(e, raw.get("measures"))
    dims = _dimensions(e, raw.get("dimensions"), raw.get("time_grain"), readable)
    filters = _filters(e, raw.get("filters"), readable)
    keys = [d.key for d in dims] + [m.name for m in measures]
    sort: list[tuple[str, str]] = []
    for n, item in enumerate(_list(raw.get("sort"), "/sort", 10)):
        if isinstance(item, str):
            item = {"field": item.lstrip("-"), "dir": "desc" if item.startswith("-") else "asc"}
        if not isinstance(item, Mapping) or item.get("dir", "asc") not in ("asc", "desc"):
            raise _invalid("sort items are {field, dir: asc|desc}", f"/sort/{n}")
        fname = item.get("field")
        if fname not in keys:
            # sorting by a field that is not selected would leak an ordering oracle on masked data
            if isinstance(fname, str) and e.field_named(fname.partition(":")[0]):
                _field(e, fname.partition(":")[0], f"/sort/{n}", readable)
            raise _invalid("sort by a selected dimension or measure", f"/sort/{n}")
        sort.append((str(fname), str(item.get("dir", "asc"))))
    limit = raw.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise _invalid(f"limit is an integer 1..{MAX_LIMIT}", "/limit", "range")
    as_of = None
    if raw.get("as_of") not in (None, ""):
        try:
            as_of = dt.date.fromisoformat(str(raw["as_of"]))
        except ValueError as exc:
            raise _invalid("as_of is a date (YYYY-MM-DD)", "/as_of") from exc
    compare = raw.get("compare_to")
    if compare is not None and compare not in COMPARE:
        raise _invalid(f"compare_to is one of {', '.join(COMPARE)}", "/compare_to")
    if compare and not any(c.field.time and c.op == "between" for c in filters):
        raise _invalid("compare_to needs a between filter on the time field", "/compare_to", "required")
    return Spec(e, measures, dims, filters, sort, limit, as_of, compare, dict(raw))


# --------------------------------------------------------------------------- execution


def _plan(ctx: TenantContext, companies: list[uuid.UUID], spec: Spec, filters: list[Cond] | None = None) -> Plan | None:
    e = spec.entity
    model = django_apps.get_model(e.orm_model)
    scoped = _policy().scope_queryset(
        ctx, e.policy_action, model.objects.filter(tenant_id=ctx.tenant_id, company_id__in=companies)
    )
    policy_sql = compiler.queryset_sql(scoped)
    if policy_sql is None:
        return None  # the policy scope is empty: nothing is visible
    sensitive = any(m.sensitive for m in spec.measures)
    return Plan(
        entity=e,
        tenant_id=ctx.tenant_id,
        companies=companies,
        measures=spec.measures,
        dimensions=spec.dimensions,
        filters=spec.filters if filters is None else filters,
        sort=spec.sort,
        limit=spec.limit,
        as_of=spec.as_of,
        policy_sql=policy_sql,
        k_anonymity=sensitive and bool(e.k_subject),
    )


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def _rows(plan: Plan, spec: Spec, raw_rows: list[tuple[Any, ...]], keys: list[str]) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    suppressed = 0
    for raw in raw_rows[: spec.limit]:
        row = dict(zip(keys, raw, strict=True))
        k = row.pop("__k", None)
        if plan.k_anonymity and k is not None and k < plan.entity.k_min:
            for m in spec.measures:
                if m.sensitive:
                    row[m.name] = None
            row["suppressed"] = True
            suppressed += 1
        out.append({key: _json(v) for key, v in row.items()})
    return out, suppressed


def _shift(c: Cond, mode: str) -> Cond:
    start, end = c.value
    if mode == "previous_year":
        return Cond(c.field, c.op, (start.replace(year=start.year - 1), end.replace(year=end.year - 1)))
    days = (end - start).days + 1
    return Cond(c.field, c.op, (start - dt.timedelta(days=days), start - dt.timedelta(days=1)))


def _totals(spec: Spec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for m in spec.measures:
        if m.agg not in ("sum", "count"):
            continue
        acc = Decimal(0)
        for r in rows:
            v = r.get(m.name)
            if v is not None:
                acc += Decimal(str(v))
        totals[m.name] = format(acc, "f") if m.type in ("money", "decimal") else int(acc)
    return totals


def _labels(spec: Spec, rows: list[dict[str, Any]]) -> None:
    """``<dim>__label`` (code — description) for hierarchy dimensions (node ids → readable labels)."""
    hier = [d for d in spec.dimensions if d.field.hierarchy]
    if not hier or not rows:
        return
    from ledger.models import DimensionNode

    ids = {r[d.key] for d in hier for r in rows if r.get(d.key)}
    names = {str(n.id): f"{n.code} — {n.description}" for n in DimensionNode.objects.filter(id__in=list(ids))}
    for r in rows:
        for d in hier:
            if r.get(d.key):
                r[f"{d.key}__label"] = names.get(str(r[d.key]), "")


def _cache_key(ctx: TenantContext, companies: list[uuid.UUID], spec: Spec, fresh: str) -> str:
    version = _policy().policy_version(ctx)
    raw = json.dumps(
        {"t": str(ctx.tenant_id), "p": str(ctx.principal_id), "c": sorted(str(c) for c in companies), "v": version,
         "f": fresh, "q": spec.raw},
        sort_keys=True, default=str,
    )  # fmt: skip
    return "reporting:q:" + hashlib.sha256(raw.encode()).hexdigest()


def run(
    ctx: TenantContext, companies: Sequence[uuid.UUID], raw: Mapping[str, Any], options: QueryOptions | None = None
) -> dict[str, Any]:
    """Run a semantic query; returns a contract ``ReportResult`` (+ ``truncated``, ``cached``)."""
    opts = options or QueryOptions()
    company_list = sorted({ctx.require_company(c) for c in companies}, key=str)
    if not company_list:
        raise _invalid("at least one company", "/company")
    with tenancy.scope(ctx):
        spec = parse_spec(ctx, raw)
        fresh = compiler.freshness(spec.entity, company_list)
        key = _cache_key(ctx, company_list, spec, fresh)
        if opts.use_cache:
            hit = cache.get(key)
            if hit is not None:
                return {**hit, "cached": True}
        result = _execute(ctx, company_list, spec, opts)
    if opts.use_cache:
        cache.set(key, result, CACHE_TTL)
    return {**result, "cached": False}


def _execute(ctx: TenantContext, companies: list[uuid.UUID], spec: Spec, opts: QueryOptions) -> dict[str, Any]:
    columns = [{"key": d.key, "label": d.field.label + (f" ({d.level or d.grain})" if d.level or d.grain else ""),
                "type": "date" if d.grain else ("uuid" if d.level else d.field.type)} for d in spec.dimensions]
    columns += [{"key": m.name, "label": m.label, "type": m.type} for m in spec.measures]  # fmt: skip
    base = {
        "report": spec.entity.key,
        "columns": columns,
        "parameters": {k: v for k, v in spec.raw.items() if k != "entity"},
        "warnings": [],
        "synthetic": True,
    }
    plan = _plan(ctx, companies, spec)
    if plan is None:
        return {**base, "rows": [], "totals": _totals(spec, []), "truncated": False}
    compiled = compiler.compile_plan(plan)
    if opts.interactive:
        cost = compiler.explain(compiled)
        if (cost.get("total_cost") or 0) > MAX_INTERACTIVE_COST:
            raise SemanticQueryInvalid(
                "the query is too expensive for interactive use; request an asynchronous export",
                reason="too_expensive",
                estimated_cost=cost.get("total_cost"),
                offer="async_export",
            )
    raw_rows = compiler.execute(compiled, timeout_ms=TIMEOUT_MS)
    rows, suppressed = _rows(plan, spec, raw_rows, compiled.keys)
    warnings: list[dict[str, Any]] = []
    if suppressed:
        warnings.append({"code": "k_anonymity_suppressed", "detail": f"{suppressed} grupos con menos de "
                         f"{spec.entity.k_min} personas se ocultaron", "count": suppressed})  # fmt: skip
    if spec.compare_to:
        shifted = [_shift(c, spec.compare_to) if c.field.time and c.op == "between" else c for c in spec.filters]
        cplan = _plan(ctx, companies, spec, shifted)
        prev_rows: list[dict[str, Any]] = []
        if cplan is not None:
            cc = compiler.compile_plan(cplan)
            prev_rows, _ = _rows(cplan, spec, compiler.execute(cc, timeout_ms=TIMEOUT_MS), cc.keys)
        dim_keys = [d.key for d in spec.dimensions]
        index = {tuple(r.get(k) for k in dim_keys): r for r in prev_rows}
        for r in rows:
            prev = index.get(tuple(r.get(k) for k in dim_keys), {})
            for m in spec.measures:
                r[f"{m.name}__compare"] = prev.get(m.name)
        for m in spec.measures:
            columns.append({"key": f"{m.name}__compare", "label": f"{m.label} ({spec.compare_to})", "type": m.type})
    _labels(spec, rows)
    published = [r for r in rows if not r.get("suppressed")]
    return {
        **base,
        "columns": columns,
        "rows": rows,
        "totals": _totals(spec, published),
        "warnings": warnings,
        "truncated": len(raw_rows) > spec.limit,
    }


def explain_query(ctx: TenantContext, companies: Sequence[uuid.UUID], raw: Mapping[str, Any]) -> dict[str, Any]:
    """ActionResult data of ``POST /query:explain``: compiled SQL (placeholders only) + the cost estimate."""
    company_list = sorted({ctx.require_company(c) for c in companies}, key=str)
    with tenancy.scope(ctx):
        spec = parse_spec(ctx, raw)
        plan = _plan(ctx, company_list, spec)
        if plan is None:
            return {"sql": None, "estimate": None, "interactive": True, "empty_scope": True}
        compiled = compiler.compile_plan(plan)
        cost = compiler.explain(compiled)
    return {
        "sql": compiled.sql,
        "parameter_count": len(compiled.params),
        "estimate": cost,
        "interactive": (cost.get("total_cost") or 0) <= MAX_INTERACTIVE_COST,
        "max_interactive_cost": MAX_INTERACTIVE_COST,
    }


# --------------------------------------------------------------------------- kernel.interfaces.ReportQueryService


class SemanticReportQueryService:
    """``kernel.interfaces.ReportQueryService`` over the semantic layer (AI agent P19, MCP P20, other modules)."""

    def run(self, ctx: TenantContext, query: ReportQuery) -> ReportResult:
        if not ctx.company_ids:
            raise PermissionDenied("no company in context", code="company_not_allowed")
        raw: dict[str, Any] = {
            "entity": query.model,
            "measures": list(query.measures),
            "dimensions": list(query.dimensions),
            "filters": dict(query.filters),
            "limit": query.limit,
        }
        result = run(ctx, sorted(ctx.company_ids, key=str), raw)
        keys = tuple(c["key"] for c in result["columns"])
        rows = tuple(tuple(r.get(k) for k in keys) for r in result["rows"])
        return ReportResult(keys, rows, bool(result.get("truncated")))

    def models(self, ctx: TenantContext) -> list[str]:
        return sorted(e.key for e in visible_entities(ctx))


SERVICE = SemanticReportQueryService()
