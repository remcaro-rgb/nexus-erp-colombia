"""F-INV-BARCODE models (P09b): barcodes per owner, company GS1 prefix, label templates and print jobs.

Imported by ``inventory.models`` so they register under the ``inventory`` app label (P09a extension point).
Tables are tenant + company scoped with forced RLS (migration ``0101_p09b_issues_barcodes``).
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from kernel.models import AuditedModel, CompanyModel


class Barcode(CompanyModel, AuditedModel):
    """One scannable value of an item / presentation / location / lot. ``value`` is unique per company."""

    class Symbology(models.TextChoices):
        CODE128 = "CODE128"
        EAN13 = "EAN13"
        GTIN14_IMPORTED = "GTIN14_IMPORTED"
        QR = "QR"

    class OwnerType(models.TextChoices):
        ITEM = "item"
        PRESENTATION = "presentation"
        LOCATION = "location"
        LOT = "lot"

    class Source(models.TextChoices):
        GENERATED_INTERNAL = "GENERATED_INTERNAL"
        GENERATED_GS1_PREFIX = "GENERATED_GS1_PREFIX"
        IMPORTED = "IMPORTED"

    value = models.CharField(max_length=80)
    symbology = models.CharField(max_length=20, choices=Symbology.choices)
    owner_type = models.CharField(max_length=20, choices=OwnerType.choices)
    owner_id = models.UUIDField()
    item_id = models.UUIDField(null=True, blank=True)  # owning item (presentation/lot owners), for reports
    source = models.CharField(max_length=24, choices=Source.choices)
    gs1_prefix = models.CharField(max_length=11, blank=True, default="")
    gs1_ownership_claimed = models.BooleanField(default=False)
    status = models.CharField(max_length=16, default="ACTIVE")

    class Meta:
        db_table = "inventory_barcode"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "value"], name="inventory_barcode_value_uq"),
            models.CheckConstraint(
                condition=Q(gs1_ownership_claimed=False) | (Q(source="GENERATED_GS1_PREFIX") & ~Q(gs1_prefix="")),
                name="inventory_barcode_gs1_claim_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "company_id", "owner_type", "owner_id"], name="inventory_barcode_owner_ix"
            )
        ]


class Gs1Prefix(CompanyModel, AuditedModel):
    """The company GS1 prefix as entered by a user (never invented; D-004). Restricted 20–29 = internal use."""

    prefix = models.CharField(max_length=11)
    restricted_circulation = models.BooleanField(default=False)
    next_reference = models.BigIntegerField(default=1)

    class Meta:
        db_table = "inventory_gs1_prefix"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "prefix"], name="inventory_gs1_uq")]


class LabelTemplate(CompanyModel, AuditedModel):
    """Fixed-field label design (D-005). ``layout.fields`` ⊂ LABEL_FIELDS; cost fields never exist."""

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    symbology = models.CharField(max_length=10, default="CODE128")
    width_mm = models.PositiveSmallIntegerField(default=50)
    height_mm = models.PositiveSmallIntegerField(default=25)
    layout = models.JSONField(default=dict)
    copies = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, default="ACTIVE")

    class Meta:
        db_table = "inventory_label_template"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="inventory_label_tpl_code_uq"),
            models.CheckConstraint(
                condition=Q(width_mm__gte=10, width_mm__lte=300, height_mm__gte=10, height_mm__lte=300),
                name="inventory_label_tpl_size_ck",
            ),
        ]


class LabelPrintJob(CompanyModel, AuditedModel):
    """QUEUED → RENDERED (label data + hash) → DOWNLOADED (PDF served; browser print, no printer driver in dev)."""

    class JobStatus(models.TextChoices):
        QUEUED = "QUEUED"
        RENDERED = "RENDERED"
        DOWNLOADED = "DOWNLOADED"

    template = models.ForeignKey(LabelTemplate, on_delete=models.PROTECT, related_name="+")
    targets = models.JSONField(default=dict)
    copies = models.PositiveSmallIntegerField(default=1)
    labels = models.JSONField(default=list)  # rendered label data (no cost, ever)
    label_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=JobStatus.choices, default=JobStatus.QUEUED)
    data_hash = models.CharField(max_length=64, blank=True, default="")
    pdf_hash = models.CharField(max_length=64, blank=True, default="")
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "inventory_label_print_job"
        constraints = [
            models.CheckConstraint(condition=Q(copies__gte=1, copies__lte=500), name="inventory_label_copies_ck")
        ]


from inventory.barcodes.registry import register_fields as _register_fields  # noqa: E402

_register_fields()
