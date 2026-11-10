"""Row-level security on every ledger table (ADR-003, reference policy kernel.db.rls_policy_sql).

Rows are visible only inside a bound tenant context (``kernel.tenancy.bind``) and only
for the context's companies; an unbound session sees nothing. P04 may replace the
policies with its final ones (same predicate on tenant_id/company_id).
"""

from django.db import migrations

from kernel.db import rls_policy_sql

TABLES = (
    "ledger_account",
    "ledger_account_role",
    "ledger_account_determination",
    "ledger_book",
    "ledger_journal",
    "ledger_period",
    "ledger_exchange_rate",
    "ledger_dimension_node",
    "ledger_dimension_closure",
    "ledger_dimension_settings",
    "ledger_dimension_rule_set",
    "ledger_dimension_rule",
    "ledger_journal_entry",
    "ledger_journal_line",
    "ledger_balance",
)

_pairs = [rls_policy_sql(t) for t in TABLES]


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_db_guards")]

    operations = [
        migrations.RunSQL("\n".join(f for f, _ in _pairs), "\n".join(r for _, r in reversed(_pairs))),
    ]
