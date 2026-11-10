"""Purchasing / AP data model (P11a core, F-PUR-CYCLE; shared/07 §2).

Money NUMERIC(20,4), quantities NUMERIC(20,6), unit prices/costs NUMERIC(24,8), FX NUMERIC(24,10),
percentages NUMERIC(9,6) (C2-2, D-001). Every table carries ``tenant_id`` + ``company_id`` with forced
row-level security (migration 0002). Comparison snapshots are append-only (DB trigger); posted
receipts and vendor invoices are immutable (``posted`` flag + trigger): corrections go through
reversal documents, never deletion.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models

from kernel import clock
from kernel.fields import AmountField, CurrencyField, FxRateField, PctField, QtyField, UnitCostField
from kernel.models import AuditedModel, CompanyModel, ImmutableModel

# --------------------------------------------------------------------------- masters


class Supplier(CompanyModel, AuditedModel):
    """Vendor profile used by purchasing (party master: P10 ``PartyService``; ``party_id`` links it)."""

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=250)
    nit = models.CharField(max_length=25, blank=True, default="")
    party_id = models.UUIDField(null=True, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("3.00"))  # 0.00–5.00
    payment_terms_days = models.PositiveIntegerField(default=30)
    currency = CurrencyField()
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "purchasing_supplier"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="purchasing_supplier_code_uq"),
            models.CheckConstraint(
                condition=models.Q(rating__gte=0) & models.Q(rating__lte=5), name="purchasing_supplier_rating_ck"
            ),
        ]


class PurchasingSettings(CompanyModel, AuditedModel):
    """Per-company purchasing defaults (D-009 weights, tolerances, approval threshold)."""

    scoring_weights = models.JSONField(default=dict)
    receipt_tolerance_pct = PctField(default=Decimal(0))
    invoice_qty_tolerance_pct = PctField(default=Decimal(0))
    invoice_price_tolerance_pct = PctField(default=Decimal(0))
    po_auto_approve_limit = AmountField(default=Decimal(0))
    rfq_deadline_business_days = models.PositiveSmallIntegerField(default=5)

    class Meta:
        db_table = "purchasing_settings"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id"], name="purchasing_settings_company_uq"),
        ]


# --------------------------------------------------------------------------- RFQ / quotes


class Rfq(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SENT = "SENT"
        CLOSED = "CLOSED"
        EVALUATED = "EVALUATED"
        AWARD_PENDING_APPROVAL = "AWARD_PENDING_APPROVAL"
        AWARDED = "AWARDED"
        PO_CREATED = "PO_CREATED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    deadline = models.DateTimeField()
    currency = CurrencyField()
    notes = models.TextField(blank=True, default="")
    terms = models.TextField(blank=True, default="")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    invited_vendors = models.JSONField(default=list)  # supplier ids (str)
    scoring_weights = models.JSONField(default=dict)
    source = models.JSONField(null=True, blank=True)  # DocumentRef of the requisition (P11b)
    source_lines = models.JSONField(default=list, blank=True)  # requisition line ids (lineage)
    sent_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopen_count = models.PositiveSmallIntegerField(default=0)
    reopen_reason = models.TextField(blank=True, default="")
    awarded_quote_id = models.UUIDField(null=True, blank=True)
    award_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "purchasing_rfq"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_rfq_number_uq"),
        ]


class RfqLine(CompanyModel):
    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField()  # base UoM (D-013)
    uom = models.CharField(max_length=20)
    tax_code = models.CharField(max_length=40, blank=True, default="")
    dimensions = models.JSONField(default=dict, blank=True)
    source_line_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "purchasing_rfq_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["rfq", "line_no"], name="purchasing_rfq_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_rfq_line_qty_ck"),
        ]


class VendorQuote(CompanyModel, AuditedModel):
    class QuoteStatus(models.TextChoices):
        RECEIVED = "RECEIVED"
        LATE = "LATE"
        WITHDRAWN = "WITHDRAWN"
        DISQUALIFIED = "DISQUALIFIED"
        AWARDED = "AWARDED"

    class Channel(models.TextChoices):
        INTERNAL_ENTRY = "internal_entry"
        SUPPLIER_PORTAL = "supplier_portal"

    rfq = models.ForeignKey(Rfq, on_delete=models.PROTECT, related_name="quotes")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=60)
    document_date = models.DateField()
    received_at = models.DateTimeField(default=clock.now)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.INTERNAL_ENTRY)
    valid_until = models.DateField(null=True, blank=True)
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    fx_source = models.CharField(max_length=20, default="functional")  # functional | quote | ledger
    lead_time_days = models.PositiveIntegerField(default=0)
    payment_terms_days = models.PositiveIntegerField(default=0)
    freight_amount = AmountField(default=Decimal(0))  # header freight, quote currency, allocated by net value
    quote_status = models.CharField(max_length=20, choices=QuoteStatus.choices, default=QuoteStatus.RECEIVED)
    disqualified_reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    total = AmountField(default=Decimal(0))  # quote currency, net of discounts, before taxes
    landed_total_cop = AmountField(default=Decimal(0))  # confidential: requested qty × landed unit COP

    class Meta:
        db_table = "purchasing_vendor_quote"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_quote_number_uq"),
        ]


class VendorQuoteLine(CompanyModel):
    quote = models.ForeignKey(VendorQuote, on_delete=models.CASCADE, related_name="lines")
    rfq_line = models.ForeignKey(RfqLine, on_delete=models.PROTECT, related_name="+")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField()  # offered, in the quoted UoM / presentation
    uom = models.CharField(max_length=20)
    factor_to_base = UnitCostField(default=Decimal(1))  # exact factor quoted UoM → base UoM (D-013)
    presentation_id = models.UUIDField(null=True, blank=True)
    unit_price = UnitCostField()  # quote currency per quoted UoM
    discount_pct = PctField(default=Decimal(0))
    tax_code = models.CharField(max_length=40, blank=True, default="")
    tax_rate_pct = PctField(default=Decimal(0))
    tax_recoverable = models.BooleanField(default=True)
    freight_amount = AmountField(null=True, blank=True)  # explicit line freight (else header allocation)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "purchasing_vendor_quote_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["quote", "line_no"], name="purchasing_quote_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_quote_line_qty_ck"),
            models.CheckConstraint(condition=models.Q(factor_to_base__gt=0), name="purchasing_quote_line_factor_ck"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="purchasing_quote_line_price_ck"),
        ]


class QuoteAttachment(CompanyModel):
    """File attached to a vendor quote: stored through ``FileStore`` after the antivirus scan."""

    quote = models.ForeignKey(VendorQuote, on_delete=models.PROTECT, related_name="attachments")
    name = models.CharField(max_length=200)
    object_key = models.CharField(max_length=400)
    media_type = models.CharField(max_length=120)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(max_length=12)  # CLEAN (INFECTED files are refused, never stored)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        db_table = "purchasing_quote_attachment"


class QuoteComparison(CompanyModel):
    """Immutable comparison snapshot (append-only table): normalized lines, scores, recommendation."""

    rfq = models.ForeignKey(Rfq, on_delete=models.PROTECT, related_name="comparisons")
    sequence = models.PositiveIntegerField()
    weights = models.JSONField()
    snapshot = models.JSONField()
    recommended_quote_id = models.UUIDField(null=True, blank=True)
    explanation = models.TextField()
    created_at = models.DateTimeField(default=clock.now)
    created_by = models.UUIDField()

    class Meta:
        db_table = "purchasing_quote_comparison"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["rfq", "sequence"], name="purchasing_comparison_seq_uq"),
        ]


class Award(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        PENDING_APPROVAL = "PENDING_APPROVAL"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"

    rfq = models.ForeignKey(Rfq, on_delete=models.PROTECT, related_name="awards")
    comparison = models.ForeignKey(QuoteComparison, on_delete=models.PROTECT, related_name="+")
    recommended_quote_id = models.UUIDField(null=True, blank=True)
    differs_from_recommendation = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_APPROVAL)
    proposed_by = models.UUIDField()
    approval_request_id = models.UUIDField(null=True, blank=True)
    decided_by = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "purchasing_award"


class AwardLine(CompanyModel):
    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="lines")
    rfq_line = models.ForeignKey(RfqLine, on_delete=models.PROTECT, related_name="+")
    quote = models.ForeignKey(VendorQuote, on_delete=models.PROTECT, related_name="+")
    quote_line = models.ForeignKey(VendorQuoteLine, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "purchasing_award_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["award", "rfq_line"], name="purchasing_award_line_uq"),
        ]


# --------------------------------------------------------------------------- purchase orders


class PurchaseOrder(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        SENT = "SENT"
        ACKNOWLEDGED = "ACKNOWLEDGED"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
        RECEIVED = "RECEIVED"
        CLOSED = "CLOSED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="+")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True, default="")
    rfq = models.ForeignKey(Rfq, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    award = models.ForeignKey(Award, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    source = models.JSONField(null=True, blank=True)
    total = AmountField(default=Decimal(0))  # document currency, net before taxes
    submitted_by = models.UUIDField(null=True, blank=True)
    approved_by = models.UUIDField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "purchasing_order"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_order_number_uq"),
        ]


class PurchaseOrderLine(CompanyModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField()  # base UoM
    uom = models.CharField(max_length=20)
    unit_price = UnitCostField()  # document currency per base UoM, net of discounts
    tax_code = models.CharField(max_length=40, blank=True, default="")
    warehouse_id = models.UUIDField(null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)
    rfq_line = models.ForeignKey(RfqLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    received_qty = QtyField(default=Decimal(0))
    invoiced_qty = QtyField(default=Decimal(0))

    class Meta:
        db_table = "purchasing_order_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["order", "line_no"], name="purchasing_order_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_order_line_qty_ck"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="purchasing_order_line_price_ck"),
            models.CheckConstraint(
                condition=models.Q(received_qty__gte=0) & models.Q(invoiced_qty__gte=0),
                name="purchasing_order_line_progress_ck",
            ),
        ]

    @property
    def amount(self) -> Decimal:
        from kernel.money import q_amount

        return q_amount(self.quantity * self.unit_price)


# --------------------------------------------------------------------------- receipts


class Receipt(CompanyModel, AuditedModel, ImmutableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        VALIDATED = "VALIDATED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    warehouse_id = models.UUIDField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True, default="")
    total_cop = AmountField(default=Decimal(0))
    journal_entry_id = models.UUIDField(null=True, blank=True)
    stock_move_ids = models.JSONField(default=list, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "purchasing_receipt"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_receipt_number_uq"),
        ]


class ReceiptLine(CompanyModel, ImmutableModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    quantity = QtyField()  # base UoM
    unit_cost_cop = UnitCostField(default=Decimal(0))  # PO price in COP at the receipt date
    amount_cop = AmountField(default=Decimal(0))
    location_id = models.UUIDField(null=True, blank=True)
    lot_id = models.UUIDField(null=True, blank=True)
    presentation_id = models.UUIDField(null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "purchasing_receipt_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["receipt", "line_no"], name="purchasing_receipt_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_receipt_line_qty_ck"),
        ]


# --------------------------------------------------------------------------- vendor invoices / AP


class VendorInvoice(CompanyModel, AuditedModel, ImmutableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"
        CANCELLED = "CANCELLED"

    class MatchStatus(models.TextChoices):
        UNMATCHED = "UNMATCHED"
        MATCHED = "MATCHED"
        MISMATCH = "MISMATCH"

    number = models.CharField(max_length=60)
    vendor_invoice_number = models.CharField(max_length=60)
    document_date = models.DateField()
    due_date = models.DateField()
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    match_status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.UNMATCHED)
    match_result = models.JSONField(default=dict, blank=True)
    mismatch_approval_id = models.UUIDField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    subtotal = AmountField(default=Decimal(0))
    tax_total = AmountField(default=Decimal(0))
    withholding_total = AmountField(default=Decimal(0))
    total = AmountField(default=Decimal(0))  # payable (subtotal + taxes − withholdings), document currency
    journal_entry_id = models.UUIDField(null=True, blank=True)
    tax_snapshot = models.JSONField(default=dict, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "purchasing_vendor_invoice"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="purchasing_vinv_number_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "supplier", "vendor_invoice_number"],
                name="purchasing_vinv_supplier_number_uq",
            ),
        ]


class VendorInvoiceLine(CompanyModel, ImmutableModel):
    invoice = models.ForeignKey(VendorInvoice, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField()
    uom = models.CharField(max_length=20, default="UND")
    unit_price = UnitCostField()  # document currency
    amount = AmountField(default=Decimal(0))  # quantity × unit_price (document currency)
    tax_code = models.CharField(max_length=40, blank=True, default="")
    account_role = models.CharField(max_length=60, blank=True, default="")  # non-PO lines (default EXPENSE)
    grni_amount_cop = AmountField(default=Decimal(0))  # GRNI cleared at the receipt value (PO lines)
    variance_cop = AmountField(default=Decimal(0))  # invoice net COP − GRNI cleared (price difference)
    dimensions = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "purchasing_vendor_invoice_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["invoice", "line_no"], name="purchasing_vinv_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchasing_vinv_line_qty_ck"),
        ]


class ApOpenItem(CompanyModel, AuditedModel):
    """AP subledger item created in the same unit of work as the invoice posting (GL AP = Σ open items)."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        PARTIAL = "PARTIAL"
        PAID = "PAID"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="+")
    document_type = models.CharField(max_length=80)
    document_id = models.UUIDField()
    document_number = models.CharField(max_length=60)
    document_date = models.DateField()
    due_date = models.DateField()
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    amount = AmountField()  # document currency
    balance = AmountField()
    amount_cop = AmountField()
    balance_cop = AmountField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "purchasing_ap_open_item"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "document_type", "document_id"], name="purchasing_ap_item_doc_uq"
            ),
            models.CheckConstraint(condition=models.Q(balance__gte=0), name="purchasing_ap_item_balance_ck"),
        ]
