"""HR tables (owner P13; this part P13a). Synthetic data only (DEV).

* :class:`OrgUnit` / :class:`Position` — organisation masters (company-scoped, ACTIVE | ARCHIVED).
* :class:`Employee` — personal data classified per Ley 1581 (contract ``x-classification``): contact data
  restricted-pii, health/pension entities sensitive-pii, bank account confidential and stored encrypted
  (:mod:`hr.crypto`); ``data_consent_at`` records the processing authorisation, ``biometric_consent_at`` the
  explicit biometric consent (R-DP-01).
* :class:`EmploymentContract` — one labour contract; ``APPROVED`` = in force, ``CANCELLED`` = terminated.
  Prestación de servicios is **not** payroll (``is_payroll`` false; routed to AP).
* :class:`ContractHistory` — append-only trail of salary changes, promotions and terminations.
* :class:`DataUpdateRequest` — employee portal data update (SUBMITTED → APPROVED | REJECTED by HR).

Money is Decimal NUMERIC(20,4) (``AmountField``).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.fields import AmountField, CurrencyField
from kernel.models import AuditedModel, CompanyModel

ACTIVE, ARCHIVED = "ACTIVE", "ARCHIVED"
MASTER_STATUSES = [(ACTIVE, ACTIVE), (ARCHIVED, ARCHIVED)]

CONTRACT_TYPES = ("INDEFINIDO", "FIJO", "OBRA_LABOR", "APRENDIZAJE", "PRESTACION_SERVICIOS")
PAYROLL_CONTRACT_TYPES = ("INDEFINIDO", "FIJO", "OBRA_LABOR", "APRENDIZAJE")
SALARY_TYPES = ("ORDINARIO", "INTEGRAL")
ARL_CLASSES = ("I", "II", "III", "IV", "V")
DOC_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "POSTED", "REVERSED", "CANCELLED")
REVIEW_STATUSES = ("SUBMITTED", "APPROVED", "REJECTED")


def _choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(v, v) for v in values]


class OrgUnit(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MASTER_STATUSES, default=ACTIVE)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="children")
    manager_employee_id = models.UUIDField(null=True)

    class Meta:
        db_table = "hr_org_unit"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="hr_org_unit_code_uniq")
        ]


class Position(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MASTER_STATUSES, default=ACTIVE)
    org_unit = models.ForeignKey(OrgUnit, on_delete=models.PROTECT, null=True, related_name="positions")

    class Meta:
        db_table = "hr_position"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="hr_position_code_uniq")
        ]


class Employee(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=MASTER_STATUSES, default=ACTIVE)
    full_name = models.CharField(max_length=200)
    document_type = models.CharField(max_length=10)
    document_number = models.CharField(max_length=20)
    check_digit = models.CharField(max_length=1, null=True)
    email = models.EmailField(max_length=254, null=True)
    phone = models.CharField(max_length=30, null=True)
    address = models.CharField(max_length=255, null=True)
    birth_date = models.DateField(null=True)
    health_entity = models.CharField(max_length=120, null=True)
    pension_entity = models.CharField(max_length=120, null=True)
    #: encrypted at rest (hr.crypto); never stored in clear text
    bank_account_enc = models.TextField(null=True)
    position = models.ForeignKey(Position, on_delete=models.PROTECT, null=True, related_name="employees")
    org_unit = models.ForeignKey(OrgUnit, on_delete=models.PROTECT, null=True, related_name="employees")
    data_consent_at = models.DateTimeField(null=True)
    biometric_consent_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "hr_employee"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="hr_employee_code_uniq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "document_type", "document_number"], name="hr_employee_doc_uniq"
            ),
        ]


class EmploymentContract(CompanyModel, AuditedModel):
    number = models.CharField(max_length=40)
    document_date = models.DateField()
    posting_date = models.DateField(null=True)
    status = models.CharField(max_length=10, choices=_choices(DOC_STATUSES), default="APPROVED")
    currency = CurrencyField(default="COP")
    notes = models.TextField(blank=True, default="")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="contracts")
    contract_type = models.CharField(max_length=24, choices=_choices(CONTRACT_TYPES))
    salary_type = models.CharField(max_length=10, choices=_choices(SALARY_TYPES), default="ORDINARIO")
    salary = AmountField()
    start_date = models.DateField()
    end_date = models.DateField(null=True)
    position = models.ForeignKey(Position, on_delete=models.PROTECT, null=True, related_name="+")
    cost_center_id = models.UUIDField(null=True)
    arl_risk_class = models.CharField(max_length=3, choices=_choices(ARL_CLASSES), default="I")
    rest_day = models.PositiveSmallIntegerField(default=6)  # ISO weekday - 1 (6 = Sunday; Ley 2466 art. 14 par. 3)
    exonerated = models.BooleanField(default=False)  # ET 114-1 employer exoneration (payroll input)
    termination_date = models.DateField(null=True)
    termination_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "hr_contract"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="hr_contract_number_uniq")
        ]

    @property
    def is_payroll(self) -> bool:
        return self.contract_type in PAYROLL_CONTRACT_TYPES


class ContractHistory(CompanyModel):
    contract = models.ForeignKey(EmploymentContract, on_delete=models.PROTECT, related_name="history")
    change = models.CharField(max_length=12)  # created | amended | terminated
    effective_date = models.DateField()
    changes = models.JSONField(default=dict)
    reason = models.CharField(max_length=255, blank=True, default="")
    at = models.DateTimeField(default=clock.now)
    by = models.UUIDField(null=True)

    class Meta:
        db_table = "hr_contract_history"


class DataUpdateRequest(CompanyModel, AuditedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="data_updates")
    changes = models.JSONField(default=dict)
    review_status = models.CharField(max_length=10, choices=_choices(REVIEW_STATUSES), default="SUBMITTED")
    reviewed_by = models.UUIDField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    review_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "hr_data_update_request"
