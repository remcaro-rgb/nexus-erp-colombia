"""Row-level security and append-only guards for the reporting tables (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* Report runs are immutable snapshots (``kernel_forbid_mutation`` without a posted column).
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = ["reporting_saved_report", "reporting_report_run"]


class Migration(migrations.Migration):
    dependencies = [("reporting", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        immutable_trigger("reporting_report_run", None),
    ]
