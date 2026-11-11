"""Forced row-level security on every dataio table (ADR-003, reference policy ``kernel.db.rls_policy_sql``)."""

from django.db import migrations

from kernel.db import rls_policy_sql

TABLES = ("dataio_import_job", "dataio_import_batch", "dataio_export_job", "dataio_mapping_profile")

_rls = [rls_policy_sql(t) for t in TABLES]


class Migration(migrations.Migration):
    dependencies = [("dataio", "0001_initial")]

    operations = [
        migrations.RunSQL("\n".join(f for f, _ in _rls), "\n".join(r for _, r in reversed(_rls))),
    ]
