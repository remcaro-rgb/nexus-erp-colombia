"""Synthetic datasets for the leak suite: rows spread over 2 tenants x 3 companies, owned by
different principals and placed in different warehouses, so every visibility rule (tenant, company,
owner, warehouse) has rows on both sides of the boundary. All values are labelled SINTÉTICO."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .oracle import Dataset, Row
from .principals import Kind, Matrix, synthetic_id

FieldsFactory = Callable[[str, int], dict[str, Any]]


def default_fields(label: str, n: int) -> dict[str, Any]:
    return {"code": f"SYN-{n:04d}", "name": f"{label} SINTÉTICO {n}"}


def seed_resource(
    dataset: Dataset,
    matrix: Matrix,
    resource: str,
    *,
    owners: Iterable[Kind] = (Kind.SALESPERSON, Kind.ADMIN),
    warehouses: Iterable[int] = (),
    company_scoped: bool = True,
    fields: FieldsFactory = default_fields,
) -> list[Row]:
    rows: list[Row] = []
    n = 0
    for tenant in matrix.tenants:
        companies = tenant.company_ids if company_scoped else (None,)
        for ci, company in enumerate(companies, start=1):
            for owner_kind in owners:
                for wh in tuple(warehouses) or (None,):
                    n += 1
                    rid = synthetic_id(resource, tenant.key, str(ci), owner_kind.value, str(wh))
                    owner = matrix.of(tenant.key, owner_kind).id
                    warehouse = tenant.warehouse(ci, wh) if wh is not None else None
                    row = Row(resource, rid, tenant.id, company, owner, warehouse, fields(resource, n))
                    dataset.add(row)
                    rows.append(row)
    return rows
