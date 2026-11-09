"""The expected policy ("oracle") the leak suite compares every channel against.

A :class:`Dataset` holds synthetic rows with their security facts (tenant, company, owner,
warehouse). The oracle answers, for a principal:

* ``allows(code)``            - function-level permission (BFLA),
* ``can_see(row)``            - row-level visibility (BOLA / invariant 12),
* ``forbidden_fields(op)``    - fields above the principal's classification ceiling (field security).

The oracle never consults the system under test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .openapi import Operation
from .principals import OWNER_SCOPED_RESOURCES, WAREHOUSE_SCOPED_MODULES, Principal


@dataclass(frozen=True, slots=True)
class Row:
    resource: str  # ``<module>.<resource>`` as in the permission code
    id: uuid.UUID
    tenant_id: uuid.UUID
    company_id: uuid.UUID | None
    owner_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    fields: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def as_json(self) -> dict[str, Any]:
        data = {"id": str(self.id), "tenant_id": str(self.tenant_id)}
        if self.company_id is not None:
            data["company_id"] = str(self.company_id)
        data.update(self.fields)
        return data


@dataclass
class Dataset:
    rows: dict[uuid.UUID, Row] = field(default_factory=dict)

    def add(self, row: Row) -> Row:
        if row.id in self.rows:
            raise ValueError(f"duplicate synthetic id {row.id}")
        self.rows[row.id] = row
        return row

    def extend(self, rows: Iterable[Row]) -> None:
        for r in rows:
            self.add(r)

    def of(self, resource: str) -> list[Row]:
        return [r for r in self.rows.values() if r.resource == resource]

    def get(self, row_id: uuid.UUID | str) -> Row | None:
        try:
            key = row_id if isinstance(row_id, uuid.UUID) else uuid.UUID(str(row_id))
        except ValueError:
            return None
        return self.rows.get(key)

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows.values())

    def __len__(self) -> int:
        return len(self.rows)


def effective_user(p: Principal) -> Principal:
    return p.on_behalf_of if p.on_behalf_of is not None else p


def can_see(p: Principal, row: Row) -> bool:
    if row.tenant_id != p.tenant.id:
        return False
    if row.company_id is not None and row.company_id not in p.company_ids:
        return False
    if p.owner_scoped and row.resource in OWNER_SCOPED_RESOURCES and row.owner_id != effective_user(p).id:
        return False
    module = row.resource.split(".", 1)[0]
    return not (
        p.warehouse_ids is not None
        and module in WAREHOUSE_SCOPED_MODULES
        and row.warehouse_id is not None
        and row.warehouse_id not in p.warehouse_ids
    )


def visible(p: Principal, rows: Iterable[Row]) -> list[Row]:
    return [r for r in rows if can_see(p, r)]


def forbidden_fields(p: Principal, op: Operation) -> frozenset[str]:
    """Top-level and nested dotted fields the principal must not receive (``secret`` always)."""
    return op.fields_above(p.max_classification) | frozenset(n for n, c in op.response_fields if c == "secret")


def expected_statuses(p: Principal | None, op: Operation, target: Row | None) -> frozenset[int]:
    """Acceptable HTTP statuses for ``p`` calling ``op`` on ``target`` (None = collection)."""
    if p is None:
        # Refused (D-ALL 1). The exact 401 + WWW-Authenticate contract is checked by the conformance suite.
        return frozenset({401, 403})
    allowed = p.allows(op.permission)
    seen = target is None or can_see(p, target)
    if not allowed and not seen:
        return frozenset({403, 404})
    if not allowed:
        return frozenset({403})
    if not seen:
        # Never reveal existence: foreign or out-of-scope objects answer 404 (403 tolerated, never 2xx).
        return frozenset({404, 403})
    if op.method == "get":
        return frozenset({200})
    # Allowed and visible writes are not executed by the leak suite (see leak.generate_cases).
    return frozenset(range(200, 300))
