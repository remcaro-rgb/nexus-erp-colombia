"""Data model of the customer portal (F-PORTAL-CUS, part P10b; app label ``sales``, tables ``sales_portal_*``).

* :class:`CustomerRequest` — what an external customer account asks for: a quote (``QUOTE``: items + quantities
  → an internal DRAFT quote, D-024 prices), a change of its master data (``PROFILE_CHANGE`` → internal approval)
  or a general message (``GENERAL``). Never priced or approved by the customer itself.
* :class:`PaymentIntent` — a payment the customer starts against its own open invoices through the SIMULATED
  provider (``FIXTURE_NOT_CERTIFIED``, D-021). A simulated success never posts: it only leaves a DRAFT recibo de
  caja (``sales.Receipt``) for treasury, which posts it manually.

Both carry ``tenant_id`` + ``company_id`` + ``party_id`` (the token's party, D-018/D-033) with forced RLS
(migration 0102); portal reads are additionally filtered by the policy engine's portal scope.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, ClassVar

from django.db import models

from kernel.fields import AmountField, CurrencyField
from kernel.models import AuditedModel, CompanyModel


class CustomerRequest(CompanyModel, AuditedModel):
    class Type(models.TextChoices):
        QUOTE = "QUOTE"
        PROFILE_CHANGE = "PROFILE_CHANGE"
        GENERAL = "GENERAL"

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED"  # waiting for the internal team
        QUOTED = "QUOTED"  # internal quote created (sent to the customer when SENT)
        ORDERED = "ORDERED"  # the customer accepted the quote → sales order
        APPROVED = "APPROVED"  # profile change applied by an internal approver
        REJECTED = "REJECTED"
        RESOLVED = "RESOLVED"  # general request answered

    objects: ClassVar[models.Manager[CustomerRequest]] = models.Manager()

    party = models.ForeignKey("sales.Party", on_delete=models.PROTECT, related_name="+")
    if TYPE_CHECKING:
        party_id: uuid.UUID
    external_account_id = models.UUIDField()
    number = models.CharField(max_length=60)
    request_type = models.CharField(max_length=20, choices=Type.choices)
    message = models.TextField(blank=True, default="")
    items = models.JSONField(default=list, blank=True)  # QUOTE: [{item_id, quantity, uom?}]
    changes = models.JSONField(default=dict, blank=True)  # PROFILE_CHANGE: allow-listed party fields
    request_status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    quote_id = models.UUIDField(null=True, blank=True)
    order_id = models.UUIDField(null=True, blank=True)
    resolution = models.TextField(blank=True, default="")
    decided_by = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_portal_request"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_portal_request_no_uq"),
        ]


class PaymentIntent(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        CREATED = "CREATED"
        SUCCEEDED_SIMULATED = "SUCCEEDED_SIMULATED"
        FAILED = "FAILED"
        CONFIRMED_BY_TREASURY = "CONFIRMED_BY_TREASURY"  # the RC draft was posted by treasury (derived on read)

    objects: ClassVar[models.Manager[PaymentIntent]] = models.Manager()

    party = models.ForeignKey("sales.Party", on_delete=models.PROTECT, related_name="+")
    if TYPE_CHECKING:
        party_id: uuid.UUID
    external_account_id = models.UUIDField()
    documents = models.JSONField(default=list)  # invoice ids of the customer (open AR items)
    amount = AmountField()
    currency = CurrencyField()
    provider = models.CharField(max_length=80, default="SIMULADO")
    provider_ref = models.CharField(max_length=80, blank=True, default="")
    intent_status = models.CharField(max_length=24, choices=Status.choices, default=Status.CREATED)
    failure_reason = models.CharField(max_length=200, blank=True, default="")
    receipt_id = models.UUIDField(null=True, blank=True)  # DRAFT recibo de caja left for treasury
    simulated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_portal_payment_intent"
        constraints: ClassVar = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sales_portal_intent_amount_ck"),
        ]


__all__ = ["CustomerRequest", "PaymentIntent"]
