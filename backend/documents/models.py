"""Documents data model (P17a): company templates with immutable versions, generated documents, render batches.

Every table carries ``tenant_id`` + ``company_id`` with forced row-level security (migration 0002). Template
versions and generated documents are append-only (DB trigger): a template change creates a new version row, and a
generated document is stored once with its SHA-256 and linked to its source record (P17 "stored as attachments with
a hash and linked to the source record").
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.models import AuditedModel, CompanyModel


class Status(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class DocumentTemplate(CompanyModel, AuditedModel):
    """A company template (overrides the built-in template of the same ``code`` for that company)."""

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    document_type = models.CharField(max_length=255)
    engine = models.CharField(max_length=20, default="weasyprint")
    body = models.TextField()
    locale = models.CharField(max_length=20, default="es-CO")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "documents_template"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="documents_template_code_uq"),
        ]


class DocumentTemplateVersion(CompanyModel):
    """Immutable snapshot of a template body per version (history; the render records the version used)."""

    template = models.ForeignKey(DocumentTemplate, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    body = models.TextField()
    body_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=clock.now)
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "documents_template_version"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["template", "version"], name="documents_template_version_uq"),
        ]


class GeneratedDocument(CompanyModel):
    """A rendered document stored once (append-only) with its hash and the link to its source record."""

    template_code = models.CharField(max_length=80)
    template_version = models.CharField(max_length=40)
    media_type = models.CharField(max_length=60)
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    source_number = models.CharField(max_length=60, blank=True, default="")
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    content = models.BinaryField()
    watermark = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=clock.now)
    created_by = models.UUIDField()

    class Meta:
        db_table = "documents_generated"
        indexes: ClassVar = [models.Index(fields=["company_id", "source_type", "source_id"])]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "source_type", "source_id", "template_code", "sha256"],
                name="documents_generated_source_hash_uq",
            ),
        ]


class RenderBatch(CompanyModel, AuditedModel):
    """``POST /render:render-batch`` operation (202): rendered by the worker task, polled as an Operation."""

    class State(models.TextChoices):
        QUEUED = "QUEUED"
        RUNNING = "RUNNING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    status = models.CharField(max_length=10, choices=State.choices, default=State.QUEUED)
    requests = models.JSONField(default=list)
    document_ids = models.JSONField(default=list)
    error = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents_render_batch"
