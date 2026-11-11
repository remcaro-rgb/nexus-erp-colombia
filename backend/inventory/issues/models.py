"""F-INV-ISSUE models (P09b): issue concepts and stock issue documents (salidas de inventario por concepto).

``StockIssue`` is immutable once POSTED (corrections by ``:reverse``, never by edit or delete); its stock
moves, valuation and journal entry are written in the same transaction as the status change.
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q

from kernel.fields import AmountField, QtyField, UnitCostField
from kernel.models import AuditedModel, CompanyModel


class IssueConcept(CompanyModel, AuditedModel):
    class Kind(models.TextChoices):
        CONSUMPTION = "CONSUMPTION"
        DIRECT_CONSUMPTION = "DIRECT_CONSUMPTION"
        SCRAP = "SCRAP"
        SAMPLE = "SAMPLE"
        DONATION = "DONATION"
        INTERNAL_USE = "INTERNAL_USE"
        OTHER = "OTHER"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    account_role = models.CharField(max_length=60)  # debit role (account determination), credit is INVENTORY
    requires_dimension = models.BooleanField(default=False)  # requires a cost center (contract name)
    required_dims = models.JSONField(default=list)  # extra dimension keys (kernel.dimensions) required on lines
    requires_reason = models.BooleanField(default=False)
    reason_codes = models.JSONField(default=list)  # allowed reason codes (BAJA); empty = free text
    requires_approval = models.BooleanField(default=False)
    approval_threshold_value = AmountField(null=True, blank=True)  # value ≥ threshold → workflow approval
    allow_negative = models.BooleanField(default=False)  # always false (D-032), kept for the data model
    status = models.CharField(max_length=16, default="ACTIVE")

    class Meta:
        db_table = "inventory_issue_concept"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="inventory_issue_concept_code_uq"),
            models.CheckConstraint(condition=Q(allow_negative=False), name="inventory_issue_concept_no_negative_ck"),
            models.CheckConstraint(
                condition=Q(approval_threshold_value__isnull=True) | Q(approval_threshold_value__gte=0),
                name="inventory_issue_concept_threshold_ck",
            ),
        ]


class StockIssue(CompanyModel, AuditedModel):
    class IssueStatus(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"  # pending approval (FEATURES-ADDENDUM PENDING_APPROVAL)
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        POSTED = "POSTED"
        REVERSED = "REVERSED"
        CANCELLED = "CANCELLED"

    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"warehouse_ids": "warehouse_id", "branch_ids": "branch_id"}

    number = models.CharField(max_length=40, blank=True, default="")
    document_date = models.DateField()
    posting_date = models.DateField(null=True, blank=True)
    branch_id = models.UUIDField()
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    concept = models.ForeignKey(IssueConcept, on_delete=models.PROTECT, related_name="+")
    requested_by = models.UUIDField(null=True, blank=True)
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=IssueStatus.choices, default=IssueStatus.DRAFT)
    approval_id = models.UUIDField(null=True, blank=True)
    total = AmountField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    reversal_entry_id = models.UUIDField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=2000, blank=True, default="")
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_stock_issue"
        indexes = [models.Index(fields=["tenant_id", "company_id", "document_date"], name="inventory_issue_date_ix")]


class StockIssueLine(CompanyModel):
    issue = models.ForeignKey(StockIssue, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    presentation = models.ForeignKey(
        "inventory.Presentation", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    qty_packages = QtyField(null=True, blank=True)
    quantity = QtyField()  # base UoM (D-013)
    uom = models.CharField(max_length=20)
    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    unit_cost = UnitCostField(null=True, blank=True)
    value = AmountField(null=True, blank=True)

    class Meta:
        db_table = "inventory_stock_issue_line"
        constraints = [
            models.UniqueConstraint(fields=["issue", "line_no"], name="inventory_issue_line_uq"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_issue_line_qty_ck"),
        ]


from inventory.issues.registry import register_fields as _register_fields  # noqa: E402

_register_fields()
