"""F-INV-REQ data model (FEATURES-ADDENDUM §13): internal requisitions, sourcing, consolidation, buyer tasks and
purchase requisitions. Quantities are Decimal NUMERIC(20,6) in the item's base UoM (D-013); amounts NUMERIC(20,4).

Invariant (DB check + service): per source line ``qty_transfer + qty_procure + qty_cancelled <= base_qty`` and
``fulfilled_qty <= base_qty``; a consolidated line never exceeds the sum of its sources (D-031).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models
from django.db.models import F, Q

from kernel.fields import AmountField, CurrencyField, QtyField
from kernel.models import AuditedModel, CompanyModel

ZERO = Decimal(0)


class RequisitionSettings(CompanyModel, AuditedModel):
    """Per-company F-INV-REQ policy: D-011 automatic-PO limit and D-012 warehouse priority list."""

    auto_po_limit = AmountField(default=Decimal("5000000"))  # D-011 default COP 5,000,000 per line total
    warehouse_priority = models.JSONField(default=list, blank=True)  # [warehouse_id, …] (D-012)

    class Meta:
        db_table = "purchasing_requisition_settings"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id"], name="purchasing_req_settings_company_uq"),
        ]


class InternalRequisition(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        IN_PROGRESS = "IN_PROGRESS"
        FULFILLED = "FULFILLED"
        PARTIALLY_FULFILLED_CLOSED = "PARTIALLY_FULFILLED_CLOSED"
        CANCELLED = "CANCELLED"

    class Priority(models.TextChoices):
        LOW = "LOW"
        NORMAL = "NORMAL"
        HIGH = "HIGH"
        URGENT = "URGENT"

    number = models.CharField(max_length=40)
    document_date = models.DateField()
    requester_id = models.UUIDField()
    requesting_warehouse_id = models.UUIDField()
    needed_by = models.DateField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    currency = CurrencyField()
    notes = models.TextField(blank=True, default="")
    dimensions = models.JSONField(default=dict, blank=True)
    source = models.JSONField(null=True, blank=True)  # DocumentRef of an origin (reorder job, AI proposal, import)
    approval_request_id = models.UUIDField(null=True, blank=True)
    decided_by = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    #: P04 row scopes: requesters see their own requisitions (scope key ``owner``)
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"owner": "requester_id"}

    class Meta:
        db_table = "purchasing_internal_requisition"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_ireq_number_uq"),
        ]


class RequisitionLine(CompanyModel):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        RESERVED = "RESERVED"
        IN_TRANSFER = "IN_TRANSFER"
        TO_PROCURE = "TO_PROCURE"
        IN_PURCHASE = "IN_PURCHASE"
        FULFILLED = "FULFILLED"
        CANCELLED = "CANCELLED"

    requisition = models.ForeignKey(InternalRequisition, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField()
    presentation_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    quantity = QtyField()  # as requested (presentation units when a presentation is named)
    uom = models.CharField(max_length=20, default="UND")
    base_qty = QtyField()  # base UoM (D-013)
    needed_by = models.DateField()
    delivery_warehouse_id = models.UUIDField()
    unit_price = AmountField(null=True, blank=True)  # requester's estimate (not a vendor price)
    dimensions = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    qty_transfer = QtyField(default=ZERO)
    qty_procure = QtyField(default=ZERO)
    qty_cancelled = QtyField(default=ZERO)
    fulfilled_qty = QtyField(default=ZERO)
    #: every hop of this source line: [{"kind": transfer|consolidation|purchase_order|buyer_task, "id", "qty", …}]
    lineage = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "purchasing_internal_requisition_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["requisition", "line_no"], name="purchasing_ireq_line_uq"),
            models.CheckConstraint(condition=Q(base_qty__gt=0), name="purchasing_ireq_line_qty_ck"),
            models.CheckConstraint(
                condition=Q(qty_transfer__gte=0, qty_procure__gte=0, qty_cancelled__gte=0, fulfilled_qty__gte=0)
                & Q(base_qty__gte=F("qty_transfer") + F("qty_procure") + F("qty_cancelled"))
                & Q(base_qty__gte=F("fulfilled_qty")),
                name="purchasing_ireq_line_allocation_ck",
            ),
        ]

    @property
    def remaining(self) -> Decimal:
        return self.base_qty - self.qty_transfer - self.qty_procure - self.qty_cancelled


class SourcingDecision(CompanyModel):
    class Source(models.TextChoices):
        TRANSFER = "TRANSFER"
        PURCHASE = "PURCHASE"
        MIXED = "MIXED"

    class CreatedBy(models.TextChoices):
        USER = "user"
        SYSTEM_POLICY = "system-policy"

    line = models.ForeignKey(RequisitionLine, on_delete=models.CASCADE, related_name="decisions")
    source = models.CharField(max_length=10, choices=Source.choices)
    from_warehouse_id = models.UUIDField(null=True, blank=True)
    qty_transfer = QtyField(default=ZERO)
    qty_purchase = QtyField(default=ZERO)
    transfer_ids = models.JSONField(default=list, blank=True)
    reason = models.TextField(blank=True, default="")
    created_by_kind = models.CharField(max_length=20, choices=CreatedBy.choices, default=CreatedBy.SYSTEM_POLICY)
    policy_ref = models.CharField(max_length=40, blank=True, default="")
    decided_by = models.UUIDField()
    decided_at = models.DateTimeField()

    class Meta:
        db_table = "purchasing_sourcing_decision"


class ConsolidationBatch(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        APPLIED = "APPLIED"
        CLOSED = "CLOSED"

    number = models.CharField(max_length=40)
    rule = models.CharField(max_length=20, default="D-031")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.APPLIED)
    run_at = models.DateTimeField()

    class Meta:
        db_table = "purchasing_consolidation"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_consol_number_uq"),
        ]


class ConsolidationLine(CompanyModel):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        IN_PURCHASE = "IN_PURCHASE"
        BUYER_TASK = "BUYER_TASK"
        FULFILLED = "FULFILLED"

    batch = models.ForeignKey(ConsolidationBatch, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField()
    presentation_id = models.UUIDField(null=True, blank=True)
    needed_week = models.DateField()  # Monday of the needed-by week (D-031)
    delivery_warehouse_id = models.UUIDField()
    quantity = QtyField()  # base UoM; == Σ sources[].qty
    #: lineage [{"line_id", "requisition_id", "requisition_number", "line_no", "qty"}] (D-031)
    sources = models.JSONField(default=list)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    purchase_order_id = models.UUIDField(null=True, blank=True)
    purchase_order_line_no = models.PositiveIntegerField(null=True, blank=True)
    unit_price = AmountField(null=True, blank=True)  # D-011 valid vendor price used for the draft (COP)
    supplier_id = models.UUIDField(null=True, blank=True)
    fulfilled_qty = QtyField(default=ZERO)

    class Meta:
        db_table = "purchasing_consolidation_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["batch", "line_no"], name="purchasing_consol_line_uq"),
            models.CheckConstraint(
                condition=Q(quantity__gt=0) & Q(fulfilled_qty__gte=0) & Q(quantity__gte=F("fulfilled_qty")),
                name="purchasing_consol_line_qty_ck",
            ),
        ]


class BuyerTask(CompanyModel, AuditedModel):
    """Work item for a buyer when D-011 forbids an automatic draft (limit exceeded / no valid vendor price)."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        DONE = "DONE"
        CANCELLED = "CANCELLED"

    consolidation_line = models.ForeignKey(ConsolidationLine, on_delete=models.CASCADE, related_name="tasks")
    reason_code = models.CharField(max_length=40)  # auto_po_limit_exceeded | vendor_price_missing
    message = models.TextField()
    estimated_total = AmountField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "purchasing_buyer_task"


