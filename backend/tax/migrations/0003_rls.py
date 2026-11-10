"""Row-level security for every tax table with a ``tenant_id`` (ADR-003, FIX-CI-W02; CCR P06-4 classes).

ENABLE + FORCE RLS; unbound sessions see no rows. Company rows need the company bound;
tenant-wide rows (``company_id IS NULL``) are visible to the tenant; platform register rows of
``tax_source`` (``tenant_id IS NULL``) are readable by every tenant and never writable by one.
"""

from django.db import migrations

from iam import rls

COMPANY_SCOPED = ["tax_tax_code", "tax_withholding_concept", "tax_ica_tariff", "tax_party_tax_profile", "tax_calendar"]
NULLABLE_COMPANY = ["tax_parameter", "tax_exogena_format_pack"]
TENANT_ONLY = ["tax_parameter_approval"]


def _source_rls() -> migrations.RunSQL:
    t = "tax_source"
    forward = (
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;\n"
        f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t};\n"
        f"CREATE POLICY {t}_tenant_isolation ON {t} USING (tenant_id IS NULL OR {rls.TENANT_PRED}) "
        f"WITH CHECK ({rls.TENANT_PRED});"
    )
    reverse = (
        f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t};\n"
        f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;"
    )
    return migrations.RunSQL(forward, reverse)


class Migration(migrations.Migration):
    dependencies = [("tax", "0002_append_only_logs"), ("iam", "0002_security")]

    operations = [
        *[rls.enable(t) for t in COMPANY_SCOPED],
        *[rls.custom(t, f"{rls.TENANT_PRED} AND (company_id IS NULL OR {rls.company_pred()})") for t in NULLABLE_COMPANY],
        *[rls.enable(t, company_scoped=False) for t in TENANT_ONLY],
        _source_rls(),
    ]
