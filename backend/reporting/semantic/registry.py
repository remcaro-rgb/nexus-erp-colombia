"""Semantic model registry (ADR-009): ``backend/reporting/semantic/models/<module>.yaml``.

Each module contributes one YAML file (by CCR; P17 merges). An entity declares its table and alias, the declared
joins (no ad-hoc joins), the fields (type, PII/confidentiality classification, optional hierarchy dimension or time
role), the measures (sum / avg / count / count_distinct / min / max over a Decimal-safe SQL expression; ``sensitive``
measures are k-anonymised), default filters, the row-policy hook (``orm_model`` + ``policy_action`` →
``PolicyService.scope_queryset``) and the field-mask resource (``resource_type`` → ``PolicyService.field_mask``).

Names in the YAML are validated here (``[a-z][a-z0-9_]*``); SQL fragments are repository content reviewed with the
model — user input never reaches them (it only selects names, and values are bound parameters).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODELS_DIR = Path(__file__).resolve().parent / "models"
NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,15}\Z")
FIELD_TYPES = ("uuid", "string", "date", "integer", "money", "decimal", "boolean")
AGGREGATES = ("sum", "avg", "count", "count_distinct", "min", "max")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret")
#: classifications that need an explicit field-mask grant (``PolicyService.field_mask(...).readable``)
PROTECTED = frozenset({"confidential", "restricted-pii", "sensitive-pii", "secret"})
HIERARCHIES = ("cost_center", "business_unit", "aux_abierto", "item_contable")
TIME_GRAINS = ("day", "week", "month", "quarter", "year")


class SemanticModelError(ValueError):
    """A semantic YAML file is invalid (raised at load time; CI fails on it)."""


@dataclass(frozen=True, slots=True)
class Join:
    name: str
    table: str
    alias: str
    on: str
    kind: str  # INNER | LEFT


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    sql: str
    type: str
    label: str
    classification: str = "internal"
    join: str | None = None
    hierarchy: str | None = None
    time: bool = False

    @property
    def protected(self) -> bool:
        return self.classification in PROTECTED


@dataclass(frozen=True, slots=True)
class Measure:
    name: str
    sql: str
    agg: str
    type: str
    label: str
    join: str | None = None
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class DefaultFilter:
    sql: str
    join: str | None = None
    unless: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Entity:
    key: str  # <module>.<entity>
    module: str
    label: str
    table: str
    alias: str
    orm_model: str
    policy_action: str
    resource_type: str
    fields: dict[str, Field]
    measures: dict[str, Measure]
    joins: dict[str, Join] = field(default_factory=dict)
    default_filters: tuple[DefaultFilter, ...] = ()
    freshness: str | None = None
    k_subject: str | None = None
    k_min: int = 5

    def field_named(self, name: str) -> Field | None:
        return self.fields.get(name)


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        raise SemanticModelError(f"{where}: invalid name {value!r}")
    return value


def _str(raw: dict[str, Any], key: str, where: str, *, required: bool = True) -> str | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise SemanticModelError(f"{where}: missing {key}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise SemanticModelError(f"{where}: {key} must be a non-empty string")
    if ";" in value or "--" in value:
        raise SemanticModelError(f"{where}: {key} must be a single SQL expression")
    return value


def _entity(module: str, name: str, raw: dict[str, Any]) -> Entity:
    where = f"{module}.{name}"
    joins: dict[str, Join] = {}
    for jname, j in (raw.get("joins") or {}).items():
        _name(jname, f"{where}.joins")
        alias = _str(j, "alias", f"{where}.joins.{jname}") or ""
        if not ALIAS.match(alias):
            raise SemanticModelError(f"{where}.joins.{jname}: invalid alias")
        kind = str(j.get("type", "inner")).upper()
        if kind not in ("INNER", "LEFT"):
            raise SemanticModelError(f"{where}.joins.{jname}: type is inner or left")
        joins[jname] = Join(
            jname, _str(j, "table", where) or "", alias, _str(j, "on", f"{where}.joins.{jname}") or "", kind
        )
    fields: dict[str, Field] = {}
    for fname, f in (raw.get("fields") or {}).items():
        _name(fname, f"{where}.fields")
        ftype = f.get("type")
        if ftype not in FIELD_TYPES:
            raise SemanticModelError(f"{where}.fields.{fname}: type must be one of {FIELD_TYPES}")
        cls = f.get("classification", "internal")
        if cls not in CLASSIFICATIONS:
            raise SemanticModelError(f"{where}.fields.{fname}: unknown classification {cls}")
        join = f.get("join")
        if join is not None and join not in joins:
            raise SemanticModelError(f"{where}.fields.{fname}: undeclared join {join}")
        hierarchy = f.get("hierarchy")
        if hierarchy is not None and hierarchy not in HIERARCHIES:
            raise SemanticModelError(f"{where}.fields.{fname}: unknown hierarchy {hierarchy}")
        fields[fname] = Field(
            fname,
            _str(f, "sql", f"{where}.fields.{fname}") or "",
            ftype,
            str(f.get("label", fname)),
            cls,
            join,
            hierarchy,
            bool(f.get("time", False)),
        )
    measures: dict[str, Measure] = {}
    for mname, m in (raw.get("measures") or {}).items():
        _name(mname, f"{where}.measures")
        if mname in fields:
            raise SemanticModelError(f"{where}.measures.{mname}: name clashes with a field")
        agg = m.get("agg")
        if agg not in AGGREGATES:
            raise SemanticModelError(f"{where}.measures.{mname}: agg must be one of {AGGREGATES}")
        mtype = m.get("type", "decimal")
        if mtype not in FIELD_TYPES:
            raise SemanticModelError(f"{where}.measures.{mname}: invalid type")
        join = m.get("join")
        if join is not None and join not in joins:
            raise SemanticModelError(f"{where}.measures.{mname}: undeclared join {join}")
        measures[mname] = Measure(
            mname, _str(m, "sql", f"{where}.measures.{mname}") or "", agg, mtype, str(m.get("label", mname)), join,
            bool(m.get("sensitive", False)),
        )  # fmt: skip
    defaults: list[DefaultFilter] = []
    for n, d in enumerate(raw.get("default_filters") or []):
        join = d.get("join")
        if join is not None and join not in joins:
            raise SemanticModelError(f"{where}.default_filters[{n}]: undeclared join {join}")
        unless = tuple(d.get("unless") or ())
        for u in unless:
            if u not in fields:
                raise SemanticModelError(f"{where}.default_filters[{n}]: unknown field {u}")
        defaults.append(DefaultFilter(_str(d, "sql", f"{where}.default_filters[{n}]") or "", join, unless))
    k = raw.get("k_anonymity") or {}
    if "company_id" not in fields:
        raise SemanticModelError(f"{where}: every entity declares its company_id field (company row scope)")
    alias = _str(raw, "alias", where) or ""
    if not ALIAS.match(alias):
        raise SemanticModelError(f"{where}: invalid alias")
    return Entity(
        key=f"{module}.{name}",
        module=module,
        label=str(raw.get("label", name)),
        table=_str(raw, "table", where) or "",
        alias=alias,
        orm_model=_str(raw, "orm_model", where) or "",
        policy_action=_str(raw, "policy_action", where) or "",
        resource_type=_str(raw, "resource_type", where) or "",
        fields=fields,
        measures=measures,
        joins=joins,
        default_filters=tuple(defaults),
        freshness=raw.get("freshness"),
        k_subject=k.get("subject"),
        k_min=int(k.get("k", 5)),
    )


def load_file(path: Path) -> list[Entity]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    module = _name(raw.get("module"), str(path.name))
    if path.stem != module:
        raise SemanticModelError(f"{path.name}: the file of module {module} is {module}.yaml")
    return [_entity(module, _name(n, path.name), e) for n, e in (raw.get("entities") or {}).items()]


@functools.cache
def entities() -> dict[str, Entity]:
    out: dict[str, Entity] = {}
    for path in sorted(MODELS_DIR.glob("*.yaml")):
        for e in load_file(path):
            out[e.key] = e
    return out


def entity(key: str) -> Entity | None:
    return entities().get(key)
