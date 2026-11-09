"""Persistence of the workflow module (tables ``workflow_*``).

All rows carry ``tenant_id`` (and ``company_id`` when company-scoped); RLS
policies follow ``kernel.db.rls_policy_sql`` (P04 owns the final policies).
``AuditEvent`` and ``FormulaEvaluation`` are append-only (DB trigger).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.fields import AmountField
from kernel.models import AuditedModel, CompanyModel, TenantModel

# --------------------------------------------------------------------------- formulas


class Formula(CompanyModel, AuditedModel):
    DRAFT, PENDING_APPROVAL, ACTIVE, RETIRED = "DRAFT", "PENDING_APPROVAL", "ACTIVE", "RETIRED"
    STATUSES: ClassVar = [(s, s) for s in (DRAFT, PENDING_APPROVAL, ACTIVE, RETIRED)]

    domain = models.CharField(max_length=20)  # approval | pricing | payroll
    code = models.CharField(max_length=40)
    formula_version = models.PositiveIntegerField(default=1)
    description = models.CharField(max_length=255, blank=True, default="")
    expression = models.TextField()
    inputs = models.JSONField(default=dict)
    result_type = models.CharField(max_length=20)
    output_places = models.PositiveSmallIntegerField(null=True)
    output_rounding = models.CharField(max_length=12, default="HALF_EVEN")
    depends_on = models.JSONField(default=list)
    test_cases = models.JSONField(default=list)
    consumers = models.JSONField(default=list)
    effective_from = models.DateField(null=True)
    effective_to = models.DateField(null=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=DRAFT)
    author_id = models.UUIDField()
    submitted_by = models.UUIDField(null=True)
    approved_by = models.UUIDField(null=True)
    approved_at = models.DateTimeField(null=True)
    retired_at = models.DateTimeField(null=True)
    hash = models.CharField(max_length=64, blank=True, default="")
    last_test_run = models.JSONField(default=dict)

    class Meta:
        db_table = "workflow_formula"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "code", "formula_version"], name="wf_formula_code_ver"
            )
        ]
        indexes: ClassVar = [models.Index(fields=["tenant_id", "company_id", "domain", "status"])]


class FormulaTable(CompanyModel):
    """Versioned lookup table for ``lookup('name', key)`` (data, never SQL)."""

    name = models.CharField(max_length=63)
    version = models.PositiveIntegerField(default=1)
    key_type = models.CharField(max_length=20, default="string")
    value_type = models.CharField(max_length=20, default="decimal")
    rows = models.JSONField(default=dict)  # key -> decimal/str as JSON strings
    effective_from = models.DateField(null=True)
    effective_to = models.DateField(null=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "workflow_formula_table"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "name", "version"], name="wf_ftable_name_ver")
        ]


class FormulaEvaluation(CompanyModel):
    """Explain snapshot stored by callers with their document line (append-only)."""

    formula = models.ForeignKey(Formula, on_delete=models.PROTECT, related_name="evaluations")
    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    hash = models.CharField(max_length=64)
    document_type = models.CharField(max_length=60, blank=True, default="")
    document_id = models.UUIDField(null=True)
    snapshot = models.JSONField()
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "workflow_formula_evaluation"


# --------------------------------------------------------------------------- approvals


class ApprovalPolicy(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    document_type = models.CharField(max_length=60)
    action = models.CharField(max_length=30, default="post")
    priority = models.IntegerField(default=100)
    min_amount = AmountField(null=True)
    max_amount = AmountField(null=True)
    party_ids = models.JSONField(default=list)
    cost_center_ids = models.JSONField(default=list)
    condition = models.TextField(blank=True, default="")  # formula DSL (domain approval), boolean
    condition_inputs = models.JSONField(default=dict)
    steps = models.JSONField(default=list)  # [{"kind": role|user|manager, "ref": str, "quorum": int, "parallel": bool}]
    escalation_hours = models.PositiveIntegerField(null=True)
    escalate_to = models.JSONField(default=dict)  # {"kind": "user"|"role", "ref": ...}
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "workflow_approval_policy"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="wf_approval_policy_code")
        ]


class ApprovalRequest(CompanyModel, AuditedModel):
    PENDING, APPROVED, REJECTED, CANCELLED = "PENDING", "APPROVED", "REJECTED", "CANCELLED"

    policy = models.ForeignKey(ApprovalPolicy, on_delete=models.PROTECT, related_name="requests")
    document_type = models.CharField(max_length=60)
    document_id = models.UUIDField()
    document_number = models.CharField(max_length=60, blank=True, default="")
    action = models.CharField(max_length=30, default="post")
    amount = AmountField(null=True)
    context = models.JSONField(default=dict)
    requested_by = models.UUIDField()
    reason = models.TextField(blank=True, default="")
    state = models.CharField(max_length=12, default=PENDING)
    current_step = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(null=True)
    escalated_at = models.DateTimeField(null=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "workflow_approval_request"
        indexes: ClassVar = [
            models.Index(fields=["tenant_id", "company_id", "state"]),
            models.Index(fields=["tenant_id", "document_type", "document_id"]),
        ]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "document_type", "document_id", "action", "policy"],
                condition=models.Q(state="PENDING"),
                name="wf_one_pending_request",
            )
        ]


class ApprovalAssignment(TenantModel):
    """Who may decide a step (resolved at request/escalation time)."""

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="assignments")
    step = models.PositiveSmallIntegerField()
    principal_id = models.UUIDField(null=True)
    role = models.CharField(max_length=60, blank=True, default="")
    escalation = models.BooleanField(default=False)

    class Meta:
        db_table = "workflow_approval_assignment"


class ApprovalDecision(TenantModel):
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="decisions")
    step = models.PositiveSmallIntegerField()
    decider_id = models.UUIDField()
    on_behalf_of = models.UUIDField(null=True)  # delegation
    approve = models.BooleanField()
    reason = models.TextField(blank=True, default="")
    at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "workflow_approval_decision"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["request", "step", "decider_id"], name="wf_one_decision_per_step")
        ]


class ApprovalDelegation(CompanyModel):
    delegator_id = models.UUIDField()
    delegate_id = models.UUIDField()
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "workflow_approval_delegation"


# --------------------------------------------------------------------------- audit


class AuditEvent(TenantModel):
    """Hash-chained, append-only audit log (UPDATE/DELETE blocked by trigger)."""

    seq = models.BigIntegerField()
    company_id = models.UUIDField(null=True, db_index=True)
    at = models.DateTimeField()
    principal_id = models.UUIDField()
    principal_type = models.CharField(max_length=20)
    actor_id = models.UUIDField(null=True)  # on_behalf_of (agent/client acting for the principal)
    channel = models.CharField(max_length=12)
    action = models.CharField(max_length=80)
    resource_type = models.CharField(max_length=80)
    resource_id = models.UUIDField(null=True)
    changes = models.JSONField(default=dict)
    request_id = models.CharField(max_length=64, blank=True, default="")
    trace_id = models.CharField(max_length=64, blank=True, default="")
    ip = models.CharField(max_length=45, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    retention_class = models.CharField(max_length=20, default="financial")
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "workflow_audit_event"
        constraints: ClassVar = [models.UniqueConstraint(fields=["tenant_id", "seq"], name="wf_audit_seq")]
        indexes: ClassVar = [
            models.Index(fields=["tenant_id", "resource_type", "resource_id"]),
            models.Index(fields=["tenant_id", "principal_id", "at"]),
        ]


class AuditAnchor(TenantModel):
    """Daily anchor of the chain head, also written to object storage (retention lock)."""

    day = models.DateField()
    last_seq = models.BigIntegerField()
    head_hash = models.CharField(max_length=64)
    anchor_hash = models.CharField(max_length=64)
    object_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "workflow_audit_anchor"
        constraints: ClassVar = [models.UniqueConstraint(fields=["tenant_id", "day"], name="wf_audit_anchor_day")]


# --------------------------------------------------------------------------- scheduler


class ScheduledJob(CompanyModel, AuditedModel):
    ACTIVE, PAUSED, CANCELLED, FAILED, DONE = "ACTIVE", "PAUSED", "CANCELLED", "FAILED", "DONE"

    code = models.CharField(max_length=60)
    job_type = models.CharField(max_length=60)
    owner_id = models.UUIDField()
    owner_type = models.CharField(max_length=20, default="user")
    cron = models.CharField(max_length=100, blank=True, default="")
    run_at = models.DateTimeField(null=True)  # one-off
    timezone = models.CharField(max_length=64, default="America/Bogota")
    params = models.JSONField(default=dict)
    grant_ref = models.CharField(max_length=80, blank=True, default="")  # delegated grant of AI-created jobs
    created_by_agent = models.BooleanField(default=False)
    state = models.CharField(max_length=12, default=ACTIVE)
    next_run_at = models.DateTimeField(null=True, db_index=True)
    last_run_at = models.DateTimeField(null=True)
    last_result = models.JSONField(default=dict)

    class Meta:
        db_table = "workflow_scheduled_job"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="wf_scheduled_job_code")
        ]


class JobRun(TenantModel):
    job = models.ForeignKey(ScheduledJob, on_delete=models.CASCADE, related_name="runs")
    scheduled_for = models.DateTimeField()
    idempotency_key = models.CharField(max_length=120, unique=True)  # job_id:scheduled_time
    state = models.CharField(max_length=12, default="ENQUEUED")  # ENQUEUED RUNNING SUCCEEDED FAILED
    attempts = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "workflow_job_run"


# --------------------------------------------------------------------------- notifications


class Notification(CompanyModel):
    recipient_id = models.UUIDField(db_index=True)
    template_code = models.CharField(max_length=80)
    channel = models.CharField(max_length=12, default="in_app")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict)
    link = models.CharField(max_length=255, blank=True, default="")
    permission = models.CharField(max_length=120, blank=True, default="")  # needed to see it (policy-filtered)
    created_at = models.DateTimeField(default=clock.now)
    delivered_at = models.DateTimeField(null=True)
    read_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "workflow_notification"
        indexes: ClassVar = [models.Index(fields=["tenant_id", "company_id", "recipient_id", "read_at"])]


class NotificationPreference(TenantModel):
    principal_id = models.UUIDField()
    template_code = models.CharField(max_length=80)  # "*" = default
    channels = models.JSONField(default=list)
    digest = models.CharField(max_length=10, default="none")  # none | daily

    class Meta:
        db_table = "workflow_notification_preference"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "principal_id", "template_code"], name="wf_notif_pref")
        ]


# --------------------------------------------------------------------------- attachments


class Attachment(CompanyModel):
    resource_type = models.CharField(max_length=80)
    resource_id = models.UUIDField()
    name = models.CharField(max_length=200)
    object_key = models.CharField(max_length=400)
    media_type = models.CharField(max_length=120)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(max_length=12, default="PENDING")  # PENDING CLEAN INFECTED SKIPPED
    legal_hold = models.BooleanField(default=False)
    retention_until = models.DateField(null=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(default=clock.now)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "workflow_attachment"
        indexes: ClassVar = [models.Index(fields=["tenant_id", "resource_type", "resource_id"])]


# --------------------------------------------------------------------------- webhooks


class WebhookSubscription(CompanyModel, AuditedModel):
    url = models.CharField(max_length=500)
    event_types = models.JSONField(default=list)
    secret = models.CharField(max_length=128)  # x-classification: secret — never serialized
    service_account_id = models.UUIDField()
    active = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "workflow_webhook_subscription"


class WebhookDelivery(TenantModel):
    PENDING, DELIVERED, RETRYING, DEAD = "PENDING", "DELIVERED", "RETRYING", "DEAD"

    subscription = models.ForeignKey(WebhookSubscription, on_delete=models.CASCADE, related_name="deliveries")
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=120)
    payload = models.JSONField()
    state = models.CharField(max_length=12, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    last_status = models.IntegerField(null=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(default=clock.now)
    delivered_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "workflow_webhook_delivery"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["subscription", "event_id"], name="wf_webhook_delivery_once")
        ]


# --------------------------------------------------------------------------- DSR (Ley 1581)


class DsrRequest(CompanyModel, AuditedModel):
    CONSULTA, RECLAMO = "consulta", "reclamo"

    kind = models.CharField(max_length=10)
    right = models.CharField(max_length=20, default="access")  # access rectification suppression revocation
    subject_type = models.CharField(max_length=20)  # employee | customer | supplier | contact
    subject_ref = models.CharField(max_length=80)
    requester_name = models.CharField(max_length=200)  # x-classification: personal
    requester_email = models.CharField(max_length=200, blank=True, default="")  # x-classification: personal
    description = models.TextField(blank=True, default="")
    received_on = models.DateField()
    identity_verified_at = models.DateTimeField(null=True)
    identity_method = models.CharField(max_length=60, blank=True, default="")
    state = models.CharField(max_length=20, default="RECEIVED")
    due_on = models.DateField()
    extended = models.BooleanField(default=False)
    extension_reason = models.TextField(blank=True, default="")
    max_due_on = models.DateField()
    response = models.TextField(blank=True, default="")
    denial_reason = models.TextField(blank=True, default="")
    export = models.JSONField(default=dict)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "workflow_dsr_request"
