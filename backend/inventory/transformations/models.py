"""Transformation engine models (F-INV-XFORM, P09c). Imported by ``inventory.models`` (app label ``inventory``).

* ``TransformationOrder`` — one document (number ``TRF-…``, sequence ``TRF``) of type PRESENTATION (repack/unpack),
  PORTIONING (with or without template), MANUAL or PRODUCTION (from a BOM). Status follows the frozen contract:
  DRAFT → [SUBMITTED (pending approval)] → APPROVED → POSTED → REVERSED; DRAFT/SUBMITTED/APPROVED → CANCELLED.
  ``inputs_reserved`` records the ``:reserve`` step (RESERVED in FEATURES-ADDENDUM).
* ``TransformationLine`` — role INPUT (consumed), OUTPUT (produced; ``is_main`` takes the rounding remainder,
  ``is_byproduct`` valued by cost share) or WASTE (not stock; normal waste is absorbed by the outputs, abnormal
  waste is expensed). Quantities in the item's base UoM; ``unit_cost``/``value`` are frozen at post.
* ``PortioningTemplate`` (+ outputs) — expected yield % and cost share % per output, waste %, tolerance % (D-015).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from inventory.bom.models import Bom
from inventory.models import Item, Location, Lot, Presentation, Status, Warehouse
from kernel.fields import AmountField, PctField, QtyField, UnitCostField
from kernel.models import AuditedModel, CompanyModel


class PortioningTemplate(CompanyModel, AuditedModel):
    class Rounding(models.TextChoices):
        MAIN_OUTPUT = "MAIN_OUTPUT"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    input_item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    tolerance_percent = PctField(default=2)  # D-015
    waste_percent = PctField(default=0)  # expected (normal) waste of the input
    rounding_rule = models.CharField(max_length=16, choices=Rounding.choices, default=Rounding.MAIN_OUTPUT)
    notes = models.CharField(max_length=2000, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_portioning_template"
        constraints = [
            models.UniqueConstraint(Lower("code"), "tenant_id", "company_id", name="inventory_portion_tpl_code_uq"),
            models.CheckConstraint(
                condition=Q(tolerance_percent__gte=0) & Q(tolerance_percent__lte=100),
                name="inventory_portion_tpl_tol_ck",
            ),
            models.CheckConstraint(
                condition=Q(waste_percent__gte=0) & Q(waste_percent__lt=100), name="inventory_portion_tpl_waste_ck"
            ),
        ]


class PortioningTemplateOutput(CompanyModel):
    template = models.ForeignKey(PortioningTemplate, on_delete=models.CASCADE, related_name="outputs")
    line_no = models.PositiveIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    expected_yield_percent = PctField()
    cost_share_percent = PctField(null=True, blank=True)
    is_main = models.BooleanField(default=False)

    class Meta:
        db_table = "inventory_portioning_template_output"
        constraints = [
            models.UniqueConstraint(fields=["template", "line_no"], name="inventory_portion_out_uq"),
            models.CheckConstraint(
                condition=Q(expected_yield_percent__gt=0) & Q(expected_yield_percent__lte=100),
                name="inventory_portion_out_yield_ck",
            ),
        ]


class TransformationOrder(CompanyModel, AuditedModel):
    class Kind(models.TextChoices):
        PRESENTATION = "PRESENTATION"
        PORTIONING = "PORTIONING"
        MANUAL = "MANUAL"
        PRODUCTION = "PRODUCTION"

    class OrderStatus(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        POSTED = "POSTED"
        REVERSED = "REVERSED"
        CANCELLED = "CANCELLED"

    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"warehouse_ids": "warehouse_id"}

    number = models.CharField(max_length=40, blank=True, default="")
    transformation_type = models.CharField(max_length=14, choices=Kind.choices)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    template = models.ForeignKey(PortioningTemplate, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    bom = models.ForeignKey(Bom, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    planned_qty = QtyField(null=True, blank=True)  # PRODUCTION: good output to make
    document_date = models.DateField()
    posting_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    notes = models.CharField(max_length=10000, blank=True, default="")
    reason = models.CharField(max_length=500, blank=True, default="")
    additional_costs = AmountField(default=0)
    tolerance_percent = PctField(null=True, blank=True)
    inputs_reserved = models.BooleanField(default=False)
    approval_required = models.BooleanField(default=False)
    approval_reasons = models.CharField(max_length=2000, blank=True, default="")
    approval_note = models.CharField(max_length=500, blank=True, default="")
    approved_by = models.UUIDField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    value_in = AmountField(null=True, blank=True)
    value_out = AmountField(null=True, blank=True)
    waste_expensed = AmountField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    reversal_entry_id = models.UUIDField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_transformation"
        constraints = [
            models.CheckConstraint(condition=Q(additional_costs__gte=0), name="inventory_xform_addcost_ck"),
        ]
        indexes = [models.Index(fields=["tenant_id", "company_id", "status"], name="inventory_xform_status_ix")]


class TransformationLine(CompanyModel):
    class Role(models.TextChoices):
        INPUT = "INPUT"
        OUTPUT = "OUTPUT"
        WASTE = "WASTE"

    order = models.ForeignKey(TransformationOrder, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    role = models.CharField(max_length=8, choices=Role.choices)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    lot_code = models.CharField(max_length=60, blank=True, default="")  # new output lot
    quantity = QtyField()  # base UoM
    qty_packages = QtyField(null=True, blank=True)
    actual_weight = QtyField(null=True, blank=True)  # variable-weight outputs (base UoM of the input)
    cost_share = PctField(null=True, blank=True)
    is_main = models.BooleanField(default=False)
    is_byproduct = models.BooleanField(default=False)
    abnormal = models.BooleanField(default=False)  # WASTE: expensed instead of absorbed
    unit_cost = UnitCostField(null=True, blank=True)
    value = AmountField(null=True, blank=True)

    class Meta:
        db_table = "inventory_transformation_line"
        constraints = [
            models.UniqueConstraint(fields=["order", "line_no"], name="inventory_xform_line_uq"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_xform_line_qty_ck"),
            models.CheckConstraint(
                condition=Q(role="WASTE") | Q(item__isnull=False), name="inventory_xform_line_item_ck"
            ),
        ]