class PurchaseRequisition(CompanyModel, AuditedModel):
    """``/api/v1/purchasing/requisitions`` (P11 ``PurchaseRequisition``): manual purchase request → RFQ / PO."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        POSTED = "POSTED"  # converted to an RFQ or a purchase order
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=40)
    document_date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    requester_id = models.UUIDField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    currency = CurrencyField()
    notes = models.TextField(blank=True, default="")
    source = models.JSONField(null=True, blank=True)
    target = models.JSONField(null=True, blank=True)  # DocumentRef of the RFQ / purchase order created from it
    approval_request_id = models.UUIDField(null=True, blank=True)
    decided_by = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"owner": "requester_id"}

    class Meta:
        db_table = "purchasing_purchase_requisition"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_preq_number_uq"),
        ]


class PurchaseRequisitionLine(CompanyModel):
    requisition = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    quantity = QtyField()
    uom = models.CharField(max_length=20, default="UND")
    unit_price = AmountField(default=ZERO)
    tax_code = models.CharField(max_length=40, blank=True, default="")
    account_code = models.CharField(max_length=40, blank=True, default="")
    dimensions = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "purchasing_purchase_requisition_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["requisition", "line_no"], name="purchasing_preq_line_uq"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="purchasing_preq_line_qty_ck"),
        ]
