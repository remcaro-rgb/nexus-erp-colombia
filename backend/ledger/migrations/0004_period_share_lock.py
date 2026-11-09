"""``ledger_period_status_for_share(id)``: reads a period's status under ``FOR SHARE`` (decision D9).

Concurrent postings share the lock and do not serialize; a period close (UPDATE) waits for them. Defined here so the
posting engine calls it through the ORM (``Func``) instead of raw SQL (ADR-004 static checks). SECURITY INVOKER:
row-level security of ``ledger_period`` still applies to the caller.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION ledger_period_status_for_share(p_id uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY INVOKER AS $$
DECLARE s text;
BEGIN
  SELECT status INTO s FROM ledger_period WHERE id = p_id FOR SHARE;
  RETURN s;
END $$;
"""
REVERSE = "DROP FUNCTION IF EXISTS ledger_period_status_for_share(uuid);"


class Migration(migrations.Migration):
    dependencies = [("ledger", "0003_rls")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
