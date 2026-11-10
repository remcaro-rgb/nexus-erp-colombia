"""einvoice tables (owner P15). Every table is tenant + company scoped with forced RLS (0003_rls).

Evidence, transitions, transmission and delivery logs are append-only (DB trigger, 0002_guards):
fiscal evidence can never be updated or deleted (retention ≥ 10 years, legal hold).
Secrets are stored as references only (``*_ref`` columns, ``secret://einvoice/<name>``).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.fields import AmountField
from kernel.models import AuditedModel, CompanyModel


class TechnologyProvider(CompanyModel, AuditedModel):
    """``ProviderProfile`` of F-PT-OPEN (draft → tested → active → retired)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    class ConfigState(models.TextChoices):
        DRAFT = "DRAFT"
        TESTED = "TESTED"
        ACTIVE = "ACTIVE"
        RETIRED = "RETIRED"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    adapter = models.CharField(max_length=100)
    base_url = models.CharField(max_length=2000, blank=True, default="")
    environment = models.CharField(max_length=20, default="SANDBOX")
    credentials_ref = models.CharField(max_length=255, blank=True, default="")  # secret reference, never returned
    contract_version = models.CharField(max_length=100, default="generic-json-v1")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    config_state = models.CharField(max_length=16, choices=ConfigState.choices, default=ConfigState.DRAFT)
    last_test = models.JSONField(default=dict, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    tested_fingerprint = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "einvoice_provider"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="einvoice_provider_code_uq")
        ]


class EinvoiceSettings(CompanyModel, AuditedModel):
    class Mode(models.TextChoices):
        SANDBOX_SIMULATOR = "SANDBOX_SIMULATOR"
        PROVIDER_SANDBOX = "PROVIDER_SANDBOX"

    mode = models.CharField(max_length=30, choices=Mode.choices, default=Mode.SANDBOX_SIMULATOR)
    adapter = models.CharField(max_length=20, default="simulator")
    provider = models.ForeignKey(TechnologyProvider, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    software_id = models.CharField(max_length=255, blank=True, default="")
    software_pin_ref = models.CharField(max_length=255, blank=True, default="")
    environment = models.CharField(max_length=2, default="2")
    #: Provider switch waiting for a second administrator (F-PT-OPEN: switching requires approval).
    pending_provider = models.ForeignKey(
        TechnologyProvider, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    pending_requested_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "einvoice_settings"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id"], name="einvoice_settings_company_uq")]


class NumberingRange(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    code = models.CharField(max_length=40)
    family = models.CharField(max_length=30, default="FE")
    prefix = models.CharField(max_length=10, blank=True, default="")
    from_number = models.BigIntegerField()
    to_number = models.BigIntegerField()
    resolution = models.CharField(max_length=255)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField()
    technical_key_ref = models.CharField(max_length=255, blank=True, default="")
    last_used_number = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    source = models.CharField(max_length=10, default="MANUAL")  # MANUAL | SYNC
    synthetic = models.BooleanField(default=True)

    class Meta:
        db_table = "einvoice_numbering_range"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="einvoice_range_code_uq"),
            models.CheckConstraint(
                condition=models.Q(from_number__lte=models.F("to_number")), name="einvoice_range_ck"
            ),
        ]


class FiscalDocument(CompanyModel, AuditedModel):
    class State(models.TextChoices):
        PENDING_BUILD = "pending_build"
        BUILT = "built"
        SIGNED = "signed"
        SUBMITTED = "submitted"
        ACCEPTED = "accepted"
        ACCEPTED_WITH_NOTICES = "accepted_with_notices"
        REJECTED = "rejected"
        ERROR_RETRYABLE = "error_retryable"
        CONTINGENCY_ISSUED = "contingency_issued"
        TRANSMITTED_LATE = "transmitted_late"

    source_type = models.CharField(max_length=60)
    source_id = models.UUIDField()
    family = models.CharField(max_length=30)
    number = models.CharField(max_length=60)
    issue_date = models.DateField()
    unique_code = models.CharField(max_length=128)
    status = models.CharField(max_length=30, choices=State.choices, default=State.PENDING_BUILD)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    tracking_id = models.CharField(max_length=200, blank=True, default="")
    adapter_key = models.CharField(max_length=100, blank=True, default="")
    provider_code = models.CharField(max_length=40, blank=True, default="")
    contingency = models.ForeignKey("Contingency", null=True, blank=True, on_delete=models.PROTECT)
    model = models.JSONField()  # canonical FiscalDocumentModel (no secrets)
    test_set_id = models.CharField(max_length=255, blank=True, default="")
    messages = models.JSONField(default=list, blank=True)
    error_codes = models.JSONField(default=list, blank=True)
    last_error = models.CharField(max_length=1000, blank=True, default="")
    simulated = models.BooleanField(default=True)
    synthetic = models.BooleanField(default=True)
    payable = AmountField(default=0)
    accepted_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    FINAL: ClassVar[frozenset[str]] = frozenset({"accepted", "accepted_with_notices", "rejected"})

    class Meta:
        db_table = "einvoice_document"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "family", "number"], name="einvoice_doc_number_uq"
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "source_type", "source_id", "family"], name="einvoice_doc_source_uq"
            ),
        ]
        indexes = [models.Index(fields=["tenant_id", "company_id", "status"], name="einvoice_doc_status_ix")]


