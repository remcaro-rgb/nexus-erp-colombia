"""Row-level security and immutability guards for every purchasing table (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* ``purchasing_quote_comparison`` is append-only: comparison snapshots never change.
* Receipts, vendor invoices and their lines are immutable once ``posted`` (corrections by reversal
  documents / credit notes, never by update or delete).
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = [
    "purchasing_supplier",
    "purchasing_settings",
    "purchasing_rfq",
    "purchasing_rfq_line",
    "purchasing_vendor_quote",
    "purchasing_vendor_quote_line",
    "purchasing_quote_attachment",
    "purchasing_quote_comparison",
    "purchasing_award",
    "purchasing_award_line",
    "purchasing_order",
    "purchasing_order_line",
    "purchasing_receipt",
    "purchasing_receipt_line",
    "purchasing_vendor_invoice",
    "purchasing_vendor_invoice_line",
    "purchasing_ap_open_item",
]
POSTED = ["purchasing_receipt", "purchasing_receipt_line", "purchasing_vendor_invoice", "purchasing_vendor_invoice_line"]


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        immutable_trigger("purchasing_quote_comparison", None),
        *[immutable_trigger(t) for t in POSTED],
    ]
