"""Forced row-level security on every consolidation/intercompany table (ADR-003) and append-only guards (C1-5).

* Intercompany agreements and links: visible to a context bound to **either** company of the pair.
* Intercompany documents (B-side mirror invoices, settlements): company scoped (the company that owns them).
* Consolidation groups/runs/book: tenant scoped; the API additionally requires every member company in the
  context and the permission in each of them (a group spanning a hidden company is never disclosed).
* ``consolidation_entry`` / ``consolidation_entry_line`` (consolidation book) and ``consolidation_ic_document`` are
  append-only; ``consolidation_ic_link`` is append-only except ``consumed_amount`` (buyer consumption).
"""

from django.db import migrations

from iam.rls import company_pred, custom
from kernel.db import immutable_trigger_sql, rls_policy_sql

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
PAIR = f"{TENANT} AND ({company_pred('company_a_id')} OR {company_pred('company_b_id')})"
TENANT_TABLES = (
    "consolidation_group",
    "consolidation_group_member",
    "consolidation_account_map",
    "consolidation_run",
    "consolidation_run_line",
    "consolidation_entry",
    "consolidation_entry_line",
)
APPEND_ONLY = ("consolidation_entry", "consolidation_entry_line", "consolidation_ic_document")

_tenant = [rls_policy_sql(t, company_scoped=False) for t in TENANT_TABLES]
_docs = rls_policy_sql("consolidation_ic_document")
_guards = [immutable_trigger_sql(t, None) for t in APPEND_ONLY]

LINK_GUARD = """
CREATE OR REPLACE FUNCTION consolidation_ic_link_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR (to_jsonb(NEW) - 'consumed_amount') IS DISTINCT FROM (to_jsonb(OLD) - 'consumed_amount') THEN
    RAISE EXCEPTION 'immutable row in %: % is not allowed (use a reversal)', TG_TABLE_NAME, TG_OP
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS consolidation_ic_link_immutable_trg ON consolidation_ic_link;
CREATE TRIGGER consolidation_ic_link_immutable_trg BEFORE UPDATE OR DELETE ON consolidation_ic_link
  FOR EACH ROW EXECUTE FUNCTION consolidation_ic_link_guard();
"""
LINK_GUARD_REVERSE = (
    "DROP TRIGGER IF EXISTS consolidation_ic_link_immutable_trg ON consolidation_ic_link;\n"
    "DROP FUNCTION IF EXISTS consolidation_ic_link_guard();"
)


class Migration(migrations.Migration):
    dependencies = [("consolidation", "0001_initial"), ("iam", "0002_security")]

    operations = [
        migrations.RunSQL("\n".join(f for f, _ in _tenant), "\n".join(r for _, r in reversed(_tenant))),
        migrations.RunSQL(_docs[0], _docs[1]),
        custom("consolidation_ic_agreement", PAIR),
        custom("consolidation_ic_link", PAIR),
        migrations.RunSQL("\n".join(f for f, _ in _guards), "\n".join(r for _, r in reversed(_guards))),
        migrations.RunSQL(LINK_GUARD, LINK_GUARD_REVERSE),
    ]
