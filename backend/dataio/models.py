"""dataio persistence: import jobs (+ committed batches), export jobs and mapping profiles.

Every table is company-scoped (``CompanyModel``) with forced RLS (migration 0002). The uploaded rows live in the
job (JSON) for the dev scale this module targets (``formats.MAX_ROWS``); the committed batches give the
``(job_id, batch_no)`` idempotency that makes a crashed commit resumable without duplicates.
"""

from __future__ import annotations

from django.db import models

from kernel.models import AuditedModel, CompanyModel


class ImportStatus(models.TextChoices):
    UPLOADED = "UPLOADED"
    MAPPED = "MAPPED"
    VALIDATED = "VALIDATED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ImportMode(models.TextChoices):
    CREATE_ONLY = "create_only"
    UPDATE_ONLY = "update_only"
    UPSERT = "upsert"


class ImportJob(CompanyModel, AuditedModel):
    entity = models.CharField(max_length=60)
    mode = models.CharField(max_length=20, choices=ImportMode.choices, default=ImportMode.UPSERT)
    import_status = models.CharField(max_length=20, choices=ImportStatus.choices, default=ImportStatus.UPLOADED)
    filename = models.CharField(max_length=255, default="", blank=True)
    file_format = models.CharField(max_length=10, default="", blank=True)
    file_hash = models.CharField(max_length=64, default="", blank=True)
    batch_hash = models.CharField(max_length=64, default="", blank=True)
    decimal_comma = models.BooleanField(default=False)
    batch_size = models.PositiveIntegerField(default=0)  # 0 = all rows in one atomic batch
    headers = models.JSONField(default=list, blank=True)
    rows = models.JSONField(default=list, blank=True)
    mapping = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    preview = models.JSONField(default=list, blank=True)
    results = models.JSONField(default=list, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    applied_rows = models.PositiveIntegerField(default=0)
    batches_total = models.PositiveIntegerField(default=0)
    batches_done = models.PositiveIntegerField(default=0)
    #: the commit created or updated rows (an all-unchanged re-import is a no-op, not a duplicate)
    effective = models.BooleanField(default=False)
    committed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dataio_import_job"
        indexes = [models.Index(fields=["tenant_id", "company_id", "import_status"], name="dataio_job_status_ix")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "entity", "batch_hash"],
                condition=models.Q(import_status="COMMITTED", effective=True),
                name="dataio_job_batch_hash_uq",
            )
        ]


class ImportBatch(CompanyModel):
    job = models.ForeignKey(ImportJob, on_delete=models.PROTECT, related_name="batches")
    batch_no = models.PositiveIntegerField()
    rows = models.PositiveIntegerField(default=0)
    counts = models.JSONField(default=dict)
    committed_at = models.DateTimeField()

    class Meta:
        db_table = "dataio_import_batch"
        constraints = [models.UniqueConstraint(fields=["job", "batch_no"], name="dataio_batch_job_no_uq")]


class ExportJob(CompanyModel, AuditedModel):
    source = models.CharField(max_length=60)
    format = models.CharField(max_length=10)
    export_status = models.CharField(max_length=20, default="QUEUED")
    row_count = models.PositiveIntegerField(default=0)
    filename = models.CharField(max_length=255, default="", blank=True)
    content_type = models.CharField(max_length=120, default="", blank=True)
    content = models.BinaryField(null=True, blank=True)
    omitted_columns = models.JSONField(default=list, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dataio_export_job"
        indexes = [models.Index(fields=["tenant_id", "company_id", "export_status"], name="dataio_export_status_ix")]


class MappingProfile(CompanyModel, AuditedModel):
    name = models.CharField(max_length=200)
    entity = models.CharField(max_length=60)
    mapping = models.JSONField(default=dict)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "dataio_mapping_profile"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "entity", "name"], name="dataio_profile_name_uq")
        ]
