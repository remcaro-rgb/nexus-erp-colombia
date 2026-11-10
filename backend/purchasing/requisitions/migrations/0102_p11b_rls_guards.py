"""Row-level security and guards for the P11b tables (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* Sourcing decisions and profile-change approvals are append-only (an audit trail of who decided what).
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = [
    "purchasing_requisition_settings",
    "purchasing_internal_requisition",
    "purchasing_internal_requisition_line",
    "purchasing_sourcing_decision",
    "purchasing_consolidation",
    "purchasing_consolidation_line",
    "purchasing_buyer_task",
    "purchasing_purchase_requisition",
    "purchasing_purchase_requisition_line",
    "purchasing_supplier_master_data",
    "purchasing_supplier_profile_change",
    "purchasing_supplier_profile_change_approval",
    "purchasing_shipment_notice",
    "purchasing_shipment_notice_line",
    "purchasing_supplier_document",
    "purchasing_supplier_invoice_upload",
    "purchasing_po_acknowledgement",
]
APPEND_ONLY = [
    "purchasing_sourcing_decision",
    "purchasing_supplier_profile_change_approval",
    "purchasing_po_acknowledgement",
]


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0101_p11b_requisitions_portal")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        *[immutable_trigger(t, None) for t in APPEND_ONLY],
    ]
