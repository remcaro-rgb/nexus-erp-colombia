"""PostgreSQL row-level security helpers (ADR-003, second line of defense).

``enable(table, company_scoped=True)`` → migration operation with ENABLE + FORCE
RLS and one policy on ``tenant_id = current_setting('app.tenant_id')::uuid``
(and ``company_id = ANY(current_setting('app.company_ids')::uuid[])``).
Unbound sessions see no rows (``current_setting(..., true)`` is NULL/'').
Every module calls it from its own migrations for every tenant table;
``tables_without_rls()`` is used by the CI test that enumerates all tables with
a ``tenant_id`` column.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import connection, migrations

from kernel.db import _ident, rls_policy_sql

TENANT_PRED = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def company_pred(column: str = "company_id") -> str:
    return f"{column} = ANY (coalesce(nullif(current_setting('app.company_ids', true), ''), '{{}}')::uuid[])"


def enable_sql(table: str, company_scoped: bool = True) -> tuple[str, str]:
    return rls_policy_sql(table, company_scoped=company_scoped)


def enable(table: str, company_scoped: bool = True) -> migrations.RunSQL:
    forward, reverse = enable_sql(table, company_scoped)
    return migrations.RunSQL(forward, reverse)


def custom(table: str, predicate: str) -> migrations.RunSQL:
    """RLS with an explicit predicate (e.g. ``iam_company`` scopes on ``id``)."""
    forward = (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};\n"
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate});"
    )
    reverse = (
        f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};\n"
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
    )
    return migrations.RunSQL(forward, reverse)


def tables_without_rls(prefix: str = "") -> list[str]:
    """Tables in the current schema with a ``tenant_id`` column but no forced RLS."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
             WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
               AND c.relname LIKE %s
               AND NOT (c.relrowsecurity AND c.relforcerowsecurity
                        AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
             ORDER BY 1
            """,
            [prefix + "%"],
        )
        return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------- cross-module reference lookups
# Raw SQL is confined to this allow-listed module (scripts/ci/static_checks_allowlist.json). These read
# tables registered by other modules (P09 warehouses, P12 POS configs) whose models IAM cannot import;
# identifiers are validated with kernel.db._ident, values are bound parameters, RLS still applies.


def reference_rows(
    table: str,
    branch_column: str,
    label_column: str,
    active_sql: str,
    tenant_id: uuid.UUID,
    company_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> list[tuple[Any, Any]]:
    t, b, label = _ident(table), _ident(branch_column), _ident(label_column)
    sql = (
        f"SELECT id, {label} FROM {t} "  # noqa: S608 - identifiers validated by _ident; active_sql is module code
        f"WHERE tenant_id = %s AND company_id = %s AND {b} = %s AND ({active_sql}) ORDER BY 2 LIMIT 50"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [str(tenant_id), str(company_id), str(branch_id)])
        return [(r[0], r[1]) for r in cur.fetchall()]


def reference_company(table: str, tenant_id: uuid.UUID, row_id: uuid.UUID) -> uuid.UUID | None:
    t = _ident(table)
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT company_id FROM {t} WHERE tenant_id = %s AND id = %s",  # noqa: S608 - validated identifier
            [str(tenant_id), str(row_id)],
        )
        row = cur.fetchone()
    return uuid.UUID(str(row[0])) if row else None
