"""Kernel base models (abstract) and kernel tables.

Abstract bases for every module:

* :class:`TenantModel` — UUIDv7 ``id`` + ``tenant_id``.
* :class:`CompanyModel` — adds ``company_id``; the composite FK
  ``(tenant_id, company_id) → iam_company(tenant_id, id)`` is added by the
  module migration with :func:`kernel.db.company_fk_sql` once P04's table exists.
* :class:`AuditedModel` — created/updated by/at + ``version`` (optimistic lock, ETag).
* :class:`ImmutableModel` — ``posted`` flag; app guard here plus the DB trigger
  from :func:`kernel.db.immutable_trigger`.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, ClassVar

from django.db import models
from django.db.models import F

from kernel import clock, ids
from kernel.errors import ConcurrencyConflict, ImmutableRecord, PreconditionFailed, PreconditionRequired


class TenantModel(models.Model):
    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    tenant_id = models.UUIDField(db_index=True, editable=False)

    class Meta:
        abstract = True


class CompanyModel(TenantModel):
    company_id = models.UUIDField(db_index=True, editable=False)

    class Meta:
        abstract = True


class AuditedModel(models.Model):
    created_at = models.DateTimeField(default=clock.now, editable=False)
    created_by = models.UUIDField(null=True, editable=False)
    updated_at = models.DateTimeField(default=clock.now, editable=False)
    updated_by = models.UUIDField(null=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True

    @property
    def etag(self) -> str:
        raw = f"{self._meta.label}:{self.pk}:{self.version}".encode()
        return 'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"'

    def check_if_match(self, if_match: str | None) -> None:
        """Validate an ``If-Match`` header against the current ETag (428 / 412)."""
        if not if_match:
            raise PreconditionRequired()
        candidates = {c.strip() for c in if_match.split(",")}
        if "*" not in candidates and self.etag not in candidates:
            raise PreconditionFailed("the resource changed since it was read")

    def save_versioned(self, principal_id: uuid.UUID | None, update_fields: list[str]) -> None:
        """``UPDATE ... SET version = version + 1 WHERE id = %s AND version = %s``.

        Raises :class:`ConcurrencyConflict` if another transaction won.
        """
        expected = self.version
        now = clock.now()
        values: dict[str, Any] = {f: getattr(self, f) for f in update_fields}
        values.update(updated_at=now, updated_by=principal_id, version=F("version") + 1)
        manager = type(self)._default_manager
        changed = manager.filter(pk=self.pk, version=expected).update(**values)
        if changed != 1:
            raise ConcurrencyConflict("stale version; reload and retry")
        self.version = expected + 1
        self.updated_at = now
        self.updated_by = principal_id


class ImmutableModel(models.Model):
    """Rows become append-only once ``posted`` is true (C1-5)."""

    posted = models.BooleanField(default=False)

    IMMUTABLE_ALWAYS: ClassVar[bool] = False

    class Meta:
        abstract = True

    def _locked(self) -> bool:
        if self._state.adding:
            return False
        if self.IMMUTABLE_ALWAYS:
            return True
        stored = type(self)._default_manager.filter(pk=self.pk).values_list("posted", flat=True).first()
        return bool(stored)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._locked():
            raise ImmutableRecord(f"{self._meta.label} {self.pk} is posted")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        if self._locked() or self.posted:
            raise ImmutableRecord(f"{self._meta.label} {self.pk} is posted")
        return super().delete(*args, **kwargs)


# --------------------------------------------------------------------------- tables


class IdempotencyRecord(models.Model):
    """``kernel_idempotency(tenant_id, key, request_hash, response, status, created_at)``."""

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    tenant_id = models.UUIDField()
    key = models.CharField(max_length=255)
    scope = models.CharField(max_length=200, default="")
    request_hash = models.CharField(max_length=64)
    response = models.JSONField(null=True)
    status_code = models.PositiveSmallIntegerField(default=200)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETED)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "kernel_idempotency"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "key"], name="kernel_idem_tenant_key_uq")]


class OutboxMessage(models.Model):
    """Transactional outbox (ADR-006). Written inside the UoW, relayed after commit."""

    class Status(models.TextChoices):
        PENDING = "PENDING"
        PUBLISHED = "PUBLISHED"
        DEAD = "DEAD"

    id = models.UUIDField(primary_key=True, editable=False)  # = event_id
    tenant_id = models.UUIDField()
    company_id = models.UUIDField(null=True)
    event_type = models.CharField(max_length=200)
    queue = models.CharField(max_length=50, default="default")
    envelope = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=clock.now)
    available_at = models.DateTimeField(default=clock.now)
    published_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "kernel_outbox"
        indexes = [models.Index(fields=["status", "available_at", "created_at"], name="kernel_outbox_pending_ix")]


class InboxRecord(models.Model):
    """Consumer dedupe: ``kernel_inbox(consumer, event_id)``."""

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    consumer = models.CharField(max_length=200)
    event_id = models.UUIDField()
    processed_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "kernel_inbox"
        constraints = [models.UniqueConstraint(fields=["consumer", "event_id"], name="kernel_inbox_uq")]


class DeadLetter(models.Model):
    """Events that could not be published or consumed (replay UI: P07)."""

    class Stage(models.TextChoices):
        RELAY = "RELAY"
        CONSUMER = "CONSUMER"

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    stage = models.CharField(max_length=16, choices=Stage.choices)
    consumer = models.CharField(max_length=200, blank=True, default="")
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=200)
    tenant_id = models.UUIDField()
    envelope = models.JSONField()
    error = models.TextField()
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=clock.now)
    replayed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "kernel_dead_letter"


class Sequence(CompanyModel):
    """Gap-free numbering per ``(tenant, company, sequence_code, prefix)``.

    Fiscal sequences carry the DIAN numbering-resolution data (external input,
    entered by the tenant; never invented): range, validity, resolution number
    and technical key.
    """

    sequence_code = models.CharField(max_length=40)
    prefix = models.CharField(max_length=10, blank=True, default="")
    next_number = models.BigIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=0)
    is_fiscal = models.BooleanField(default=False)
    range_from = models.BigIntegerField(null=True, blank=True)
    range_to = models.BigIntegerField(null=True, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    resolution_number = models.CharField(max_length=60, blank=True, default="")
    technical_key = models.CharField(max_length=128, blank=True, default="")  # confidential
    alert_80_sent = models.BooleanField(default=False)
    alert_95_sent = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "kernel_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "sequence_code", "prefix"],
                name="kernel_sequence_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(range_from__isnull=True)
                | models.Q(range_to__isnull=True)
                | models.Q(range_from__lte=models.F("range_to")),
                name="kernel_sequence_range_ck",
            ),
        ]


class FeatureFlagOverride(models.Model):
    """Per-tenant / per-company override of a registered flag (code default otherwise)."""

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    tenant_id = models.UUIDField()
    company_id = models.UUIDField(null=True, blank=True)
    code = models.CharField(max_length=100)
    enabled = models.BooleanField()
    reason = models.CharField(max_length=500, blank=True, default="")
    updated_at = models.DateTimeField(default=clock.now)
    updated_by = models.UUIDField(null=True)

    class Meta:
        db_table = "kernel_feature_flag"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "code"],
                name="kernel_flag_scope_uq",
                nulls_distinct=False,
            )
        ]
