"""``ExogenaFormatPack`` table (F-ACC-EXO data model, P06 part)."""

from __future__ import annotations

from django.db import models

from kernel.models import AuditedModel, TenantModel


class ExogenaFormatPack(TenantModel, AuditedModel):
    class Source(models.TextChoices):
        SYNTHETIC = "SYNTHETIC"
        OFFICIAL_IMPORTED = "OFFICIAL_IMPORTED"

    class LegalStatus(models.TextChoices):
        NOT_DIAN_VALID = "NOT_DIAN_VALID"
        PENDING_VERIFICATION = "PENDING_VERIFICATION"
        VERIFIED_BY_USER = "VERIFIED_BY_USER"

    class Lifecycle(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    company_id = models.UUIDField(null=True, blank=True)  # null = available to every company of the tenant
    code = models.CharField(max_length=40)  # pack_code
    format_code = models.CharField(max_length=40)
    year = models.PositiveSmallIntegerField()
    pack_version = models.CharField(max_length=40)
    source = models.CharField(max_length=24, choices=Source.choices)
    legal_status = models.CharField(max_length=24, choices=LegalStatus.choices)
    pack = models.JSONField()
    checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_exogena_format_pack"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "code", "year", "pack_version"], name="tax_exogena_pack_version_uq"
            ),
            models.CheckConstraint(
                condition=~models.Q(source="SYNTHETIC") | models.Q(legal_status="NOT_DIAN_VALID"),
                name="tax_exogena_synthetic_not_valid",
            ),
        ]

    @property
    def synthetic(self) -> bool:
        return self.source == self.Source.SYNTHETIC

    @property
    def verified(self) -> bool:
        return self.legal_status == self.LegalStatus.VERIFIED_BY_USER

    @property
    def label(self) -> str:
        from tax.exogena.schema import SYNTHETIC_LABEL

        if self.synthetic:
            return SYNTHETIC_LABEL
        return "Verificado por el usuario" if self.verified else "Pendiente de verificación"
