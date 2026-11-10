"""Ledger data model (P05): chart of accounts, determination, books, journals,
periods, journal entries/lines, balances and the four accounting dimensions.

Money columns use the kernel precision fields (NUMERIC 20,4 / 24,10). Posted
entries and lines are protected by DB triggers (migration 0001); dimension
references on lines use composite foreign keys ``(company_id, <slot>) →
ledger_dimension_node(company_id, id)`` so a line can never reference another
company's node (DIM-R04).
"""

from __future__ import annotations

from typing import ClassVar, Self

from django.db import models
from django.db.models.functions import Lower

from kernel import clock
from kernel.fields import AmountField, CurrencyField, FxRateField
from kernel.models import AuditedModel, CompanyModel, TenantModel

DIMENSIONS = ("cost_center", "business_unit", "aux_abierto", "item_contable")
DIMENSION_CHOICES = [(d, d) for d in DIMENSIONS]
SLOT_BY_DIMENSION = {
    "cost_center": "cost_center_id",
    "business_unit": "business_unit_id",
    "aux_abierto": "aux_abierto_id",
    "item_contable": "item_dimension_id",
}
DIMENSION_BY_SLOT = {v: k for k, v in SLOT_BY_DIMENSION.items()}


# --------------------------------------------------------------------------- chart of accounts


class Account(CompanyModel, AuditedModel):
    class Type(models.TextChoices):
        ASSET = "asset"
        LIABILITY = "liability"
        EQUITY = "equity"
        INCOME = "income"
        EXPENSE = "expense"
        MEMO = "memo"

    class Nature(models.TextChoices):
        DEBIT = "debit"
        CREDIT = "credit"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=10, choices=Type.choices)
    nature = models.CharField(max_length=6, choices=Nature.choices)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    level = models.PositiveSmallIntegerField(default=1)
    postable = models.BooleanField(default=True)
    is_control = models.BooleanField(default=False)
    # Source document types (prefix match, e.g. "sales.") allowed to post to a control account.
    control_sources = models.JSONField(default=list, blank=True)
    reconcilable = models.BooleanField(default=False)
    currency_restriction = models.CharField(max_length=3, blank=True, default="")
    requires_dimensions = models.JSONField(default=list, blank=True)
    puc_code = models.CharField(max_length=20, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "ledger_account"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="ledger_account_code_uq"),
        ]


class AccountRole(CompanyModel):
    """Logical account roles (shared/07): AR, AP, SALES_REVENUE, VAT_PAYABLE, ROUNDING…"""

    code = models.CharField(max_length=60)
    name = models.CharField(max_length=200)

    class Meta:
        db_table = "ledger_account_role"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="ledger_account_role_uq"),
        ]


class AccountDetermination(CompanyModel):
    """Priority-ordered rule ``role (+ optional match keys) → account``."""

    role_code = models.CharField(max_length=60)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    priority = models.IntegerField(default=0)
    document_type = models.CharField(max_length=80, blank=True, default="")
    journal_code = models.CharField(max_length=40, blank=True, default="")
    tax_code = models.CharField(max_length=40, blank=True, default="")
    warehouse_id = models.UUIDField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "ledger_account_determination"
        indexes = [models.Index(fields=["tenant_id", "company_id", "role_code"], name="ledger_det_role_ix")]


class Book(CompanyModel):
    code = models.CharField(max_length=20)  # NIIF, FISCAL
    name = models.CharField(max_length=200)
    mirror = models.BooleanField(default=True)
    primary = models.BooleanField(default=False)

    class Meta:
        db_table = "ledger_book"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="ledger_book_uq")]


class Journal(CompanyModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)

    class Meta:
        db_table = "ledger_journal"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="ledger_journal_uq")]


class Period(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        SOFT_CLOSED = "SOFT_CLOSED"
        CLOSED = "CLOSED"

    fiscal_year = models.PositiveSmallIntegerField()
    number = models.PositiveSmallIntegerField()  # 1..12, 13 = adjustment period
    code = models.CharField(max_length=10)  # 2026-11, 2026-13
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "ledger_period"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="ledger_period_code_uq"),
            models.CheckConstraint(condition=models.Q(start__lte=models.F("end")), name="ledger_period_range_ck"),
        ]


class ExchangeRate(CompanyModel):
    currency = CurrencyField()
    date = models.DateField()
    rate = FxRateField()
    source = models.CharField(max_length=20, default="manual")

    class Meta:
        db_table = "ledger_exchange_rate"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "currency", "date"], name="ledger_fx_uq"),
        ]


# --------------------------------------------------------------------------- dimensions


