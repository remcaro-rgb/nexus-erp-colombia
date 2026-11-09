"""RLS, composite FKs and integrity triggers for the IAM tables (ADR-003)."""

from django.db import migrations

from iam import rls
from kernel.db import company_fk_sql

TENANT_ONLY = [
    "iam_user",
    "iam_membership",
    "iam_role",
    "iam_row_policy",
    "iam_field_policy",
    "iam_external_account",
    "iam_portal_invitation",
    "iam_company",
]
COMPANY_SCOPED = ["iam_branch", "iam_role_assignment"]

HEAD_OFFICE_SQL = """
CREATE OR REPLACE FUNCTION iam_branch_head_office_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  active_count integer;
  head_count integer;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE is_head_office)
    INTO active_count, head_count
    FROM iam_branch
   WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id AND status = 'ACTIVE';
  IF active_count > 0 AND head_count <> 1 THEN
    RAISE EXCEPTION 'company % must have exactly one active head office (found %)', NEW.company_id, head_count
      USING ERRCODE = 'P0001', HINT = 'iam.branch_head_office_required';
  END IF;
  RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER iam_branch_head_office_trg
  AFTER INSERT OR UPDATE ON iam_branch
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION iam_branch_head_office_check();

CREATE OR REPLACE FUNCTION iam_branch_company_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.company_id <> OLD.company_id OR NEW.tenant_id <> OLD.tenant_id THEN
    RAISE EXCEPTION 'branch company is immutable' USING ERRCODE = 'P0001', HINT = 'iam.branch_cross_company';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER iam_branch_company_immutable_trg BEFORE UPDATE ON iam_branch
  FOR EACH ROW EXECUTE FUNCTION iam_branch_company_immutable();

CREATE OR REPLACE FUNCTION iam_role_assignment_internal_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  user_kind text;
BEGIN
  SELECT kind INTO user_kind FROM iam_user WHERE id = NEW.user_id AND tenant_id = NEW.tenant_id;
  IF user_kind IS DISTINCT FROM 'INTERNAL' THEN
    RAISE EXCEPTION 'roles can only be assigned to internal users'
      USING ERRCODE = 'P0001', HINT = 'iam.external_role_forbidden';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER iam_role_assignment_internal_only_trg BEFORE INSERT OR UPDATE ON iam_role_assignment
  FOR EACH ROW EXECUTE FUNCTION iam_role_assignment_internal_only();
"""

HEAD_OFFICE_REVERSE = """
DROP TRIGGER IF EXISTS iam_branch_head_office_trg ON iam_branch;
DROP TRIGGER IF EXISTS iam_branch_company_immutable_trg ON iam_branch;
DROP TRIGGER IF EXISTS iam_role_assignment_internal_only_trg ON iam_role_assignment;
DROP FUNCTION IF EXISTS iam_branch_head_office_check();
DROP FUNCTION IF EXISTS iam_branch_company_immutable();
DROP FUNCTION IF EXISTS iam_role_assignment_internal_only();
"""


def _company_fk(table: str) -> migrations.RunSQL:
    forward, reverse = company_fk_sql(table)
    return migrations.RunSQL(forward, reverse)


class Migration(migrations.Migration):
    dependencies = [("iam", "0001_initial")]

    operations = [
        *[_company_fk(t) for t in ("iam_branch", "iam_membership", "iam_role_assignment", "iam_external_account")],
        migrations.RunSQL(HEAD_OFFICE_SQL, HEAD_OFFICE_REVERSE),
        rls.custom("iam_tenant", rls.TENANT_PRED),
        rls.custom("iam_policy_version", rls.TENANT_PRED),
        *[rls.enable(t, company_scoped=False) for t in TENANT_ONLY],
        *[rls.enable(t, company_scoped=True) for t in COMPANY_SCOPED],
    ]
