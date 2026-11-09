"""Tax module tables (owner P06).

The dated parameter store (C1-9): every regulatory value is a
:class:`Parameter` row with ``valid_from``/``valid_to``, a :class:`SourceRecord`,
a verification status and a legal-review stamp. Rows are tenant-scoped (seeded
from ``tax/fixtures/parameters_2026.yaml``); ``company_id`` set → company override;
``scope_ref`` → municipality (ICA) or other sub-scope.

New values enter as PROPOSED rows and become effective only after a second,
different person approves them (two-person rule, T-P06-09). Approved rows are
never edited; a correction is a new proposal that supersedes the old row.
"""

from __future__ import annotations

from django.db import models

from kernel import clock
from kernel.models import AuditedModel, CompanyModel, TenantModel
from kernel.parameters import ParameterStatus
from tax.exogena.models import ExogenaFormatPack

__all__ = [
    "ExogenaFormatPack",
    "IcaTariff",
    "Parameter",
    "ParameterApproval",
    "PartyTaxProfile",
    "SourceRecord",
    "TaxCalendar",
    "TaxCode",
    "WithholdingConcept",
]

PARAMETER_STATUS_CHOICES = [(s.value, s.value) for s in ParameterStatus]


class Lifecycle(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SourceRecord(TenantModel, AuditedModel):
    """Where a parameter value comes from (shared/05 register id + access evidence)."""

    tenant_id = models.UUIDField(db_index=True, null=True, editable=False)  # null = platform register entry
    register_id = models.CharField(max_length=20, blank=True, default="")  # e.g. S-08
    url = models.URLField(max_length=2000)
    title = models.CharField(max_length=255)
    accessed_at = models.DateTimeField()
    method = models.CharField(max_length=255, blank=True, default="")  # OPENED | SEARCH | SECONDARY | NOT_OPENED
    excerpt_hash = models.CharField(max_length=255, blank=True, default="")
    document_sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_source"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "register_id"],
                condition=~models.Q(register_id=""),
                name="tax_source_register_uq",
            )
        ]


class Parameter(TenantModel, AuditedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global"
        COMPANY = "company"
        MUNICIPALITY = "municipality"

    class Approval(models.TextChoices):
        PROPOSED = "PROPOSED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        SUPERSEDED = "SUPERSEDED"

    class ValueType(models.TextChoices):
        DECIMAL = "decimal"
        PERCENT = "percent"
        PERMIL = "permil"
        UVT = "uvt"
        TABLE = "table"
        JSON = "json"
        UNKNOWN = "unknown"

    company_id = models.UUIDField(null=True, blank=True, db_index=True)
    code = models.CharField(max_length=40)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.GLOBAL)
    scope_ref = models.CharField(max_length=40, blank=True, default="")
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    value = models.JSONField(default=dict)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    parameter_status = models.CharField(max_length=24, choices=PARAMETER_STATUS_CHOICES)
    source = models.ForeignKey(SourceRecord, on_delete=models.PROTECT, related_name="parameters")
    legal_history = models.JSONField(default=list, blank=True)
    legal_reviewed_by = models.UUIDField(null=True, blank=True)
    legal_reviewed_at = models.DateTimeField(null=True, blank=True)
    synthetic = models.BooleanField(default=False)
    approval = models.CharField(max_length=16, choices=Approval.choices, default=Approval.PROPOSED)
    proposed_by = models.UUIDField(null=True, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    document_ref = models.CharField(max_length=255, blank=True, default="")  # uploaded official document (FileStore id)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_parameter"
        indexes = [models.Index(fields=["tenant_id", "code", "valid_from"], name="tax_param_lookup_ix")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=models.F("valid_from")),
                name="tax_param_valid_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(scope="company") | models.Q(company_id__isnull=False),
                name="tax_param_company_scope_has_company",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code}@{self.valid_from}"


class ParameterApproval(TenantModel):
    """Append-only approval log (who proposed, who approved, when, with which reason)."""

    parameter = models.ForeignKey(Parameter, on_delete=models.PROTECT, related_name="approvals")
    action = models.CharField(max_length=16)  # PROPOSE | APPROVE | REJECT
    principal_id = models.UUIDField()
    reason = models.CharField(max_length=2000, blank=True, default="")
    at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "tax_parameter_approval"


