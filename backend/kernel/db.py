"""Migration helpers (raw SQL generators used by module migrations)."""

from __future__ import annotations

import re

from django.db import migrations

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


FORBID_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION kernel_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  posted_col text := TG_ARGV[0];
  was_posted boolean;
BEGIN
  IF posted_col IS NULL OR posted_col = '' THEN
    was_posted := true;
  ELSE
    EXECUTE format('SELECT ($1).%I', posted_col) INTO was_posted USING OLD;
  END IF;
  IF was_posted THEN
    RAISE EXCEPTION 'immutable row in %: % is not allowed (use a reversal)', TG_TABLE_NAME, TG_OP
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
"""


def immutable_trigger_sql(table: str, posted_column: str | None = "posted") -> tuple[str, str]:
    """(forward, reverse) SQL for the UPDATE/DELETE guard on ``table``.

    ``posted_column=None`` makes every row immutable (append-only ledgers).
    """
    t = _ident(table)
    arg = "" if posted_column is None else _ident(posted_column)
    trigger = f"{t}_immutable_trg"
    forward = (
        FORBID_FUNCTION_SQL
        + f"DROP TRIGGER IF EXISTS {trigger} ON {t};\n"
        + f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {t} "
        + f"FOR EACH ROW EXECUTE FUNCTION kernel_forbid_mutation('{arg}');"
    )
    reverse = f"DROP TRIGGER IF EXISTS {trigger} ON {t};"
    return forward, reverse


def immutable_trigger(table: str, posted_column: str | None = "posted") -> migrations.RunSQL:
    """Migration operation installing the immutability trigger (C1-5)."""
    forward, reverse = immutable_trigger_sql(table, posted_column)
    return migrations.RunSQL(forward, reverse)


def company_fk_sql(table: str, ref_table: str = "iam_company", constraint: str | None = None) -> tuple[str, str]:
    """Composite FK ``(tenant_id, company_id) → ref_table(tenant_id, id)`` (ADR-003).

    ``ref_table`` needs a UNIQUE ``(tenant_id, id)`` constraint (P04).
    """
    t, r = _ident(table), _ident(ref_table)
    name = _ident(constraint or f"{t}_company_fk"[:63])
    forward = (
        f"ALTER TABLE {t} ADD CONSTRAINT {name} FOREIGN KEY (tenant_id, company_id) "
        f"REFERENCES {r} (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;"
    )
    return forward, f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {name};"


def rls_policy_sql(table: str, company_scoped: bool = True) -> tuple[str, str]:
    """Reference RLS policy (P04 owns the final policies, ADR-003).

    Unbound sessions see no rows: ``current_setting(..., true)`` is NULL/'' and
    the comparison is not true.
    """
    t = _ident(table)
    tenant_pred = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    company_pred = (
        " AND company_id = ANY (coalesce(nullif(current_setting('app.company_ids', true), ''), '{}')::uuid[])"
        if company_scoped
        else ""
    )
    pred = tenant_pred + company_pred
    forward = (
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;\n"
        f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t};\n"
        f"CREATE POLICY {t}_tenant_isolation ON {t} USING ({pred}) WITH CHECK ({pred});"
    )
    reverse = (
        f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t};\n"
        f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;"
    )
    return forward, reverse
