"""Append-only approval log (C1-5): no UPDATE/DELETE on tax_parameter_approval."""

from django.db import migrations

from kernel.db import immutable_trigger


class Migration(migrations.Migration):
    dependencies = [("tax", "0001_initial")]

    operations = [immutable_trigger("tax_parameter_approval", None)]
