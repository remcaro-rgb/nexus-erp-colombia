"""Template framework (F-X-DATAIO data model): columns, typed cells, records and the adapter contract.

A :class:`Template` describes one importable/exportable entity (``contracts/dataio/<entity>.yaml``) and adapts it
to the **owning module's service functions** — dataio never writes another module's tables directly. Adapters
implement:

* ``find(ctx, company, key)`` — the existing row for a natural key, *only inside the job's company*;
* ``current(obj)`` — the row's values in template terms (typed), used for upsert diffs and export;
* ``create(uow, company, rec)`` / ``update(uow, obj, rec, changed)`` — calls into the owner service;
* ``queryset(ctx, company)`` — rows to export (the engine applies ``scope_queryset`` of ``read_permission``).

Line templates (``line_columns``) carry one row per line; rows with the same natural key form one record.
Hierarchical templates (``parent_column``) are ordered parents-first inside the file (two-pass resolution).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from django.db.models import QuerySet

from dataio.errors import RowError
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

TYPES = ("string", "date", "decimal", "integer", "boolean", "list", "json")
TRUE = {"true", "1", "si", "sí", "s", "yes", "y", "verdadero", "x"}
FALSE = {"false", "0", "no", "n", "falso"}


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str = "string"
    required: bool = False
    allowed: tuple[str, ...] = ()
    max_length: int | None = None
    help: str = ""
    example: str = ""
    synonyms: tuple[str, ...] = ()
    #: extra permission to read/write this column (e.g. cost columns need the cost permission)
    permission: str | None = None
    #: line-level column of a line template
    line: bool = False
    #: list column whose order carries no meaning (compared and stored sorted)
    unordered: bool = False

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required, "help": self.help}
        if self.allowed:
            out["allowed"] = list(self.allowed)
        if self.max_length:
            out["max_length"] = self.max_length
        if self.example:
            out["example"] = self.example
        if self.permission:
            out["permission"] = self.permission
        if self.line:
            out["line"] = True
        return out


@dataclass
class Record:
    """One logical record of the file (one row, or the rows of one natural key for line templates)."""

    key: tuple[Any, ...]
    values: dict[str, Any]
    present: frozenset[str]
    rows: list[int]
    lines: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- typing


def _norm_decimal(text: str, decimal_comma: bool) -> Decimal:
    t = text.strip().replace(" ", "").replace("$", "")
    if decimal_comma:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." not in t and t.count(",") == 1:
        t = t.replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError("decimal") from None
    if not d.is_finite():
        raise ValueError("decimal")
    return d


def parse_cell(col: Column, raw: str, *, decimal_comma: bool = False) -> Any:
    """``raw`` (text) as the column's Python value; ``None`` when empty. Raises :class:`RowError`."""
    text = "" if raw is None else str(raw)
    if col.type != "json":
        text = text.strip()
    if text.startswith("'") and len(text) > 1 and text[1] in "=+-@":
        text = text[1:]  # our own formula neutralisation (export → import round trip)
    if text == "":
        if col.required:
            raise RowError("required", f"{col.name} es obligatorio", col.name)
        return None
    try:
        value: Any
        if col.type == "string":
            value = text
            if col.max_length and len(value) > col.max_length:
                raise RowError("max_length", f"{col.name}: máximo {col.max_length} caracteres", col.name)
            if col.allowed:
                up = value.upper() if all(a.upper() == a for a in col.allowed) else value
                if up not in col.allowed:
                    raise RowError(
                        "enum", f"{col.name}: valor {value!r} no permitido ({', '.join(col.allowed)})", col.name
                    )
                value = up
        elif col.type == "date":
            value = _parse_date(text)
        elif col.type == "decimal":
            value = _norm_decimal(text, decimal_comma)
        elif col.type == "integer":
            d = _norm_decimal(text, decimal_comma)
            if d != d.to_integral_value():
                raise ValueError("integer")
            value = int(d)
        elif col.type == "boolean":
            low = text.lower()
            if low in TRUE:
                value = True
            elif low in FALSE:
                value = False
            else:
                raise ValueError("boolean")
        elif col.type == "list":
            value = tuple(p.strip() for p in text.split("|") if p.strip())
            if col.unordered:
                value = tuple(sorted(set(value)))
            for p in value:
                if col.allowed and p not in col.allowed:
                    raise RowError("enum", f"{col.name}: valor {p!r} no permitido", col.name)
        elif col.type == "json":
            value = json.loads(text, parse_float=Decimal)
        else:  # pragma: no cover - guarded by TYPES
            raise ValueError(col.type)
    except RowError:
        raise
    except ValueError:
        raise RowError("type", f"{col.name}: {text[:60]!r} no es un valor {col.type} válido", col.name) from None
    return value


def _parse_date(text: str) -> dt.date:
    if re.fullmatch(r"\d{4,5}(\.0+)?", text) and int(text.split(".")[0]) > 20000:
        # spreadsheet serial day (1900 system)
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(text.split(".")[0]))
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text[:19] if "T" in text else text, fmt).replace(tzinfo=dt.UTC).date()
        except ValueError:
            continue
    raise ValueError("date")


