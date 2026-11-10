"""Row-level security for every einvoice table (ADR-003): ENABLE + FORCE RLS, tenant + company scoped."""

from django.db import migrations

from iam import rls

COMPANY_SCOPED = [
    "einvoice_provider",
    "einvoice_settings",
    "einvoice_numbering_range",
    "einvoice_document",
    "einvoice_transition",
    "einvoice_evidence",
    "einvoice_transmission_log",
    "einvoice_contingency",
    "einvoice_radian_event",
    "einvoice_habilitation_test_set",
    "einvoice_certificate",
    "einvoice_gate_record",
    "einvoice_delivery_log",
]


class Migration(migrations.Migration):
    dependencies = [("einvoice", "0002_guards"), ("iam", "0002_security")]

    operations = [rls.enable(t) for t in COMPANY_SCOPED]
