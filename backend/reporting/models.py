"""Reporting data model (P17a core): saved reports (report builder) and immutable report runs.

``SavedReport.definition`` holds the semantic query (``entity, measures, dimensions, filters, sort, limit,
time_grain, compare_to, as_of``) plus presentation (``view``: table|pivot|bar|line, ``pivot``, ``formatting``) and
``share_roles``. A report is DRAFT until published (``published_at``), ARCHIVED at the end of its life. A run is
an immutable snapshot (rows + the data-version cut-off) executed with the *runner's current* permissions.
Forced row-level security on every table (migration 0002); runs are append-only.
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.models import AuditedModel, CompanyModel


class SavedReport(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    definition = models.JSONField(default=dict)
    schedule = models.CharField(max_length=255, null=True, blank=True)
    recipients = models.JSONField(default=list)
    owner_id = models.UUIDField()
    published_at = models.DateTimeField(null=True, blank=True)
    scheduled_job_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "reporting_saved_report"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="reporting_report_code_uq"),
        ]


class ReportRun(CompanyModel):
    class State(models.TextChoices):
        READY = "READY"
        FAILED = "FAILED"

    report = models.ForeignKey(SavedReport, on_delete=models.PROTECT, null=True, blank=True, related_name="runs")
    report_key = models.CharField(max_length=120)  # saved report code or semantic entity / standard report key
    state = models.CharField(max_length=10, choices=State.choices)
    trigger = models.CharField(max_length=12, default="ui")  # ui | api | scheduler | ai | mcp
    principal_id = models.UUIDField()
    query = models.JSONField(default=dict)
    result = models.JSONField(null=True, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    data_version = models.CharField(max_length=120, blank=True, default="")
    ledger_cutoff_seq = models.BigIntegerField(default=0)
    error = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(default=clock.now)
    finished_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "reporting_report_run"
        indexes: ClassVar = [models.Index(fields=["company_id", "started_at"])]
