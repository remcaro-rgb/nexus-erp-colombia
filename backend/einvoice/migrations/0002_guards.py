"""Append-only guards (C1-5): fiscal evidence, transitions, transmission/delivery logs and gate records.

Fiscal evidence can never be updated or deleted (retention ≥ 10 years with legal hold, T-P15-13);
corrections are new documents (notes) and new rows.
"""

from django.db import migrations

from kernel.db import immutable_trigger

APPEND_ONLY = [
    "einvoice_evidence",
    "einvoice_transition",
    "einvoice_transmission_log",
    "einvoice_delivery_log",
    "einvoice_gate_record",
]


class Migration(migrations.Migration):
    dependencies = [("einvoice", "0001_initial")]

    operations = [immutable_trigger(t, posted_column=None) for t in APPEND_ONLY]
