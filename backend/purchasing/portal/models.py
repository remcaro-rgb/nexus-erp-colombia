"""F-PORTAL-SUP data model (FEATURES-ADDENDUM §20).

Every row carries ``company_id`` + ``party_id`` (the P10 party of the supplier; D-018 one external account = one
party in one company), so ``PolicyEngine.scope_queryset`` scopes portal reads in the repository layer.

* :class:`SupplierMasterData` — the vendor-master data the portal may change (bank account encrypted at rest,
  contacts, tax data); it changes only through an APPROVED :class:`SupplierProfileChange` (bank changes need two
  internal approvers, SoD). CCR P11b-3 proposes moving it into ``purchasing_supplier`` (P11a).
* :class:`ShipmentNotice` — advance shipment notice of a purchase order, visible to receiving.
* :class:`SupplierDocument` / :class:`SupplierInvoiceUpload` — uploads create items for internal review; the portal
  never posts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager

from kernel.fields import QtyField
from kernel.models import AuditedModel, CompanyModel


class SupplierMasterData(CompanyModel, AuditedModel):
    objects: ClassVar[models.Manager[SupplierMasterData]] = models.Manager()

    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    bank_account_enc = models.TextField(null=True, blank=True)
    bank_account_last4 = models.CharField(max_length=4, blank=True, default="")
    contacts = models.JSONField(default=dict, blank=True)
    tax_data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "purchasing_supplier_master_data"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "supplier_id"], name="purchasing_smd_uq"),
        ]


class SupplierProfileChange(CompanyModel, AuditedModel):
    objects: ClassVar[models.Manager[SupplierProfileChange]] = models.Manager()
    if TYPE_CHECKING:
        approvals: RelatedManager[ProfileChangeApproval]

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"

    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    external_account_id = models.UUIDField()
    #: requested values; ``bank_account`` is kept encrypted in ``bank_account_enc`` (never in this JSON)
    requested = models.JSONField(default=dict, blank=True)
    bank_account_enc = models.TextField(null=True, blank=True)
    bank_account_last4 = models.CharField(max_length=4, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "purchasing_supplier_profile_change"
        constraints: ClassVar = [
            # at most one pending change per supplier (portal_profile_change_pending)
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "supplier_id"],
                condition=models.Q(status="SUBMITTED"),
                name="purchasing_spc_one_pending_uq",
            ),
        ]


class ProfileChangeApproval(CompanyModel):
    objects: ClassVar[models.Manager[ProfileChangeApproval]] = models.Manager()

    change = models.ForeignKey(SupplierProfileChange, on_delete=models.CASCADE, related_name="approvals")
    approver_id = models.UUIDField()
    approve = models.BooleanField()
    reason = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField()

    class Meta:
        db_table = "purchasing_supplier_profile_change_approval"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["change", "approver_id"], name="purchasing_spc_approver_uq"),
        ]


class ShipmentNotice(CompanyModel, AuditedModel):
    objects: ClassVar[models.Manager[ShipmentNotice]] = models.Manager()
    if TYPE_CHECKING:
        lines: RelatedManager[ShipmentNoticeLine]

    class Status(models.TextChoices):
        ANNOUNCED = "ANNOUNCED"
        RECEIVED = "RECEIVED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=40)
    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    purchase_order_id = models.UUIDField()
    expected_date = models.DateField()
    tracking = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ANNOUNCED)

    class Meta:
        db_table = "purchasing_shipment_notice"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_asn_number_uq"),
        ]


class ShipmentNoticeLine(CompanyModel):
    objects: ClassVar[models.Manager[ShipmentNoticeLine]] = models.Manager()

    notice = models.ForeignKey(ShipmentNotice, on_delete=models.CASCADE, related_name="lines")
    party_id = models.UUIDField()
    order_line_no = models.PositiveIntegerField()
    quantity = QtyField()

    class Meta:
        db_table = "purchasing_shipment_notice_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["notice", "order_line_no"], name="purchasing_asn_line_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_asn_line_qty_ck"),
        ]


class SupplierDocument(CompanyModel, AuditedModel):
    objects: ClassVar[models.Manager[SupplierDocument]] = models.Manager()

    class Review(models.TextChoices):
        PENDING = "PENDING"
        ACCEPTED = "ACCEPTED"
        REJECTED = "REJECTED"

    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    document_type = models.CharField(max_length=40)  # RUT, CERTIFICATION, BANK_CERTIFICATE, OTHER
    file_name = models.CharField(max_length=255)
    expires_on = models.DateField(null=True, blank=True)
    review_status = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)

    class Meta:
        db_table = "purchasing_supplier_document"


class SupplierInvoiceUpload(CompanyModel, AuditedModel):
    """Electronic invoice uploaded by the supplier: a DRAFT item for internal ingestion (P11 received e-invoices)."""

    objects: ClassVar[models.Manager[SupplierInvoiceUpload]] = models.Manager()

    class Processing(models.TextChoices):
        RECEIVED = "RECEIVED"
        IN_REVIEW = "IN_REVIEW"
        REGISTERED = "REGISTERED"
        REJECTED = "REJECTED"

    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    number = models.CharField(max_length=60)
    xml_sha256 = models.CharField(max_length=64, blank=True, default="")
    xml_size = models.PositiveIntegerField(default=0)
    processing_status = models.CharField(max_length=12, choices=Processing.choices, default=Processing.RECEIVED)

    class Meta:
        db_table = "purchasing_supplier_invoice_upload"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "supplier_id", "number"], name="purchasing_siu_number_uq"
            ),
        ]


class PoAcknowledgement(CompanyModel):
    """Supplier answer to a SENT purchase order: accepted (→ P11a ACKNOWLEDGED) or changes requested."""

    objects: ClassVar[models.Manager[PoAcknowledgement]] = models.Manager()

    supplier_id = models.UUIDField()
    party_id = models.UUIDField()
    external_account_id = models.UUIDField()
    purchase_order_id = models.UUIDField()
    accepted = models.BooleanField()
    comment = models.TextField(blank=True, default="")
    answered_at = models.DateTimeField()

    class Meta:
        db_table = "purchasing_po_acknowledgement"
