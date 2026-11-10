"""Row-level security (tenant + bound companies, ENABLE + FORCE) and the composite company FK on every
time keeping table (ADR-003)."""

from django.db import migrations

from iam import rls
from kernel.db import company_fk_sql

TABLES = [
    "timekeeping_shift",
    "timekeeping_clock_event",
    "timekeeping_absence",
    "timekeeping_overtime_request",
    "timekeeping_timesheet",
]


class Migration(migrations.Migration):
    dependencies = [("timekeeping", "0001_initial"), ("iam", "0002_security")]

    operations = [*[rls.enable(t) for t in TABLES], *[migrations.RunSQL(*company_fk_sql(t)) for t in TABLES]]
