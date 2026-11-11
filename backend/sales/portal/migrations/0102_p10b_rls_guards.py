"""Forced row-level security (tenant + company) on the customer-portal tables (ADR-003, C1-5)."""

from django.db import migrations

from iam import rls

TABLES = ["sales_portal_request", "sales_portal_payment_intent"]


class Migration(migrations.Migration):
    dependencies = [("sales", "0101_p10b_portal")]

    operations = [rls.enable(t) for t in TABLES]
