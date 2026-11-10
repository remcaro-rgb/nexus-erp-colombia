"""Fixed-asset register, per-book depreciation state and asset documents (P14, shared/07 §5).

Tables (all ``assets_*``, company scoped, forced RLS in migration 0002):

* :class:`AssetBook` — depreciation book (NIIF / FISCAL) mapped to a ledger book code.
* :class:`AssetCategory` + :class:`CategoryBookDefault` — account roles, default method/life per book,
  useful-life range, capitalization threshold.
* :class:`Asset` — the register row (tag/QR, custodian, location, dimension slots, lifecycle). NIIF components
  are assets with ``parent`` set (componentization; partial disposal = disposing a component).
* :class:`AssetBookValue` — per asset per book: method, life, salvage, carrying amounts, the *anchor* used for
  prospective re-estimation (NIIF: changes in estimates are prospective).
* :class:`RegisterEntry` — append-only per-asset per-book sub-ledger of every movement (GL = register).
* :class:`DepreciationRun` / :class:`DepreciationLine` — one run per company + book + period (idempotent).
* :class:`Revaluation`, :class:`Impairment`, :class:`Transfer`, :class:`Disposal`, :class:`PhysicalCount`.

Money is ``Decimal`` (NUMERIC 20,4), quantities NUMERIC 20,6. No floats.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import models

from kernel.fields import AmountField, CurrencyField, PctField, QtyField
from kernel.models import AuditedModel, CompanyModel

ZERO = Decimal(0)

#: Dimension slots carried by assets and by every journal-producing line (F-ACC-DIM).
SLOTS = ("cost_center_id", "business_unit_id", "aux_abierto_id", "item_dimension_id")


class Framework(models.TextChoices):
    NIIF = "NIIF"
    FISCAL = "FISCAL"


class MasterStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class Method(models.TextChoices):
    STRAIGHT_LINE = "STRAIGHT_LINE"
    DECLINING = "DECLINING"  # declining balance (factor × straight-line rate), switches to SL when larger
    UNITS = "UNITS"  # units of production
    SUM_OF_YEARS = "SUM_OF_YEARS"  # [A] sum of the periods' digits (service API only; not in the category contract)


class StartConvention(models.TextChoices):
    FOLLOWING_MONTH = "FOLLOWING_MONTH"  # default (P14 prompt): depreciation starts the month after in-service
    SAME_MONTH = "SAME_MONTH"


class DimensionSlotsMixin(models.Model):
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)

    class Meta:
        abstract = True

    def slots(self) -> dict[str, Any]:
        return {s: getattr(self, s) for s in SLOTS}


class AssetBook(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    framework = models.CharField(max_length=10, choices=Framework.choices)
    ledger_book_code = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=MasterStatus.choices, default=MasterStatus.ACTIVE)
    start_convention = models.CharField(
        max_length=20, choices=StartConvention.choices, default=StartConvention.FOLLOWING_MONTH
    )

    class Meta:
        db_table = "assets_book"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="assets_book_uq")]


class AssetCategory(CompanyModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MasterStatus.choices, default=MasterStatus.ACTIVE)
    useful_life_months = models.PositiveIntegerField()
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.STRAIGHT_LINE)
    #: account role of the asset cost (default ``FIXED_ASSET_COST``); the other roles come from ``role_overrides``.
    account_role = models.CharField(max_length=255, blank=True, default="")
    role_overrides = models.JSONField(default=dict, blank=True)
    min_life_months = models.PositiveIntegerField(null=True, blank=True)
    max_life_months = models.PositiveIntegerField(null=True, blank=True)
    #: company policy threshold (COP); below it a purchase is expensed, not capitalized.
    capitalization_threshold = AmountField(null=True, blank=True)
    #: optional threshold in UVT (regulatory value read through kernel.parameters ``uvt``; legal review).
    capitalization_threshold_uvt = PctField(null=True, blank=True)

    class Meta:
        db_table = "assets_category"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="assets_category_uq"),
            models.CheckConstraint(condition=models.Q(useful_life_months__gte=1), name="assets_category_life_ck"),
        ]


class CategoryBookDefault(CompanyModel):
    category = models.ForeignKey(AssetCategory, on_delete=models.CASCADE, related_name="book_defaults")
    book = models.ForeignKey(AssetBook, on_delete=models.PROTECT)
    method = models.CharField(max_length=20, choices=Method.choices)
    useful_life_months = models.PositiveIntegerField()
    salvage_pct = PctField(default=ZERO)
    #: FISCAL books: parameter code with the maximum annual depreciation rate (ET; legal review), e.g.
    #: ``assets.fiscal.max_annual_rate.computo``. Read through kernel.parameters; UNKNOWN fails closed.
    rate_parameter_code = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        db_table = "assets_category_book_default"
        constraints = [models.UniqueConstraint(fields=["category", "book"], name="assets_category_book_uq")]


class Lifecycle(models.TextChoices):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    FULLY_DEPRECIATED = "FULLY_DEPRECIATED"
    DISPOSED = "DISPOSED"


class Asset(CompanyModel, AuditedModel, DimensionSlotsMixin):
    code = models.CharField(max_length=40)  # tag (QR payload)
    name = models.CharField(max_length=200)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="components")
    lifecycle = models.CharField(max_length=20, choices=Lifecycle.choices, default=Lifecycle.DRAFT)
    acquisition_cost = AmountField()
    currency = CurrencyField(default="COP")
    acquisition_date = models.DateField()
    in_service_date = models.DateField(null=True, blank=True)
    branch_id = models.UUIDField(null=True, blank=True)
    location = models.CharField(max_length=255, null=True, blank=True)
    custodian_employee_id = models.UUIDField(null=True, blank=True)  # restricted-pii (field policy)
    serial = models.CharField(max_length=120, blank=True, default="")
    warranty_until = models.DateField(null=True, blank=True)
    insurance_ref = models.CharField(max_length=120, blank=True, default="")
    supplier_id = models.UUIDField(null=True, blank=True)
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    source_number = models.CharField(max_length=60, blank=True, default="")
    source_line_no = models.PositiveIntegerField(null=True, blank=True)
    cost_components = models.JSONField(default=list, blank=True)
    disposed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "assets_asset"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="assets_asset_code_uq"),
            models.CheckConstraint(condition=models.Q(acquisition_cost__gte=0), name="assets_asset_cost_ck"),
        ]

    @property
    def contract_status(self) -> str:
        return MasterStatus.ARCHIVED if self.lifecycle == Lifecycle.DISPOSED else MasterStatus.ACTIVE


class BookValueStatus(models.TextChoices):
    PENDING = "PENDING"  # asset not yet in service
    ACTIVE = "ACTIVE"
    FULLY_DEPRECIATED = "FULLY_DEPRECIATED"
    DISPOSED = "DISPOSED"


class AssetBookValue(CompanyModel, AuditedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="book_values")
    book = models.ForeignKey(AssetBook, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=BookValueStatus.choices, default=BookValueStatus.PENDING)
    method = models.CharField(max_length=20, choices=Method.choices)
    useful_life_months = models.PositiveIntegerField()
    salvage_value = AmountField(default=ZERO)
    declining_factor = PctField(default=Decimal(2))
    units_total = QtyField(null=True, blank=True)
    depreciation_start = models.DateField(null=True, blank=True)  # first day of the first depreciated month
    cost = AmountField(default=ZERO)  # gross carrying amount (after revaluations)
    accumulated = AmountField(default=ZERO)
    impairment = AmountField(default=ZERO)
    surplus = AmountField(default=ZERO)  # revaluation surplus (OCI)
    periods_depreciated = models.PositiveIntegerField(default=0)
    units_used = QtyField(default=ZERO)
    anchor_base = AmountField(default=ZERO)
    anchor_periods = models.PositiveIntegerField(default=0)
    anchor_units = QtyField(null=True, blank=True)
    periods_since_anchor = models.PositiveIntegerField(default=0)
    units_since_anchor = QtyField(default=ZERO)
    last_period_end = models.DateField(null=True, blank=True)
    parameter_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "assets_asset_book"
        constraints = [models.UniqueConstraint(fields=["asset", "book"], name="assets_asset_book_uq")]

    @property
    def nbv(self) -> Decimal:
        value: Decimal = self.cost - self.accumulated - self.impairment
        return value


class RegisterKind(models.TextChoices):
    ACQUISITION = "ACQUISITION"
    OPENING = "OPENING"
    DEPRECIATION = "DEPRECIATION"
    REVALUATION = "REVALUATION"
    IMPAIRMENT = "IMPAIRMENT"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    DISPOSAL = "DISPOSAL"
    ESTIMATE_CHANGE = "ESTIMATE_CHANGE"


class RegisterEntry(CompanyModel, DimensionSlotsMixin):
    """Append-only (DB trigger) movement of one asset in one book; Σ = carrying amounts (invariant 5)."""

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="register_entries")
    book = models.ForeignKey(AssetBook, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=RegisterKind.choices)
    date = models.DateField()
    cost_delta = AmountField(default=ZERO)
    accumulated_delta = AmountField(default=ZERO)
    impairment_delta = AmountField(default=ZERO)
    surplus_delta = AmountField(default=ZERO)
    units = QtyField(null=True, blank=True)
    category_id = models.UUIDField()
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    journal_entry_id = models.UUIDField(null=True, blank=True)
    memo = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "assets_register_entry"
        indexes = [models.Index(fields=["company_id", "book", "date"], name="assets_register_book_date_ix")]


class DocStatus(models.TextChoices):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    CANCELLED = "CANCELLED"


class DocumentBase(CompanyModel, AuditedModel):
    number = models.CharField(max_length=60, blank=True, default="")
    document_date = models.DateField()
    posting_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=DocStatus.choices, default=DocStatus.DRAFT)
    currency = CurrencyField(default="COP")
    total = AmountField(default=ZERO)
    notes = models.TextField(blank=True, default="")
    lines = models.JSONField(default=list, blank=True)  # contract lines echo (amounts as strings)
    journal_entry_ids = models.JSONField(default=list, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class DepreciationRun(DocumentBase):
    book = models.ForeignKey(AssetBook, on_delete=models.PROTECT, related_name="runs")
    period_id = models.UUIDField()
    period_start = models.DateField()
    period_end = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "assets_depreciation_run"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "book", "period_id"],
                condition=~models.Q(status="CANCELLED"),
                name="assets_run_period_book_uq",
            )
        ]


class DepreciationLine(CompanyModel, DimensionSlotsMixin):
    """Per-asset detail of a posted run (append-only)."""

    run = models.ForeignKey(DepreciationRun, on_delete=models.PROTECT, related_name="detail")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    line_no = models.PositiveIntegerField()
    amount = AmountField()
    units = QtyField(null=True, blank=True)
    accumulated_after = AmountField()
    nbv_after = AmountField()
    expense_role = models.CharField(max_length=120)
    accumulated_role = models.CharField(max_length=120)

    class Meta:
        db_table = "assets_depreciation_line"
        constraints = [models.UniqueConstraint(fields=["run", "asset"], name="assets_run_asset_uq")]


class Revaluation(DocumentBase):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="revaluations")
    fair_value = AmountField(default=ZERO)

    class Meta:
        db_table = "assets_revaluation"


class Impairment(DocumentBase):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="impairments")
    recoverable_amount = AmountField(default=ZERO)

    class Meta:
        db_table = "assets_impairment"


class Transfer(CompanyModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="transfers")
    effective_date = models.DateField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    journal_entry_ids = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=2000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "assets_transfer"


class DisposalKind(models.TextChoices):
    SALE = "SALE"
    SCRAP = "SCRAP"
    DONATION = "DONATION"


class Disposal(CompanyModel):
    asset = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name="disposal")
    kind = models.CharField(max_length=10, choices=DisposalKind.choices)
    disposal_date = models.DateField()
    proceeds = AmountField(default=ZERO)
    vat = AmountField(default=ZERO)
    customer_id = models.UUIDField(null=True, blank=True)
    invoice_type = models.CharField(max_length=80, blank=True, default="")
    invoice_id = models.UUIDField(null=True, blank=True)
    invoice_number = models.CharField(max_length=60, blank=True, default="")
    results = models.JSONField(default=dict)  # per book: nbv, gain (negative = loss), journal_entry_id
    reason = models.CharField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "assets_disposal"


class PhysicalCount(DocumentBase):
    location = models.CharField(max_length=255, blank=True, default="")
    discrepancies = models.JSONField(default=dict, blank=True)
    submitted_by = models.UUIDField(null=True, blank=True)
    approved_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "assets_physical_count"
