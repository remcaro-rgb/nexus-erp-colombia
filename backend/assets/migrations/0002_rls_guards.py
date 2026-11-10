"""Forced row-level security on every assets table (ADR-003, reference policy ``kernel.db.rls_policy_sql``) and
append-only guards (C1-5) on the register sub-ledger and the per-asset depreciation detail."""

from django.db import migrations

from kernel.db import immutable_trigger_sql, rls_policy_sql

TABLES = (
    "assets_book",
    "assets_category",
    "assets_category_book_default",
    "assets_asset",
    "assets_asset_book",
    "assets_register_entry",
    "assets_depreciation_run",
    "assets_depreciation_line",
    "assets_revaluation",
    "assets_impairment",
    "assets_transfer",
    "assets_disposal",
    "assets_physical_count",
)
APPEND_ONLY = ("assets_register_entry", "assets_depreciation_line")

_rls = [rls_policy_sql(t) for t in TABLES]
_guards = [immutable_trigger_sql(t, None) for t in APPEND_ONLY]


class Migration(migrations.Migration):
    dependencies = [("assets", "0001_initial")]

    operations = [
        migrations.RunSQL("\n".join(f for f, _ in _rls), "\n".join(r for _, r in reversed(_rls))),
        migrations.RunSQL("\n".join(f for f, _ in _guards), "\n".join(r for _, r in reversed(_guards))),
    ]