class DimensionNode(CompanyModel, AuditedModel):
    """One master row for the four dimensions (FEATURES-ADDENDUM F-ACC-DIM)."""

    class NodeType(models.TextChoices):
        AGRUPADOR = "AGRUPADOR"
        DETALLE = "DETALLE"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    dimension = models.CharField(max_length=20, choices=DIMENSION_CHOICES)
    code = models.CharField(max_length=40)
    description = models.CharField(max_length=200)
    node_type = models.CharField(max_length=10, choices=NodeType.choices)
    # Composite FK (company_id, dimension, parent_id) → (company_id, dimension, id) in migration 0001.
    parent_id = models.UUIDField(null=True, blank=True)
    depth = models.PositiveSmallIntegerField(default=1)
    path = models.CharField(max_length=2000, default="")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    archived_at = models.DateField(null=True, blank=True)
    sort_key = models.CharField(max_length=255, null=True, blank=True)
    external_ref = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "ledger_dimension_node"
        constraints = [
            models.UniqueConstraint("company_id", "dimension", Lower("code"), name="ledger_dim_code_ci_uq"),
            models.UniqueConstraint(fields=["company_id", "id"], name="ledger_dim_company_id_uq"),
            models.UniqueConstraint(fields=["company_id", "dimension", "id"], name="ledger_dim_company_dim_id_uq"),
            models.CheckConstraint(
                condition=models.Q(depth__gte=1) & models.Q(depth__lte=10), name="ledger_dim_depth_ck"
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True) | models.Q(valid_from__lte=models.F("valid_to")),
                name="ledger_dim_validity_ck",
            ),
            models.CheckConstraint(condition=~models.Q(parent_id=models.F("id")), name="ledger_dim_not_self_ck"),
        ]
        indexes = [
            models.Index(fields=["company_id", "dimension", "parent_id"], name="ledger_dim_parent_ix"),
        ]


class CostCenterManager[M: "DimensionNode"](models.Manager[M]):
    dimension = "cost_center"

    def get_queryset(self) -> models.QuerySet[M]:
        return super().get_queryset().filter(dimension=self.dimension)


class _BusinessUnitManager[M: "DimensionNode"](CostCenterManager[M]):
    dimension = "business_unit"


class _AuxAbiertoManager[M: "DimensionNode"](CostCenterManager[M]):
    dimension = "aux_abierto"


class _ItemContableManager[M: "DimensionNode"](CostCenterManager[M]):
    dimension = "item_contable"


class CostCenter(DimensionNode):
    """``kernel.dimensions`` FK target ``ledger.CostCenter`` (proxy of DimensionNode)."""

    objects: ClassVar[CostCenterManager[Self]] = CostCenterManager()

    class Meta:
        proxy = True


class BusinessUnit(DimensionNode):
    objects: ClassVar[_BusinessUnitManager[Self]] = _BusinessUnitManager()

    class Meta:
        proxy = True


class AuxAbierto(DimensionNode):
    objects: ClassVar[_AuxAbiertoManager[Self]] = _AuxAbiertoManager()

    class Meta:
        proxy = True


class ItemContable(DimensionNode):
    objects: ClassVar[_ItemContableManager[Self]] = _ItemContableManager()

    class Meta:
        proxy = True


class DimensionClosure(models.Model):
    """Effective-dated closure table (DIM-R06): (ancestor, descendant, depth, validity)."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField()
    company_id = models.UUIDField()
    dimension = models.CharField(max_length=20, choices=DIMENSION_CHOICES)
    ancestor_id = models.UUIDField()
    descendant_id = models.UUIDField()
    distance = models.PositiveSmallIntegerField()
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "ledger_dimension_closure"
        indexes = [
            models.Index(fields=["company_id", "ancestor_id"], name="ledger_dimcl_anc_ix"),
            models.Index(fields=["company_id", "descendant_id"], name="ledger_dimcl_desc_ix"),
        ]


class DimensionSettings(CompanyModel, AuditedModel):
    dimension = models.CharField(max_length=20, choices=DIMENSION_CHOICES)
    max_depth = models.PositiveSmallIntegerField(default=6)  # D-007
    label_es = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        db_table = "ledger_dimension_settings"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "dimension"], name="ledger_dimset_uq"),
            models.CheckConstraint(
                condition=models.Q(max_depth__gte=1) & models.Q(max_depth__lte=10), name="ledger_dimset_depth_ck"
            ),
        ]


class DimensionRuleSet(CompanyModel, AuditedModel):
    """Versioned container (ETag) of a company's rule matrix."""

    class Meta:
        db_table = "ledger_dimension_rule_set"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id"], name="ledger_dimruleset_uq")]


