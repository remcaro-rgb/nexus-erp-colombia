"""Versioned lookup tables (data, never database tables).

A formula may only ``lookup('name', key)`` a table that (1) its input schema
declares and (2) exists for the *evaluating company* on the evaluation date.
Tables of other companies are invisible: the resolver is bound to one company
and reports them exactly like a missing table (no existence oracle).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from workflow.formula.errors import FormulaTypeError, FormulaUnknownIdentifier
from workflow.formula.nodes import Value
from workflow.formula.schema import FIELD_TYPES, check_table_name, coerce

MAX_TABLE_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class VersionedTable:
    company_id: uuid.UUID
    name: str
    version: int
    key_type: str
    value_type: str
    rows: Mapping[Value, Value]
    effective_from: dt.date | None = None
    effective_to: dt.date | None = None

    @classmethod
    def build(
        cls,
        company_id: uuid.UUID,
        name: str,
        rows: Mapping[Any, Any],
        *,
        key_type: str = "string",
        value_type: str = "decimal",
        version: int = 1,
        effective_from: dt.date | None = None,
        effective_to: dt.date | None = None,
    ) -> VersionedTable:
        check_table_name(name)
        if key_type not in ("string", "integer", "decimal") or value_type not in FIELD_TYPES:
            raise FormulaTypeError(f"table {name}: unsupported key/value types")
        if len(rows) > MAX_TABLE_ROWS:
            raise FormulaTypeError(f"table {name}: more than {MAX_TABLE_ROWS} rows")
        typed = {
            coerce(k, key_type, f"table {name} key"): coerce(v, value_type, f"table {name} value")
            for k, v in rows.items()
        }
        return cls(company_id, name, version, key_type, value_type, typed, effective_from, effective_to)

    def covers(self, on: dt.date) -> bool:
        return (self.effective_from is None or self.effective_from <= on) and (
            self.effective_to is None or on <= self.effective_to
        )


@runtime_checkable
class TableResolver(Protocol):
    company_id: uuid.UUID
    accessed: list[tuple[str, int]]

    def get(self, name: str) -> VersionedTable:
        """The table effective for the bound company/date; ``FormulaUnknownIdentifier`` otherwise."""
        ...


@dataclass
class TableRegistry:
    """In-memory store of versioned tables (the persisted twin is ``workflow.models.FormulaTable``)."""

    _tables: dict[tuple[uuid.UUID, str], list[VersionedTable]] = field(default_factory=dict)

    def add(self, table: VersionedTable) -> VersionedTable:
        self._tables.setdefault((table.company_id, table.name), []).append(table)
        return table

    def resolver(self, company_id: uuid.UUID, on: dt.date) -> BoundResolver:
        return BoundResolver(self, company_id, on)

    def find(self, company_id: uuid.UUID, name: str, on: dt.date) -> VersionedTable | None:
        candidates = [t for t in self._tables.get((company_id, name), []) if t.covers(on)]
        return max(candidates, key=lambda t: t.version) if candidates else None


@dataclass(frozen=True)
class BoundResolver:
    registry: TableRegistry
    company_id: uuid.UUID
    on: dt.date
    accessed: list[tuple[str, int]] = field(default_factory=list, compare=False)

    def get(self, name: str) -> VersionedTable:
        check_table_name(name)
        table = self.registry.find(self.company_id, name, self.on)
        if table is None or table.company_id != self.company_id:
            raise FormulaUnknownIdentifier(f"table '{name}' is not available for this company and date")
        self.accessed.append((name, table.version))
        return table


@dataclass(frozen=True)
class NoTables:
    company_id: uuid.UUID = field(default_factory=lambda: uuid.UUID(int=0))
    accessed: list[tuple[str, int]] = field(default_factory=list)

    def get(self, name: str) -> VersionedTable:
        raise FormulaUnknownIdentifier(f"table '{name}' is not available")
