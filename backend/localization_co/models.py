"""Localization reference data (owner P06).

* :class:`Department` / :class:`Municipality` — DANE DIVIPOLA, platform-wide
  reference data (no tenant), imported from a DANE file
  (``manage.py import_divipola``); the repository ships only a small sample.
* :class:`CodeListVersion` — DIAN annex code lists as versioned data (P15/P06).
* :class:`HolidayOverride` — per-company admin override of the Ley 51/1983 calendar.
"""

from __future__ import annotations

from django.db import models

from kernel import clock, ids
from kernel.models import AuditedModel, CompanyModel


class Department(models.Model):
    code = models.CharField(primary_key=True, max_length=2)
    name = models.CharField(max_length=200)

    class Meta:
        db_table = "localization_co_department"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class Municipality(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    code = models.CharField(max_length=5, unique=True)  # DIVIPOLA: 2 department + 3 municipality digits
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="municipalities")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    source = models.CharField(max_length=120, default="")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=clock.now, editable=False)
    updated_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "localization_co_municipality"
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=models.Q(code__regex=r"^[0-9]{5}$"), name="loc_municipality_code_5d"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class CodeListVersion(models.Model):
    """One version of a DIAN annex code list (e.g. ``dian.document_type``)."""

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    list_code = models.CharField(max_length=80)
    version = models.CharField(max_length=40)
    annex_version = models.CharField(max_length=40, blank=True, default="")
    entries = models.JSONField(default=list)  # [{"code": "...", "name": "...", "internal": "..."}]
    synthetic = models.BooleanField(default=True)
    verified = models.BooleanField(default=False)
    source_ref = models.CharField(max_length=120, blank=True, default="")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=clock.now, editable=False)

    class Meta:
        db_table = "localization_co_code_list_version"
        constraints = [models.UniqueConstraint(fields=["list_code", "version"], name="loc_code_list_version_uq")]


class HolidayOverride(CompanyModel, AuditedModel):
    date = models.DateField()
    add = models.BooleanField(default=True)
    name = models.CharField(max_length=200, blank=True, default="")
    reason = models.CharField(max_length=2000)

    class Meta:
        db_table = "localization_co_holiday_override"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "date"], name="loc_holiday_override_uq"),
        ]
