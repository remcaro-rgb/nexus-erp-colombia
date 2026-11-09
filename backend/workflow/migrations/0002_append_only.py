"""Append-only guards: audit events and formula evaluation snapshots (C1-5)."""

from django.db import migrations

from kernel.db import immutable_trigger


class Migration(migrations.Migration):
    dependencies = [("workflow", "0001_initial")]

    operations = [
        immutable_trigger("workflow_audit_event", posted_column=None),
        immutable_trigger("workflow_formula_evaluation", posted_column=None),
        immutable_trigger("workflow_audit_anchor", posted_column=None),
    ]
