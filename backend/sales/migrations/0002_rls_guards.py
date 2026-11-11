"""Row-level security and immutability guards for every sales table (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* Deliveries, invoices, credit/debit notes, receipts, write-offs and their lines are immutable once
  ``posted`` (corrections by notes / reversal documents, never by update or delete).
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = [
    "sales_party",
    "sales_settings",
    "sales_discount_limit",
    "sales_item_price",
    "sales_price_list",
    "sales_price_list_line",
    "sales_price_formula",
    "sales_quote",
    "sales_quote_line",
    "sales_order",
    "sales_order_line",
    "sales_delivery",
    "sales_delivery_line",
    "sales_invoice",
    "sales_invoice_line",
    "sales_note",
    "sales_note_line",
    "sales_receipt",
    "sales_ar_open_item",
    "sales_ar_application",
    "sales_write_off",
    "sales_return",
]
POSTED = [
    "sales_delivery",
    "sales_delivery_line",
    "sales_invoice",
    "sales_invoice_line",
    "sales_note",
    "sales_note_line",
    "sales_receipt",
    "sales_write_off",
]


class Migration(migrations.Migration):
    dependencies = [("sales", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        *[immutable_trigger(t) for t in POSTED],
    ]