class DimensionRule(CompanyModel):
    class ScopeType(models.TextChoices):
        ACCOUNT = "account"
        ACCOUNT_RANGE = "account_range"
        DOCUMENT_TYPE = "document_type"
        CONCEPT = "concept"
        JOURNAL = "journal"
        STOCK_DOCUMENT = "stock_document"

    class Mode(models.TextChoices):
        REQUIRED = "REQUIRED"
        ALLOWED = "ALLOWED"
        FORBIDDEN = "FORBIDDEN"

    rule_set = models.ForeignKey(DimensionRuleSet, on_delete=models.CASCADE, related_name="rules")
    dimension = models.CharField(max_length=20, choices=DIMENSION_CHOICES)
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices)
    scope_ref = models.CharField(max_length=255)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    priority = models.IntegerField(default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "ledger_dimension_rule"
        indexes = [models.Index(fields=["tenant_id", "company_id", "dimension"], name="ledger_dimrule_ix")]


# --------------------------------------------------------------------------- journal entries


class JournalEntry(CompanyModel):
    """Posted entry header. Only ``status`` posted→reversed and ``reversed_by_id`` may
    change after insert (DB trigger, migration 0001); never deleted (C1-5)."""

    class Status(models.TextChoices):
        POSTED = "posted"
        REVERSED = "reversed"

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="+")
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="+")
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=60)
    posting_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    source_number = models.CharField(max_length=60, blank=True, default="")
    purpose = models.CharField(max_length=120)
    reversal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reversed_by_id = models.UUIDField(null=True, blank=True)
    memo = models.CharField(max_length=500, blank=True, default="")
    currency = CurrencyField(default="COP")
    branch_id = models.UUIDField(null=True, blank=True)
    total_debit = AmountField(default=0)
    total_credit = AmountField(default=0)
    created_by = models.UUIDField()
    posted_by = models.UUIDField()
    channel = models.CharField(max_length=10)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "ledger_journal_entry"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "book", "source_type", "source_id", "purpose"],
                name="ledger_entry_source_uq",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "book", "number"], name="ledger_entry_number_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(total_debit=models.F("total_credit")), name="ledger_entry_balanced_ck"
            ),
        ]


class JournalLine(CompanyModel):
    """Append-only line (DB trigger). One side per line; amounts in COP plus the
    transaction currency amount."""

    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    book_id = models.UUIDField()
    period_id = models.UUIDField()
    posting_date = models.DateField()
    line_no = models.PositiveIntegerField()
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    debit = AmountField(default=0)
    credit = AmountField(default=0)
    amount_currency = AmountField(default=0)
    currency = CurrencyField(default="COP")
    fx_rate = FxRateField(default=1)
    party_id = models.UUIDField(null=True, blank=True)
    party_kind = models.CharField(max_length=20, blank=True, default="")
    tax_code = models.CharField(max_length=40, blank=True, default="")
    tax_base = AmountField(null=True, blank=True)
    item_id = models.UUIDField(null=True, blank=True)  # inventory product (not item_contable)
    warehouse_id = models.UUIDField(null=True, blank=True)
    branch_id = models.UUIDField(null=True, blank=True)
    # Dimension slots (kernel.dimensions); composite FKs added in migration 0001.
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    dims_hash = models.CharField(max_length=64)
    is_rounding = models.BooleanField(default=False)
    memo = models.CharField(max_length=500, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    reconcile_key = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        db_table = "ledger_journal_line"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0), name="ledger_line_nonneg_ck"
            ),
            models.CheckConstraint(condition=models.Q(debit=0) | models.Q(credit=0), name="ledger_line_one_side_ck"),
        ]
        indexes = [
            models.Index(fields=["tenant_id", "company_id", "account", "posting_date"], name="ledger_line_acct_ix"),
            models.Index(fields=["company_id", "cost_center_id"], name="ledger_line_cc_ix"),
            models.Index(fields=["company_id", "business_unit_id"], name="ledger_line_bu_ix"),
            models.Index(fields=["company_id", "aux_abierto_id"], name="ledger_line_aux_ix"),
            models.Index(fields=["company_id", "item_dimension_id"], name="ledger_line_itemdim_ix"),
        ]


class LedgerBalance(TenantModel):
    """``ledger_balance(company, book, account, period, dims_hash)`` maintained in-line."""

    company_id = models.UUIDField()
    book_id = models.UUIDField()
    account_id = models.UUIDField()
    period_id = models.UUIDField()
    dims_hash = models.CharField(max_length=64)
    cost_center_id = models.UUIDField(null=True, blank=True)
    business_unit_id = models.UUIDField(null=True, blank=True)
    aux_abierto_id = models.UUIDField(null=True, blank=True)
    item_dimension_id = models.UUIDField(null=True, blank=True)
    debit = AmountField(default=0)
    credit = AmountField(default=0)

    class Meta:
        db_table = "ledger_balance"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "book_id", "account_id", "period_id", "dims_hash"],
                name="ledger_balance_uq",
            ),
        ]
