"""Treasury data model (P16a: funds, bank accounts, cash boxes, concepts, RC/CE vouchers, payment batches,
bank statements) — FEATURES-ADDENDUM F-TRE-RC / F-TRE-CE.

Money NUMERIC(20,4), FX NUMERIC(24,10) (C2-2, D-001). Every table carries ``tenant_id`` + ``company_id`` with
forced row-level security (migration 0002). Posted vouchers are immutable (DB trigger; the header only accepts the
``POSTED → REVERSED`` status change and its link): corrections go through a reversal voucher, never deletion.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models

from kernel.fields import AmountField, CurrencyField, FxRateField
from kernel.models import AuditedModel, CompanyModel

DIM_SLOTS = ("cost_center_id", "business_unit_id", "aux_abierto_id", "item_dimension_id")


class Status(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


# --------------------------------------------------------------------------- masters


class TreasurySettings(CompanyModel, AuditedModel):
    """Per-company treasury defaults: D-017 prefixes and the approval thresholds (COP)."""

    rc_prefix = models.CharField(max_length=10, default="RC-")
    ce_prefix = models.CharField(max_length=10, default="CE-")
    #: RC at or above this amount need approval (None = never).
    rc_approval_threshold = AmountField(null=True, blank=True)
    #: CE at or above this amount need approval (None = never; 0 = always, "CE always above COP 0").
    ce_approval_threshold = AmountField(null=True, blank=True, default=Decimal(0))
    #: CE / payment batches at or above this amount need two approvers (FEATURES-ADDENDUM default COP 20,000,000).
    dual_approval_threshold = AmountField(default=Decimal("20000000"))

    class Meta:
        db_table = "treasury_settings"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id"], name="treasury_settings_company_uq"),
        ]


class Fund(CompanyModel, AuditedModel):
    """A bank account or cash box as the money side of a voucher (its GL account comes from the linked master)."""

    class FundType(models.TextChoices):
        BANK = "BANK"
        CASH = "CASH"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    fund_type = models.CharField(max_length=4, choices=FundType.choices)
    currency = CurrencyField()
    allow_overdraft = models.BooleanField(default=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "treasury_fund"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="treasury_fund_code_uq"),
        ]


class BankAccount(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    bank_code = models.CharField(max_length=40)
    account_number_enc = models.TextField()  # encrypted at rest (treasury.crypto); never returned in clear
    account_number_last4 = models.CharField(max_length=4)
    currency = CurrencyField()
    account_id = models.UUIDField()  # ledger Account (GL role of the fund)
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, null=True, blank=True, related_name="bank_accounts")
    gmf_exempt = models.BooleanField(default=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "treasury_bank_account"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="treasury_bank_account_code_uq"),
        ]


class CashBox(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    currency = CurrencyField()
    account_id = models.UUIDField()
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, null=True, blank=True, related_name="cash_boxes")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "treasury_cash_box"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="treasury_cash_box_code_uq"),
        ]


class TreasuryConcept(CompanyModel, AuditedModel):
    """Non-AR/AP reason of a voucher line (loan received, reimbursement, expense, petty-cash refill…)."""

    class Direction(models.TextChoices):
        IN = "IN"
        OUT = "OUT"
        BOTH = "BOTH"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    direction = models.CharField(max_length=4, choices=Direction.choices)
    account_role = models.CharField(max_length=120)  # ledger role code or ``account:<code>``
    withholding_concept_ids = models.JSONField(default=list)
    withholding_codes = models.JSONField(default=list)  # P06 tax codes proposed on lines of this concept
    requires_dimension = models.BooleanField(default=False)
    requires_party = models.BooleanField(default=False)
    default_dimensions = models.JSONField(default=dict)
    approval_threshold = AmountField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "treasury_concept"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="treasury_concept_code_uq"),
        ]


# --------------------------------------------------------------------------- vouchers


class TreasuryVoucher(CompanyModel, AuditedModel):
    """Recibo de caja (RC) or comprobante de egreso (CE); numbered (D-017) only at POST."""

    class Type(models.TextChoices):
        RC = "RC"
        CE = "CE"

    class State(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"  # = PENDING_APPROVAL of the FEATURES-ADDENDUM
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        POSTED = "POSTED"
        REVERSED = "REVERSED"
        CANCELLED = "CANCELLED"

    class Method(models.TextChoices):
        CASH = "CASH"
        TRANSFER = "TRANSFER"
        CHECK = "CHECK"
        CARD = "CARD"
        OTHER = "OTHER"

    voucher_type = models.CharField(max_length=2, choices=Type.choices)
    number = models.CharField(max_length=40, blank=True, default="")
    document_date = models.DateField()
    posting_date = models.DateField(null=True, blank=True)
    branch_id = models.UUIDField(null=True, blank=True)
    party_id = models.UUIDField(null=True, blank=True)
    party_kind = models.CharField(max_length=10, blank=True, default="")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name="vouchers")
    payment_method = models.CharField(max_length=8, choices=Method.choices)
    reference = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    total = AmountField(default=Decimal(0))  # money moved through the fund (voucher currency)
    withholding_total = AmountField(default=Decimal(0))
    notes = models.TextField(blank=True, default="")
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    source_number = models.CharField(max_length=60, blank=True, default="")
    approval_request_id = models.UUIDField(null=True, blank=True)
    dual_approval = models.BooleanField(default=False)
    approved_by = models.JSONField(default=list)  # principal ids of the approvers (in order)
    approved_at = models.DateTimeField(null=True, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)
    posted_entry_id = models.UUIDField(null=True, blank=True)
    reversal_of = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal_voucher"
    )
    reversed_by_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    fund_snapshot = models.JSONField(default=dict)
    tax_snapshot = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)

    class Meta:
        db_table = "treasury_voucher"
        indexes: ClassVar = [
            models.Index(fields=["tenant_id", "company_id", "voucher_type", "status"], name="treasury_voucher_st_idx"),
        ]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "voucher_type", "number"],
                condition=~models.Q(number=""),
                name="treasury_voucher_number_uq",
            ),
            models.CheckConstraint(condition=models.Q(total__gte=0), name="treasury_voucher_total_ck"),
        ]


class TreasuryVoucherLine(CompanyModel):
    class Kind(models.TextChoices):
        AR_APPLICATION = "AR_APPLICATION"
        AP_APPLICATION = "AP_APPLICATION"
        ADVANCE = "ADVANCE"
        CONCEPT = "CONCEPT"
        WITHHOLDING = "WITHHOLDING"
        ROUNDING = "ROUNDING"

    voucher = models.ForeignKey(TreasuryVoucher, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    description = models.CharField(max_length=255, blank=True, default="")
    open_item_id = models.UUIDField(null=True, blank=True)
    open_item_document = models.JSONField(default=dict)  # {type, id, number} of the applied invoice/bill
    concept = models.ForeignKey(TreasuryConcept, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    tax_code = models.CharField(max_length=40, blank=True, default="")
    tax_base = AmountField(null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    amount = AmountField()  # voucher currency, always ≥ 0; the kind gives the side
    amount_cop = AmountField(default=Decimal(0))
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    posted = models.BooleanField(default=False)

    class Meta:
        db_table = "treasury_voucher_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["voucher", "line_no"], name="treasury_voucher_line_no_uq"),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="treasury_voucher_line_amount_ck"),
        ]

    def dims(self) -> dict[str, str]:
        return {s: str(getattr(self, s)) for s in DIM_SLOTS if getattr(self, s)}


class ApApplication(CompanyModel):
    """Application of a CE to a P11 AP open item (P11 has no application table yet — CCR P16a-1)."""

    voucher = models.ForeignKey(TreasuryVoucher, on_delete=models.PROTECT, related_name="ap_applications")
    open_item_id = models.UUIDField()
    amount = AmountField()
    amount_cop = AmountField()
    applied_on = models.DateField()
    reversed = models.BooleanField(default=False)

    class Meta:
        db_table = "treasury_ap_application"
        indexes: ClassVar = [models.Index(fields=["tenant_id", "open_item_id"], name="treasury_ap_app_item_idx")]


# --------------------------------------------------------------------------- payment batches


class PaymentBatch(CompanyModel, AuditedModel):
    class State(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        POSTED = "POSTED"  # executed: one CE per payee
        REVERSED = "REVERSED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=40)
    document_date = models.DateField()
    payment_date = models.DateField()
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name="+")
    currency = CurrencyField()
    status = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    total = AmountField(default=Decimal(0))
    notes = models.TextField(blank=True, default="")
    approval_request_id = models.UUIDField(null=True, blank=True)
    dual_approval = models.BooleanField(default=False)
    approved_by = models.JSONField(default=list)
    executed_at = models.DateTimeField(null=True, blank=True)
    file_label = models.CharField(max_length=40, blank=True, default="")
    file_name = models.CharField(max_length=120, blank=True, default="")
    file_content = models.TextField(blank=True, default="")
    file_sha256 = models.CharField(max_length=64, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "treasury_payment_batch"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="treasury_batch_number_uq"),
        ]


class PaymentBatchLine(CompanyModel):
    batch = models.ForeignKey(PaymentBatch, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    party_id = models.UUIDField()
    open_item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    amount = AmountField()
    tax_code = models.CharField(max_length=40, blank=True, default="")
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    voucher = models.ForeignKey(TreasuryVoucher, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "treasury_payment_batch_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["batch", "line_no"], name="treasury_batch_line_no_uq"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="treasury_batch_line_amount_ck"),
        ]


# --------------------------------------------------------------------------- bank statements (reconciliation)


class BankStatement(CompanyModel, AuditedModel):
    class State(models.TextChoices):
        OPEN = "OPEN"
        RECONCILED = "RECONCILED"

    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="statements")
    date_from = models.DateField()
    date_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    source = models.CharField(max_length=20, default="MANUAL")  # MANUAL | CSV (generic synthetic mapping)

    class Meta:
        db_table = "treasury_bank_statement"


class StatementLine(CompanyModel, AuditedModel):
    class State(models.TextChoices):
        UNMATCHED = "UNMATCHED"
        MATCHED = "MATCHED"

    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True, default="")
    reference = models.CharField(max_length=120, blank=True, default="")
    amount = AmountField()  # signed: + inflow, − outflow
    state = models.CharField(max_length=10, choices=State.choices, default=State.UNMATCHED)
    voucher = models.ForeignKey(TreasuryVoucher, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    matched_by = models.UUIDField(null=True, blank=True)
    matched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "treasury_statement_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["statement", "line_no"], name="treasury_statement_line_no_uq"),
            models.UniqueConstraint(
                fields=["voucher"], condition=models.Q(voucher__isnull=False), name="treasury_statement_voucher_uq"
            ),
        ]


__all__ = [
    "DIM_SLOTS",
    "ApApplication",
    "BankAccount",
    "BankStatement",
    "CashBox",
    "Fund",
    "PaymentBatch",
    "PaymentBatchLine",
    "StatementLine",
    "TreasuryConcept",
    "TreasurySettings",
    "TreasuryVoucher",
    "TreasuryVoucherLine",
]
