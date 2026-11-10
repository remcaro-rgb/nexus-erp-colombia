"""Row-level security (tenant + bound companies, ENABLE + FORCE) on every HR table, the composite company FK
(ADR-003) and the append-only contract history (C1-5)."""

from django.db import migrations

from iam import rls
from kernel.db import company_fk_sql, immutable_trigger

TABLES = ["hr_org_unit", "hr_position", "hr_employee", "hr_contract", "hr_contract_history", "hr_data_update_request"]


class Migration(migrations.Migration):
    dependencies = [("hr", "0001_initial"), ("iam", "0002_security")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        *[migrations.RunSQL(*company_fk_sql(t)) for t in TABLES],
        immutable_trigger("hr_contract_history", posted_column=None),
    ]
