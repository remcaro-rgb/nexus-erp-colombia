"""Row-level security and immutability guards for every treasury table (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* Posted voucher lines are immutable (``kernel_forbid_mutation``).
* A posted voucher header is immutable except the single ``POSTED → REVERSED`` transition with its
  ``reversed_by_id`` link (and the version/updated stamps): corrections are reversal vouchers, never edits.
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = [
    "treasury_settings",
    "treasury_fund",
    "treasury_bank_account",
    "treasury_cash_box",
    "treasury_concept",
    "treasury_voucher",
    "treasury_voucher_line",
    "treasury_ap_application",
    "treasury_payment_batch",
    "treasury_payment_batch_line",
    "treasury_bank_statement",
    "treasury_statement_line",
]

VOUCHER_GUARD = """
CREATE OR REPLACE FUNCTION treasury_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  allowed text[] := ARRAY['status', 'reversed_by_id', 'version', 'updated_at', 'updated_by'];
BEGIN
  IF NOT OLD.posted THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'immutable row in %: DELETE is not allowed (use a reversal)', TG_TABLE_NAME
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed)
     OR NOT (NEW.status = OLD.status OR (OLD.status = 'POSTED' AND NEW.status = 'REVERSED'))
     OR (OLD.reversed_by_id IS NOT NULL AND NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id) THEN
    RAISE EXCEPTION 'immutable row in %: a posted voucher only changes POSTED -> REVERSED', TG_TABLE_NAME
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS treasury_voucher_immutable_trg ON treasury_voucher;
CREATE TRIGGER treasury_voucher_immutable_trg BEFORE UPDATE OR DELETE ON treasury_voucher
FOR EACH ROW EXECUTE FUNCTION treasury_voucher_guard();
"""
VOUCHER_GUARD_REVERSE = """
DROP TRIGGER IF EXISTS treasury_voucher_immutable_trg ON treasury_voucher;
DROP FUNCTION IF EXISTS treasury_voucher_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("treasury", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        immutable_trigger("treasury_voucher_line"),
        migrations.RunSQL(VOUCHER_GUARD, VOUCHER_GUARD_REVERSE),
    ]
