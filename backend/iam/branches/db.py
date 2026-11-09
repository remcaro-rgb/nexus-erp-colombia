"""Migration helpers for tables that reference a branch (P09 warehouses, P12 POS configs).

``branch_fk(table)`` adds the composite FK
``(tenant_id, company_id, branch_id) → iam_branch(tenant_id, company_id, id)``:
a warehouse/POS of company A can never point to a branch of company B
(``branch_cross_company`` at the database level).
"""

from __future__ import annotations

from django.db import migrations

from kernel.db import _ident


def branch_fk_sql(table: str, column: str = "branch_id", constraint: str | None = None) -> tuple[str, str]:
    t, c = _ident(table), _ident(column)
    name = _ident(constraint or f"{t}_{c}_branch_fk"[:63])
    forward = (
        f"ALTER TABLE {t} ADD CONSTRAINT {name} FOREIGN KEY (tenant_id, company_id, {c}) "
        f"REFERENCES iam_branch (tenant_id, company_id, id) DEFERRABLE INITIALLY IMMEDIATE;"
    )
    return forward, f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {name};"


def branch_fk(table: str, column: str = "branch_id") -> migrations.RunSQL:
    forward, reverse = branch_fk_sql(table, column)
    return migrations.RunSQL(forward, reverse)
