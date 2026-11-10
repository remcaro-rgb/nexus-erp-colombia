"""Row-level security (ENABLE + FORCE, tenant + company predicate) on every CRM table (ADR-003)."""

from django.db import migrations

from iam import rls

TABLES = ["crm_pipeline", "crm_lead", "crm_contact", "crm_opportunity", "crm_activity"]


class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [rls.enable(t) for t in TABLES]
