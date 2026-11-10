"""CRM data model (P10a). Non-posting (C1-3): nothing here touches the GL or stock.

Every table carries ``tenant_id`` + ``company_id`` with forced row-level security (migration 0002). Personal
data of leads/contacts is ``restricted-pii``; a lead converts into a customer party only with a consent record
reference (habeas data, Ley 1581 — consent records are kept by P04/P23).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models

from kernel.fields import AmountField, CurrencyField, PctField
from kernel.models import AuditedModel, CompanyModel


class Pipeline(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    #: [{"code": "PROSPECCION", "name": "Prospección", "probability": "10"}, …] in board order.
    stages = models.JSONField(default=list)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "crm_pipeline"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="crm_pipeline_code_uq"),
        ]


class Lead(CompanyModel, AuditedModel):
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"owner_ids": "owner_id"}

    class Status(models.TextChoices):
        NEW = "NEW"
        QUALIFIED = "QUALIFIED"
        CONVERTED = "CONVERTED"
        LOST = "LOST"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=250, blank=True, default="")
    nit = models.CharField(max_length=20, blank=True, default="")
    email = models.CharField(max_length=254, null=True, blank=True)
    phone = models.CharField(max_length=40, null=True, blank=True)
    source = models.CharField(max_length=60, blank=True, default="")
    campaign = models.CharField(max_length=120, blank=True, default="")
    owner_id = models.UUIDField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    consent_ref = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    party_id = models.UUIDField(null=True, blank=True)
    opportunity_id = models.UUIDField(null=True, blank=True)
    lost_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "crm_lead"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="crm_lead_code_uq"),
        ]


class Contact(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    email = models.CharField(max_length=254, null=True, blank=True)
    phone = models.CharField(max_length=40, null=True, blank=True)
    party_id = models.UUIDField(null=True, blank=True)
    position = models.CharField(max_length=120, null=True, blank=True)
    consent_ref = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "crm_contact"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="crm_contact_code_uq"),
        ]


class Opportunity(CompanyModel, AuditedModel):
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"owner_ids": "owner_id"}

    class Status(models.TextChoices):
        OPEN = "OPEN"
        WON = "WON"
        LOST = "LOST"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    party_id = models.UUIDField(null=True, blank=True)
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    pipeline = models.ForeignKey(Pipeline, on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=40)
    amount = AmountField(default=Decimal(0))
    currency = CurrencyField()
    probability = PctField(default=Decimal(0))
    close_date = models.DateField(null=True, blank=True)
    owner_id = models.UUIDField(null=True, blank=True)
    products = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)
    lost_reason = models.TextField(blank=True, default="")
    closed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "crm_opportunity"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="crm_opportunity_code_uq"),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="crm_opportunity_amount_ck"),
        ]


class Activity(CompanyModel, AuditedModel):
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"owner_ids": "owner_id"}

    class Type(models.TextChoices):
        CALL = "CALL"
        MEETING = "MEETING"
        EMAIL = "EMAIL"
        TASK = "TASK"
        NOTE = "NOTE"

    activity_type = models.CharField(max_length=10, choices=Type.choices)
    subject = models.CharField(max_length=250)
    body = models.TextField(blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    related = models.JSONField(null=True, blank=True)  # DocumentRef (crm.lead, crm.opportunity, sales.party, …)
    owner_id = models.UUIDField(null=True, blank=True)
    done = models.BooleanField(default=False)

    class Meta:
        db_table = "crm_activity"
