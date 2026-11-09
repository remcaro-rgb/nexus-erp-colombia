"""Row-level security for every workflow table with a ``tenant_id`` (ADR-003, FIX-CI-W02; CCR P07-2 §5).

ENABLE + FORCE RLS; unbound sessions see no rows. ``workflow_audit_event`` is tenant scoped (one hash
chain per tenant; company visibility is filtered by the service). Cross-tenant work (scheduler leader,
webhook deliverer) binds each tenant's context before touching its rows.
"""

from django.db import migrations

from iam import rls
from kernel.db import _ident

COMPANY_SCOPED = [
    "workflow_formula",
    "workflow_formula_table",
    "workflow_formula_evaluation",
    "workflow_approval_policy",
    "workflow_approval_request",
    "workflow_approval_delegation",
    "workflow_scheduled_job",
    "workflow_notification",
    "workflow_attachment",
    "workflow_webhook_subscription",
    "workflow_dsr_request",
]
TENANT_ONLY = [
    "workflow_audit_anchor",
    "workflow_approval_assignment",
    "workflow_approval_decision",
    "workflow_job_run",
    "workflow_notification_preference",
    "workflow_webhook_delivery",
]

#: Cross-tenant platform jobs (``kernel.tenancy.bind_platform``) → the tables each one may see across tenants.
PLATFORM = {
    "workflow_scheduler": ["workflow_scheduled_job", "workflow_job_run"],
    "workflow_webhooks": ["workflow_webhook_subscription", "workflow_webhook_delivery"],
    "workflow_audit": ["workflow_audit_event", "workflow_audit_anchor"],
}


def _platform_policy(table: str, job: str) -> migrations.RunSQL:
    t = _ident(table)
    name = _ident(f"{t}_platform_{job}"[:63])
    pred = f"current_setting('app.platform_job', true) = '{job}'"
    return migrations.RunSQL(
        f"DROP POLICY IF EXISTS {name} ON {t};\nCREATE POLICY {name} ON {t} USING ({pred}) WITH CHECK ({pred});",
        f"DROP POLICY IF EXISTS {name} ON {t};",
    )


class Migration(migrations.Migration):
    dependencies = [("workflow", "0002_append_only"), ("iam", "0002_security")]

    operations = [
        *[rls.enable(t) for t in COMPANY_SCOPED],
        *[rls.enable(t, company_scoped=False) for t in TENANT_ONLY],
        rls.enable("workflow_audit_event", company_scoped=False),  # one hash chain per tenant (CCR P07-2 §5)
        *[_platform_policy(t, job) for job, tables in PLATFORM.items() for t in tables],
    ]
