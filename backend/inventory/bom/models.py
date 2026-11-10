"""Recipes / bills of materials (F-INV-BOM, P09c). Imported by ``inventory.models`` (app label ``inventory``).

* ``Bom`` is one **version** of a recipe family identified by ``code``: ``version_no`` grows per (company, code).
  ``bom_status`` DRAFT → ACTIVE → OBSOLETE; only DRAFT versions are editable or deletable.
* ``bom_type`` KIT (consumed at sale, P10) or PRODUCTION (consumed by a transformation order, F-INV-XFORM).
* Quantities are NUMERIC(20,6) in the item's base UoM, percents NUMERIC(9,6) (C2).
* Invariants (``inventory.bom.service``): no cycles across ACTIVE versions, at most one ACTIVE version per
  (output item, type) on any date (validity ranges never overlap), 0 < yield ≤ 100.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from inventory.models import Item, Presentation
from kernel.fields import PctField, QtyField
from kernel.models import AuditedModel, CompanyModel


class Bom(CompanyModel, AuditedModel):
    class BomType(models.TextChoices):
        KIT = "KIT"
        PRODUCTION = "PRODUCTION"

    class BomStatus(models.TextChoices):
        DRAFT = "DRAFT"
        ACTIVE = "ACTIVE"
        OBSOLETE = "OBSOLETE"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    bom_type = models.CharField(max_length=12, choices=BomType.choices, default=BomType.PRODUCTION)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    version_no = models.PositiveIntegerField(default=1)
    bom_status = models.CharField(max_length=10, choices=BomStatus.choices, default=BomStatus.DRAFT)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    output_qty = QtyField()  # nominal output of one batch (base UoM of the output item)
    yield_percent = PctField(default=100)  # expected good output = output_qty × yield %
    normal_waste_percent = PctField(default=0)
    notes = models.CharField(max_length=2000, blank=True, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "inventory_bom"
        constraints = [
            models.UniqueConstraint(
                Lower("code"), "tenant_id", "company_id", "version_no", name="inventory_bom_code_version_uq"
            ),
            models.CheckConstraint(condition=Q(output_qty__gt=0), name="inventory_bom_output_qty_ck"),
            models.CheckConstraint(
                condition=Q(yield_percent__gt=0) & Q(yield_percent__lte=100), name="inventory_bom_yield_ck"
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="inventory_bom_validity_ck",
            ),
        ]
        indexes = [models.Index(fields=["tenant_id", "company_id", "item", "bom_status"], name="inventory_bom_item_ix")]


class BomLine(CompanyModel):
    class Role(models.TextChoices):
        COMPONENT = "COMPONENT"
        BYPRODUCT = "BYPRODUCT"

    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.COMPONENT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    quantity = QtyField()  # per batch (output_qty), base UoM of the component
    scrap_percent = PctField(default=0)
    cost_share_percent = PctField(null=True, blank=True)  # byproducts: share of the batch cost (D-016)
    is_optional = models.BooleanField(default=False)
    substitute_group = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        db_table = "inventory_bom_line"
        constraints = [
            models.UniqueConstraint(fields=["bom", "line_no"], name="inventory_bom_line_uq"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_bom_line_qty_ck"),
            models.CheckConstraint(
                condition=Q(scrap_percent__gte=0) & Q(scrap_percent__lt=1000), name="inventory_bom_line_scrap_ck"
            ),
        ]
