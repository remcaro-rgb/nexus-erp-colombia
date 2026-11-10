"""Payroll tables (owner P13; this part P13b: payroll engine and F-PAY-FORMULA).

* :class:`PayrollPeriod` — company payroll calendar (MONTHLY | BIWEEKLY | WEEKLY).
* :class:`PayrollConcept` — earning (DEVENGO), deduction (DEDUCCION), employer contribution (APORTE)
  or provision (PROVISION). A concept is either formula-based (``formula_id`` → an ACTIVE
  ``workflow.Formula`` of domain ``payroll``, F-FORMULA-ENGINE) or manual (amount from novelties).
  ``depends_on`` lists other concept codes; the company graph is a DAG (D-025, cycle rejected on save).
* :class:`PayrollNovelty` — novedades (quantity / amount per employee, concept and period).
* :class:`PayrollRun` — DRAFT → CALCULATED → APPROVED → POSTED → PAID (recalculation only in
  DRAFT/CALCULATED). The run stores the legal parameter snapshot used by its calculation.
* :class:`Payslip` / :class:`PayslipLine` — per employee; every line stores the formula version hash,
  the inputs, the explain trace and the parameter snapshot. Posted rows are immutable (DB trigger).
* :class:`ElectronicPayrollDocument` — nómina electrónica data (NominaIndividual / …DeAjuste) per
  employee and period; transmission is EXTERNAL (P15 FiscalGateway), status SIMULATED here.

Money is Decimal NUMERIC(20,4) (``AmountField``); quantities NUMERIC(20,6).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.fields import AmountField, QtyField
from kernel.models import AuditedModel, CompanyModel, ImmutableModel

CONCEPT_TYPES = ("DEVENGO", "DEDUCCION", "APORTE", "PROVISION")
FREQUENCIES = ("MONTHLY", "BIWEEKLY", "WEEKLY")


def _choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(v, v) for v in values]


class PayrollPeriod(CompanyModel, AuditedModel):
    OPEN, CLOSED = "OPEN", "CLOSED"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200, blank=True, default="")
    frequency = models.CharField(max_length=10, choices=_choices(FREQUENCIES), default="MONTHLY")
    start_date = models.DateField()
    end_date = models.DateField()
    payment_date = models.DateField(null=True)
    status = models.CharField(max_length=10, default=OPEN)

    class Meta:
        db_table = "payroll_period"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="payroll_period_code_uq"),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")), name="payroll_period_dates"
            ),
        ]


class PayrollConcept(CompanyModel, AuditedModel):
    ACTIVE, ARCHIVED = "ACTIVE", "ARCHIVED"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    concept_type = models.CharField(max_length=10, choices=_choices(CONCEPT_TYPES))
    formula_id = models.UUIDField(null=True)  # workflow.Formula (domain payroll); null = manual concept
    depends_on = models.JSONField(default=list)  # concept codes
    account_role = models.CharField(max_length=255, blank=True, default="")
    salary_base = models.BooleanField(default=False)  # salary-constitutive (IBC / Ley 1393 40 % rule)
    electronic_payroll_code = models.CharField(max_length=255, null=True)  # DIAN NominaIndividual element
    rounding_places = models.PositiveSmallIntegerField(default=2)  # COP minor unit (D-001)
    is_user_defined = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    status = models.CharField(max_length=10, default=ACTIVE)

    class Meta:
        db_table = "payroll_concept"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="payroll_concept_code_uq")
        ]


class PayrollNovelty(CompanyModel, AuditedModel):
    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="novelties")
    concept = models.ForeignKey(PayrollConcept, on_delete=models.PROTECT, related_name="novelties")
    employee_id = models.UUIDField()
    quantity = QtyField(null=True)
    amount = AmountField(null=True)
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "payroll_novelty"
        indexes: ClassVar = [models.Index(fields=["tenant_id", "company_id", "period", "employee_id"])]


class PayrollRun(CompanyModel, AuditedModel):
    DRAFT, CALCULATED, APPROVED, POSTED, PAID = "DRAFT", "CALCULATED", "APPROVED", "POSTED", "PAID"
    STATUSES = (DRAFT, CALCULATED, APPROVED, POSTED, PAID)

    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="runs")
    number = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=12, choices=_choices(STATUSES), default=DRAFT)
    employees = models.JSONField(default=list)  # selected employee ids; empty = every employee of the period
    calculation_date = models.DateField(null=True)  # date the legal parameters were read for (period end)
    parameter_snapshot = models.JSONField(default=dict)  # {code: ParameterValue.snapshot()}
    graph_order = models.JSONField(default=list)  # concept codes in topological order
    legally_verified = models.BooleanField(default=False)
    warnings = models.JSONField(default=list)
    total_earnings = AmountField(default=0)
    total_deductions = AmountField(default=0)
    total_net = AmountField(default=0)
    total_employer = AmountField(default=0)
    calculated_by = models.UUIDField(null=True)
    calculated_at = models.DateTimeField(null=True)
    approved_by = models.UUIDField(null=True)
    approved_at = models.DateTimeField(null=True)
    posted_by = models.UUIDField(null=True)
    posted_at = models.DateTimeField(null=True)
    journal_entry_id = models.UUIDField(null=True)
    journal_entry_number = models.CharField(max_length=60, blank=True, default="")
    paid_at = models.DateTimeField(null=True)
    payment_ref = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        db_table = "payroll_run"
        indexes: ClassVar = [models.Index(fields=["tenant_id", "company_id", "status"])]


class Payslip(CompanyModel, ImmutableModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="payslips")
    employee_id = models.UUIDField()
    employee_name = models.CharField(max_length=200, blank=True, default="")
    cost_center_id = models.UUIDField(null=True)
    earnings = AmountField(default=0)
    deductions = AmountField(default=0)
    employer_cost = AmountField(default=0)
    net_pay = AmountField(default=0)
    parameter_snapshot = models.JSONField(default=dict)
    legally_verified = models.BooleanField(default=False)
    legal_notice = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "payroll_payslip"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["run", "employee_id"], name="payroll_payslip_run_employee_uq")
        ]


class PayslipLine(CompanyModel, ImmutableModel):
    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name="lines")
    concept = models.ForeignKey(PayrollConcept, on_delete=models.PROTECT, related_name="+")
    concept_code = models.CharField(max_length=40)
    concept_type = models.CharField(max_length=10)
    sequence = models.PositiveIntegerField()  # topological position
    amount = AmountField()
    quantity = QtyField(null=True)
    formula_id = models.UUIDField(null=True)
    formula_version = models.PositiveIntegerField(null=True)
    formula_hash = models.CharField(max_length=64, blank=True, default="")
    inputs = models.JSONField(default=dict)
    trace = models.JSONField(default=list)
    parameters = models.JSONField(default=list)  # snapshots of the legal parameters this line read
    account_role = models.CharField(max_length=255, blank=True, default="")
    electronic_payroll_code = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "payroll_payslip_line"
        ordering: ClassVar = ["sequence"]


class ElectronicPayrollDocument(CompanyModel, AuditedModel):
    INDIVIDUAL, ADJUSTMENT = "NominaIndividual", "NominaIndividualDeAjuste"
    DRAFT, GENERATED, SUPERSEDED = "DRAFT", "GENERATED", "SUPERSEDED"

    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="electronic_documents")
    employee_id = models.UUIDField(null=True)
    document_type = models.CharField(max_length=30, default=INDIVIDUAL)
    adjustment_kind = models.CharField(max_length=10, blank=True, default="")  # Reemplazar | Eliminar
    replaces = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="adjustments")
    status = models.CharField(max_length=12, default=DRAFT)
    transmission_status = models.CharField(max_length=40, default="SIMULATED_NOT_TRANSMITTED")
    payload = models.JSONField(default=dict)
    cune = models.CharField(max_length=128, null=True)  # computed by P15 (FiscalGateway); never here
    generated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "payroll_electronic_document"
