"""Intercompany and consolidation data model (P16b, shared/07 §6, S10).

Intercompany (tenant level, visible to users of either company — RLS predicate on ``company_a_id`` /
``company_b_id``):

* :class:`IntercompanyAgreement` — a company pair (A sells to B): account roles, pricing rule, auto-mirror flag,
  default warehouse of B.
* :class:`IntercompanyLink` — one ``ic_link_id`` per mirrored pair of documents (A-side document + entry, B-side
  document + entry), append-only. Carries the sale value and A's cost of goods so consolidation can eliminate
  the unrealized profit still in B's inventory (``consumed_amount`` is the part B has already consumed/sold).
* :class:`IntercompanyDocument` — the B-side document created by the system principal (mirror vendor invoice,
  goods receipt reference, settlement, netting) with recorded causation.

Consolidation (tenant level, a group spans companies; RLS on tenant, application check that every member company
is in the caller's context and authorized):

* :class:`ConsolidationGroup` + :class:`GroupMember` (ownership %, method FULL/PROPORTIONAL/EQUITY, historical
  equity rate, investment account in the parent's books) + :class:`GroupAccountMap` (chart mapping to the group
  CoA, prefix based).
* :class:`ConsolidationRun` (document: DRAFT → POSTED → REVERSED, or CANCELLED) with manual adjustment lines
  (:class:`ConsolidationRunLine`).
* :class:`ConsolidationEntry` + :class:`ConsolidationEntryLine` — the **consolidation book** (translation, CTA,
  eliminations, NCI, adjustments). Append-only (DB trigger); nothing is ever posted to a subsidiary's ledger.

Money is ``Decimal`` (NUMERIC 20,4), FX rates NUMERIC 24,10, percentages NUMERIC 9,6. No floats.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from kernel.fields import AmountField, CurrencyField, FxRateField, PctField, QtyField
from kernel.models import AuditedModel, TenantModel

ZERO = Decimal(0)
HUNDRED = Decimal(100)

#: Default account roles of an agreement (shared/07 §6, S10). Overridable per agreement in ``accounts``.
DEFAULT_ROLES: dict[str, str] = {
    "ic_receivable": "IC_RECEIVABLE",
    "ic_payable": "IC_PAYABLE",
    "revenue": "REVENUE_IC",
    "cogs": "COGS",
    "inventory": "INVENTORY",
    "vat_payable": "VAT_PAYABLE",
    "vat_recoverable": "VAT_RECOVERABLE",
    "expense": "EXPENSE_IC",
    "bank": "BANK",
}


class MasterStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class PricingRule(models.TextChoices):
    TRANSFER_PRICE = "TRANSFER_PRICE"  # the price on A's invoice (default)
    COST_PLUS = "COST_PLUS"  # A's cost × (1 + markup %), checked against the invoice


class IntercompanyAgreement(TenantModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MasterStatus.choices, default=MasterStatus.ACTIVE)
    company_a_id = models.UUIDField()
    company_b_id = models.UUIDField()
    #: role overrides, keys of :data:`DEFAULT_ROLES` (+ ``pricing_rule``, ``markup_pct``, ``auto_mirror``,
    #: ``default_warehouse_id`` as accepted by the frozen contract's free-form ``accounts`` object).
    accounts = models.JSONField(default=dict, blank=True)
    pricing_rule = models.CharField(max_length=20, choices=PricingRule.choices, default=PricingRule.TRANSFER_PRICE)
    markup_pct = PctField(default=ZERO)
    auto_mirror = models.BooleanField(default=True)
    default_warehouse_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "consolidation_ic_agreement"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "code"], name="consolidation_ic_agreement_code_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_a_id", "company_b_id"], name="consolidation_ic_agreement_pair_uq"
            ),
            models.CheckConstraint(
                condition=~models.Q(company_a_id=models.F("company_b_id")), name="consolidation_ic_agreement_pair_ck"
            ),
        ]

    def role(self, key: str) -> str:
        value = (self.accounts or {}).get(key)
        return str(value) if value else DEFAULT_ROLES[key]


class LinkKind(models.TextChoices):
    SALE = "SALE"  # A's IC sales invoice ↔ B's mirror vendor invoice (+ receipt if goods)
    SETTLEMENT = "SETTLEMENT"  # B pays A
    NETTING = "NETTING"  # receivable and payable netted without cash


class IntercompanyLink(TenantModel):
    """``ic_link_id``: append-only (DB trigger) except ``consumed_amount`` (buyer consumption of IC goods)."""

    agreement = models.ForeignKey(IntercompanyAgreement, on_delete=models.PROTECT, related_name="links")
    kind = models.CharField(max_length=12, choices=LinkKind.choices)
    company_a_id = models.UUIDField()
    company_b_id = models.UUIDField()
    document_date = models.DateField()
    a_document_type = models.CharField(max_length=80)
    a_document_id = models.UUIDField()
    a_document_number = models.CharField(max_length=60, blank=True, default="")
    a_entry_ids = models.JSONField(default=list, blank=True)
    b_document_id = models.UUIDField(null=True, blank=True)
    b_entry_ids = models.JSONField(default=list, blank=True)
    currency = CurrencyField(default="COP")
    #: subtotal before taxes at transfer price (COP)
    amount = AmountField(default=ZERO)
    tax_amount = AmountField(default=ZERO)
    #: A's cost of the goods sold to B (COP); zero for services
    cost_amount = AmountField(default=ZERO)
    #: transfer-price value of goods still to be consumed by B is ``goods_amount - consumed_amount``
    goods_amount = AmountField(default=ZERO)
    consumed_amount = AmountField(default=ZERO)
    created_by = models.UUIDField()
    on_behalf_of = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "consolidation_ic_link"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "kind", "a_document_type", "a_document_id"], name="consolidation_ic_link_src_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(consumed_amount__gte=0) & models.Q(consumed_amount__lte=models.F("goods_amount")),
                name="consolidation_ic_link_consumed_ck",
            ),
        ]


class IcDocumentType(models.TextChoices):
    MIRROR_VENDOR_INVOICE = "MIRROR_VENDOR_INVOICE"
    SETTLEMENT = "SETTLEMENT"
    NETTING = "NETTING"


class IntercompanyDocument(TenantModel):
    """B-side (or settlement) document created by the IC system principal; immutable once written."""

    agreement = models.ForeignKey(IntercompanyAgreement, on_delete=models.PROTECT, related_name="documents")
    company_id = models.UUIDField()
    doc_type = models.CharField(max_length=30, choices=IcDocumentType.choices)
    number = models.CharField(max_length=60)
    document_date = models.DateField()
    counterpart_company_id = models.UUIDField()
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    source_number = models.CharField(max_length=60, blank=True, default="")
    currency = CurrencyField(default="COP")
    subtotal = AmountField(default=ZERO)
    tax_amount = AmountField(default=ZERO)
    total = AmountField(default=ZERO)
    lines = models.JSONField(default=list, blank=True)
    stock_move_ids = models.JSONField(default=list, blank=True)
    entry_ids = models.JSONField(default=list, blank=True)
    #: system principal that wrote the document and the user it acted for (recorded causation)
    created_by = models.UUIDField()
    on_behalf_of = models.UUIDField(null=True, blank=True)
    causation_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "consolidation_ic_document"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="consolidation_ic_doc_number_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "doc_type", "source_type", "source_id"],
                name="consolidation_ic_doc_source_uq",
            ),
        ]


# --------------------------------------------------------------------------- consolidation


class Method(models.TextChoices):
    FULL = "FULL"
    PROPORTIONAL = "PROPORTIONAL"
    EQUITY = "EQUITY"


class ConsolidationGroup(TenantModel, AuditedModel):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=MasterStatus.choices, default=MasterStatus.ACTIVE)
    parent_company_id = models.UUIDField()
    presentation_currency = CurrencyField(default="COP")
    #: consolidation periods up to and including this ``YYYY-MM`` are closed (no new run can be posted)
    closed_through = models.CharField(max_length=7, blank=True, default="")
    #: group account codes for the generated lines (synthetic group CoA, editable)
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "consolidation_group"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="consolidation_group_code_uq")]


class GroupMember(TenantModel):
    group = models.ForeignKey(ConsolidationGroup, on_delete=models.CASCADE, related_name="members")
    company_id = models.UUIDField()
    position = models.PositiveIntegerField(default=0)
    ownership_pct = PctField(default=HUNDRED)
    method = models.CharField(max_length=14, choices=Method.choices, default=Method.FULL)
    #: historical rate (presentation per functional unit) for equity translation when the currencies differ
    equity_historical_rate = FxRateField(null=True, blank=True)
    #: parent's investment account code (local CoA of the parent) eliminated against this member's equity
    investment_account_code = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "consolidation_group_member"
        constraints = [
            models.UniqueConstraint(fields=["group", "company_id"], name="consolidation_member_uq"),
            models.CheckConstraint(
                condition=models.Q(ownership_pct__gte=0) & models.Q(ownership_pct__lte=100),
                name="consolidation_member_pct_ck",
            ),
        ]


class GroupAccountMap(TenantModel):
    """Local account code prefix → group account (longest prefix wins; company-specific before group-wide)."""

    group = models.ForeignKey(ConsolidationGroup, on_delete=models.CASCADE, related_name="account_maps")
    company_id = models.UUIDField(null=True, blank=True)
    local_prefix = models.CharField(max_length=20)
    group_account = models.CharField(max_length=20)
    group_name = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "consolidation_account_map"
        constraints = [
            models.UniqueConstraint(fields=["group", "company_id", "local_prefix"], name="consolidation_map_uq"),
        ]


class RunStatus(models.TextChoices):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    CANCELLED = "CANCELLED"


class ConsolidationRun(TenantModel, AuditedModel):
    group = models.ForeignKey(ConsolidationGroup, on_delete=models.PROTECT, related_name="runs")
    number = models.CharField(max_length=60)
    period = models.CharField(max_length=7)  # YYYY-MM
    document_date = models.DateField()
    posting_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.DRAFT)
    currency = CurrencyField(default="COP")
    notes = models.TextField(blank=True, default="")
    total = AmountField(default=ZERO)
    rates = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.UUIDField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=2000, blank=True, default="")
    channel = models.CharField(max_length=10, blank=True, default="")
    on_behalf_of = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "consolidation_run"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "number"], name="consolidation_run_number_uq")]


class ConsolidationRunLine(TenantModel):
    """Manual consolidation adjustment: ``amount = quantity × unit_price``; positive = debit, negative = credit."""

    run = models.ForeignKey(ConsolidationRun, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    quantity = QtyField(default=Decimal(1))
    uom = models.CharField(max_length=20, default="UND")
    unit_price = AmountField(default=ZERO)
    amount = AmountField(default=ZERO)
    tax_code = models.CharField(max_length=255, null=True, blank=True)
    account_code = models.CharField(max_length=255, null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "consolidation_run_line"
        constraints = [models.UniqueConstraint(fields=["run", "line_no"], name="consolidation_run_line_uq")]


class EntryKind(models.TextChoices):
    TRANSLATION = "TRANSLATION"  # CTA to OCI
    IC_BALANCE = "IC_BALANCE"
    IC_REVENUE_COGS = "IC_REVENUE_COGS"
    UNREALIZED_PROFIT = "UNREALIZED_PROFIT"
    INVESTMENT_EQUITY = "INVESTMENT_EQUITY"
    NCI = "NCI"
    EQUITY_METHOD = "EQUITY_METHOD"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class ConsolidationEntry(TenantModel):
    """Consolidation-book entry (balanced, presentation currency). Append-only (DB trigger)."""

    run = models.ForeignKey(ConsolidationRun, on_delete=models.PROTECT, related_name="entries")
    group_id = models.UUIDField()
    entry_no = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=EntryKind.choices)
    period = models.CharField(max_length=7)
    posting_date = models.DateField()
    memo = models.CharField(max_length=500, blank=True, default="")
    currency = CurrencyField(default="COP")
    total_debit = AmountField(default=ZERO)
    total_credit = AmountField(default=ZERO)
    reversal_of = models.UUIDField(null=True, blank=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "consolidation_entry"
        constraints = [
            models.UniqueConstraint(fields=["run", "entry_no"], name="consolidation_entry_no_uq"),
            models.CheckConstraint(
                condition=models.Q(total_debit=models.F("total_credit")), name="consolidation_entry_balanced_ck"
            ),
        ]


class ConsolidationEntryLine(TenantModel):
    entry = models.ForeignKey(ConsolidationEntry, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveIntegerField()
    company_id = models.UUIDField(null=True, blank=True)
    group_account = models.CharField(max_length=20)
    debit = AmountField(default=ZERO)
    credit = AmountField(default=ZERO)
    memo = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "consolidation_entry_line"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0), name="consolidation_line_nonneg_ck"
            ),
            models.CheckConstraint(
                condition=models.Q(debit=0) | models.Q(credit=0), name="consolidation_line_one_side_ck"
            ),
        ]