class TaxCode(CompanyModel, AuditedModel):
    class TaxType(models.TextChoices):
        IVA = "IVA"
        INC = "INC"
        RETEFUENTE = "RETEFUENTE"
        RETEIVA = "RETEIVA"
        RETEICA = "RETEICA"
        ICA = "ICA"
        AUTORRETENCION = "AUTORRETENCION"
        OTHER = "OTHER"

    class BaseRule(models.TextChoices):
        NET = "NET"  # line net amount
        TAX_AMOUNT = "TAX_AMOUNT"  # e.g. ReteIVA over the VAT amount
        PERCENT_OF_BASE = "PERCENT_OF_BASE"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    tax_type = models.CharField(max_length=16, choices=TaxType.choices)
    rate_parameter_code = models.CharField(max_length=40, blank=True, default="")
    base_rule = models.CharField(max_length=20, choices=BaseRule.choices, default=BaseRule.NET)
    base_tax_type = models.CharField(max_length=16, blank=True, default="")  # for TAX_AMOUNT: which tax
    base_percent_parameter_code = models.CharField(max_length=40, blank=True, default="")
    min_base_uvt_parameter_code = models.CharField(max_length=40, blank=True, default="")
    applicability = models.JSONField(default=list, blank=True)  # list of matrix rules (engine.matrix)
    account_role = models.CharField(max_length=255, blank=True, default="")
    dian_code = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_tax_code"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="tax_code_uq")]


class WithholdingConcept(CompanyModel, AuditedModel):
    """Withholding concept (compras, servicios, honorarios…): base in UVT + rate, both parameters."""

    class TaxType(models.TextChoices):
        RETEFUENTE = "RETEFUENTE"
        RETEIVA = "RETEIVA"
        RETEICA = "RETEICA"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    tax_type = models.CharField(max_length=16, choices=TaxType.choices)
    base_parameter_code = models.CharField(max_length=40, blank=True, default="")
    rate_parameter_code = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_withholding_concept"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="tax_wh_concept_uq")]


class IcaTariff(CompanyModel, AuditedModel):
    """ICA tariff by municipality (DANE code) and CIIU activity; per-mil rate as a parameter (B-13: tenant-loaded)."""

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    municipality_code = models.CharField(max_length=5)
    activity_code = models.CharField(max_length=10)
    rate_parameter_code = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_ica_tariff"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="tax_ica_code_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "municipality_code", "activity_code"], name="tax_ica_mun_act_uq"
            ),
        ]


class PartyTaxProfile(CompanyModel, AuditedModel):
    class PersonType(models.TextChoices):
        NATURAL = "NATURAL"
        JURIDICA = "JURIDICA"

    class Regime(models.TextChoices):
        RESPONSABLE_IVA = "RESPONSABLE_IVA"
        NO_RESPONSABLE_IVA = "NO_RESPONSABLE_IVA"
        SIMPLE = "SIMPLE"
        NO_CONTRIBUYENTE = "NO_CONTRIBUYENTE"

    party_id = models.UUIDField()
    party_kind = models.CharField(max_length=16, default="other")
    active = models.BooleanField(default=True)
    person_type = models.CharField(max_length=16, choices=PersonType.choices, default=PersonType.JURIDICA)
    regime = models.CharField(max_length=32, choices=Regime.choices, default=Regime.RESPONSABLE_IVA)
    gran_contribuyente = models.BooleanField(default=False)
    autorretenedor = models.BooleanField(default=False)
    withholding_agent = models.BooleanField(default=False)  # agente de retención (renta)
    vat_withholding_agent = models.BooleanField(default=False)  # agente de retención de IVA
    ica_municipalities = models.JSONField(default=list, blank=True)
    ciiu_codes = models.JSONField(default=list, blank=True)
    responsibilities = models.JSONField(default=list, blank=True)  # RUT codes (code list dian.fiscal_responsibility)
    income_tax_declarant = models.BooleanField(default=True)

    class Meta:
        db_table = "tax_party_tax_profile"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "party_id"], name="tax_party_profile_uq")
        ]


class TaxCalendar(CompanyModel, AuditedModel):
    """Due dates of one obligation for one year, keyed by NIT last digit(s) (yearly data import, R-TX-07)."""

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    obligation = models.CharField(max_length=255)
    year = models.PositiveSmallIntegerField()
    due_dates = models.JSONField(default=dict)  # {"periods": [{"period": "2026-01", "by_digit": {"0": "2026-02-10"}}]}
    synthetic = models.BooleanField(default=False)
    source_ref = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        db_table = "tax_calendar"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code", "year"], name="tax_cal_uq")]
