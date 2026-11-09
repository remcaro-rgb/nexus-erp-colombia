"""Row-level security for the tenant table of localization_co (ADR-003, FIX-CI-W02).

``localization_co_holiday_override`` is company scoped; departments, municipalities and code-list
versions are platform reference data without ``tenant_id`` (readable by every tenant).
"""

from django.db import migrations

from iam import rls


class Migration(migrations.Migration):
    dependencies = [("localization_co", "0001_initial"), ("iam", "0002_security")]

    operations = [rls.enable("localization_co_holiday_override")]