class FiscalTransition(CompanyModel):
    """Append-only lifecycle log (every transition, with the evidence produced by it)."""

    document = models.ForeignKey(FiscalDocument, on_delete=models.PROTECT, related_name="transitions")
    from_state = models.CharField(max_length=30, blank=True, default="")
    to_state = models.CharField(max_length=30)
    at = models.DateTimeField(default=clock.now)
    actor_id = models.UUIDField(null=True)
    attempt = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=1000, blank=True, default="")
    evidence = models.ForeignKey("FiscalEvidence", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        db_table = "einvoice_transition"
        ordering: ClassVar[list[str]] = ["at", "id"]


class FiscalEvidence(CompanyModel):
    """Encrypted request/response XML (append-only; retention ≥ 10 years, legal hold)."""

    class Kind(models.TextChoices):
        UNSIGNED_XML = "unsigned_xml"
        SIGNED_XML = "signed_xml"
        REQUEST = "request"
        RESPONSE = "response"
        ATTACHED_DOCUMENT = "attached_document"
        EVENT_XML = "event_xml"

    document = models.ForeignKey(FiscalDocument, null=True, on_delete=models.PROTECT, related_name="evidence")
    radian_event = models.ForeignKey("RadianEvent", null=True, on_delete=models.PROTECT, related_name="evidence")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    content = models.BinaryField()  # einvoice.crypto envelope
    created_at = models.DateTimeField(default=clock.now)
    retention_until = models.DateField()
    legal_hold = models.BooleanField(default=True)

    class Meta:
        db_table = "einvoice_evidence"


class TransmissionLog(CompanyModel):
    document = models.ForeignKey(FiscalDocument, null=True, on_delete=models.PROTECT, related_name="transmissions")
    radian_event = models.ForeignKey("RadianEvent", null=True, on_delete=models.PROTECT, related_name="transmissions")
    provider_code = models.CharField(max_length=40, blank=True, default="")
    adapter_key = models.CharField(max_length=100)
    attempt = models.PositiveIntegerField()
    outcome = models.CharField(max_length=30)
    latency_ms = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=1000, blank=True, default="")  # redacted
    at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "einvoice_transmission_log"


class Contingency(CompanyModel, AuditedModel):
    class Kind(models.TextChoices):
        PROVIDER_OUTAGE = "PROVIDER_OUTAGE"  # DIAN/PT unavailable → automatic
        ISSUER_OUTAGE = "ISSUER_OUTAGE"  # issuer-side → manual declaration

    kind = models.CharField(max_length=20, choices=Kind.choices)
    active = models.BooleanField(default=True)
    since = models.DateTimeField(default=clock.now)
    declared_by = models.UUIDField(null=True)
    automatic = models.BooleanField(default=False)
    reason = models.CharField(max_length=500, blank=True, default="")
    restored_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "einvoice_contingency"


class RadianEvent(CompanyModel, AuditedModel):
    class Direction(models.TextChoices):
        EMITTED = "EMITTED"
        RECEIVED = "RECEIVED"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        SUBMITTED = "SUBMITTED"
        ACCEPTED = "ACCEPTED"
        REJECTED = "REJECTED"
        RECORDED = "RECORDED"  # received event registered

    document_type = models.CharField(max_length=60)
    document_id = models.UUIDField()
    fiscal_document = models.ForeignKey(FiscalDocument, null=True, blank=True, on_delete=models.PROTECT)
    document_number = models.CharField(max_length=60, blank=True, default="")
    document_unique_code = models.CharField(max_length=128, blank=True, default="")
    event_code = models.CharField(max_length=10)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.EMITTED)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    number = models.CharField(max_length=60, blank=True, default="")
    unique_code = models.CharField(max_length=128, blank=True, default="")
    claim_concept = models.CharField(max_length=4, blank=True, default="")
    occurred_at = models.DateTimeField(default=clock.now)
    system_generated = models.BooleanField(default=False)
    tracking_id = models.CharField(max_length=200, blank=True, default="")
    messages = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "einvoice_radian_event"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "document_type", "document_id", "event_code", "direction"],
                name="einvoice_radian_once_uq",
            ),
        ]


class HabilitationTestSet(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        CREATED = "CREATED"
        PASSED = "PASSED"
        FAILED = "FAILED"

    test_set_id = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    results = models.JSONField(default=dict, blank=True)
    runs = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "einvoice_habilitation_test_set"


class SigningCertificate(CompanyModel, AuditedModel):
    """Certificate metadata only (EG-2). The key/PKCS#12 lives in the secret store (``key_ref``)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        EXPIRED = "EXPIRED"
        FIXTURE = "FIXTURE"

    subject = models.CharField(max_length=255)
    serial = models.CharField(max_length=100, blank=True, default="")
    thumbprint = models.CharField(max_length=64)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField()
    key_ref = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FIXTURE)
    alerts_sent = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "einvoice_certificate"


class GateRecord(CompanyModel):
    """External gate evidence (EG-1/EG-2/EG-3) recorded by an operator; append-only."""

    gate = models.CharField(max_length=10)
    reference = models.CharField(max_length=500)
    recorded_by = models.UUIDField()
    recorded_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "einvoice_gate_record"


class DeliveryLog(CompanyModel):
    """Delivery of the AttachedDocument ZIP to the buyer (append-only; bounces are new rows)."""

    document = models.ForeignKey(FiscalDocument, on_delete=models.PROTECT, related_name="deliveries")
    channel = models.CharField(max_length=20, default="email")
    recipient_masked = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=12)  # QUEUED | SENT | BOUNCED | SKIPPED
    attachment_sha256 = models.CharField(max_length=64, blank=True, default="")
    detail = models.CharField(max_length=500, blank=True, default="")
    at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "einvoice_delivery_log"