def format_value(value: Any) -> str:
    """Canonical text of a template value (export side; inverse of :func:`parse_cell`)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(value.quantize(Decimal(1)))
        return format(value.normalize(), "f")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        if value and isinstance(value[0], dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return "|".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def same(a: Any, b: Any) -> bool:
    """Equality in template terms (``None`` ≡ ``""`` ≡ empty list; Decimals by value; JSON canonical)."""
    empty: tuple[Any, ...] = (None, "", (), [], {})
    if a in empty and b in empty:
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) or isinstance(b, dict):
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return False
    return bool(a == b)


# --------------------------------------------------------------------------- adapter contract


class Template:
    entity: ClassVar[str]
    label: ClassVar[str]
    module: ClassVar[str]
    owner: ClassVar[str]
    feature: ClassVar[str] = "F-X-DATAIO"
    columns: ClassVar[tuple[Column, ...]]
    natural_key: ClassVar[tuple[str, ...]]
    read_permission: ClassVar[str]
    create_permission: ClassVar[str]
    update_permission: ClassVar[str]
    #: documents are imported as DRAFT (never submitted, approved or posted by an import)
    document: ClassVar[bool] = False
    #: hierarchy: column holding the parent's natural code (two-pass resolution)
    parent_column: ClassVar[str | None] = None
    #: create-only templates (no update path in the owner service)
    updatable: ClassVar[bool] = True
    examples: ClassVar[tuple[dict[str, str], ...]] = ()
    notes: ClassVar[str] = ""

    # -- metadata -------------------------------------------------------------
    @classmethod
    def column(cls, name: str) -> Column:
        for c in cls.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    @classmethod
    def header_columns(cls) -> tuple[Column, ...]:
        return tuple(c for c in cls.columns if not c.line)

    @classmethod
    def line_columns(cls) -> tuple[Column, ...]:
        return tuple(c for c in cls.columns if c.line)

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "entity": cls.entity,
            "label": cls.label,
            "module": cls.module,
            "owner": cls.owner,
            "feature": cls.feature,
            "natural_key": list(cls.natural_key),
            "permission": cls.create_permission,
            "permissions": {
                "read": cls.read_permission,
                "create": cls.create_permission,
                "update": cls.update_permission,
            },
            "document": cls.document,
            "hierarchy": cls.parent_column is not None,
            "updatable": cls.updatable,
            "formats": ["csv", "xlsx", "jsonl"],
            "mode": "two-phase: validate → apply; atomic per batch; idempotent by batch hash",
            "columns": [c.describe() for c in cls.columns],
            "examples": [dict(e) for e in cls.examples],
            "notes": cls.notes,
        }

    # -- adapter ----------------------------------------------------------------
    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        raise NotImplementedError

    def current(self, obj: Any) -> dict[str, Any]:
        raise NotImplementedError

    def current_lines(self, obj: Any) -> list[dict[str, Any]]:
        return []

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        raise NotImplementedError

    def update(self, uow: UnitOfWork, obj: Any, rec: Record, changed: set[str]) -> Any:
        raise RowError("not_updatable", f"{self.entity}: el registro ya existe y no admite actualización")

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        raise NotImplementedError

    def export_rows(self, obj: Any) -> list[dict[str, Any]]:
        head = self.current(obj)
        lines = self.current_lines(obj)
        if not self.line_columns():
            return [head]
        return [{**head, **ln} for ln in lines] or [head]

    # -- hierarchy (two-pass) -----------------------------------------------------
    def node_key(self, rec: Record) -> tuple[Any, ...]:
        return tuple(v.lower() if isinstance(v, str) else v for v in rec.key)

    def parent_key(self, rec: Record) -> tuple[Any, ...] | None:
        """Natural key of the record's parent (the code is the last natural-key column)."""
        if self.parent_column is None:
            return None
        parent = rec.values.get(self.parent_column)
        if not parent:
            return None
        return (*self.node_key(rec)[:-1], str(parent).lower())

    # -- helpers for adapters -----------------------------------------------------
    @staticmethod
    def pick(values: Mapping[str, Any], names: Iterable[str], present: Iterable[str] | None = None) -> dict[str, Any]:
        keep = set(present) if present is not None else None
        return {n: values.get(n) for n in names if keep is None or n in keep}


def topo_order(
    records: Sequence[Record], key_of: Callable[[Record], Hashable], parent_of: Callable[[Record], Hashable | None]
) -> tuple[list[Record], list[Record]]:
    """Parents before children (two-pass resolution of in-file references): ``(ordered, cyclic)``.

    ``cyclic`` holds every record on a parent cycle and every descendant of one; parents that are not in the
    file are resolved later against the database by the adapter."""
    by_key = {key_of(r): r for r in records}
    ordered: list[Record] = []
    cyclic: list[Record] = []
    done: dict[int, bool] = {}  # id → True (ordered) / False (cyclic)
    for start in records:
        chain: list[Record] = []
        cur: Record | None = start
        bad = False
        while cur is not None and id(cur) not in done:
            if any(c is cur for c in chain):
                bad = True
                break
            chain.append(cur)
            pk = parent_of(cur)
            cur = by_key.get(pk) if pk is not None else None
        if cur is not None and done.get(id(cur)) is False:
            bad = True
        for r in reversed(chain):
            done[id(r)] = not bad
            (cyclic if bad else ordered).append(r)
    return ordered, cyclic
