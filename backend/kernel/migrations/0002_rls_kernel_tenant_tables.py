"""Row-level security on the kernel's tenant tables (ADR-003; CCR P01-5 / P04-4 item 1).

Every table with a ``tenant_id`` column has RLS ENABLED and FORCED (DR-drill invariant
``rls_forced_on_tenant_tables``, T-P03-03). Policies follow the reference
``kernel.db.rls_policy_sql`` (the same SQL ``iam.rls.enable`` generates):

* ``kernel_sequence``      tenant + company (``app.company_ids``)
* ``kernel_idempotency``   tenant
* ``kernel_feature_flag``  tenant (``company_id`` is nullable: tenant-wide overrides)
* ``kernel_outbox``, ``kernel_dead_letter``  tenant, plus the platform policy for the
  cross-tenant relay/dead-letter worker (``kernel.tenancy.bind_platform("outbox")``).

``kernel_inbox`` has no ``tenant_id`` (consumer dedupe only). Unbound sessions see no rows.
"""

from django.db import migrations

from kernel.db import platform_rls_policy_sql, rls_policy_sql

_PAIRS = [
    rls_policy_sql("kernel_sequence", company_scoped=True),
    rls_policy_sql("kernel_idempotency", company_scoped=False),
    rls_policy_sql("kernel_feature_flag", company_scoped=False),
    platform_rls_policy_sql("kernel_outbox", "outbox"),
    platform_rls_policy_sql("kernel_dead_letter", "outbox"),
]


class Migration(migrations.Migration):
    dependencies = [("kernel", "0001_initial")]

    operations = [migrations.RunSQL(fwd, rev) for fwd, rev in _PAIRS]
