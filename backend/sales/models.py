"""Sales / AR data model (P10a core: party master, pricing F-SAL-PRICING, lead-to-cash documents, AR).

Money NUMERIC(20,4), quantities NUMERIC(20,6), unit prices NUMERIC(20,6), unit costs NUMERIC(24,8),
FX NUMERIC(24,10), percentages NUMERIC(9,6) (C2-2, D-001). Every table carries ``tenant_id`` + ``company_id``
with forced row-level security (migration 0002). Posted deliveries, invoices, notes, receipts and write-offs
are immutable (``posted`` flag + DB trigger): corrections go through notes/reversal documents, never deletion.
The mutable AR balance lives in :class:`ArOpenItem` (sub-ledger; GL AR = Σ open items in COP).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models

from kernel.fields import AmountField, CurrencyField, FxRateField, PctField, QtyField
from kernel.models import AuditedModel, CompanyModel, ImmutableModel


class PriceField(QtyField):
    """Unit price NUMERIC(20,6) (D-001)."""


# --------------------------------------------------------------------------- party master (PartyService)


class Party(CompanyModel, AuditedModel):
    """Third party shared by sales/purchasing/HR (roles customer/vendor/employee/other)."""

    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"salesperson_ids": "salesperson_id"}

    class PersonType(models.TextChoices):
        NATURAL = "NATURAL"
        JURIDICA = "JURIDICA"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        INACTIVE = "INACTIVE"
        MERGED = "MERGED"

    code = models.CharField(max_length=40)
    legal_name = models.CharField(max_length=250)
    trade_name = models.CharField(max_length=250, null=True, blank=True)
    nit = models.CharField(max_length=20)  # digits only (normalized)
    dv = models.CharField(max_length=1, blank=True, default="")
    person_type = models.CharField(max_length=10, choices=PersonType.choices)
    roles = models.JSONField(default=list)
    email = models.CharField(max_length=254, null=True, blank=True)
    einvoice_email = models.CharField(max_length=254, null=True, blank=True)
    phone = models.CharField(max_length=40, null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True)
    municipality_code = models.CharField(max_length=5, null=True, blank=True)  # DANE code (P06 data)
    tax_profile_id = models.UUIDField(null=True, blank=True)  # P06 tax profile reference
    credit_limit = AmountField(null=True, blank=True)  # COP; null = no limit configured
    payment_terms_days = models.PositiveIntegerField(null=True, blank=True)
    price_list = models.ForeignKey("PriceList", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    customer_group = models.CharField(max_length=40, blank=True, default="")
    salesperson_id = models.UUIDField(null=True, blank=True)
    territory = models.CharField(max_length=60, blank=True, default="")
    consent_ref = models.UUIDField(null=True, blank=True)  # habeas data consent record (Ley 1581)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    merged_into = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "sales_party"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="sales_party_code_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "nit"],
                condition=~models.Q(status="MERGED"),
                name="sales_party_nit_uq",
            ),
        ]


class SalesSettings(CompanyModel, AuditedModel):
    """Per-company defaults: discount limit (D-024), credit control mode, AR terms."""

    class CreditMode(models.TextChoices):
        BLOCK = "BLOCK"
        APPROVAL = "APPROVAL"

    default_discount_limit_pct = PctField(default=Decimal(0))
    credit_mode = models.CharField(max_length=10, choices=CreditMode.choices, default=CreditMode.APPROVAL)
    default_payment_terms_days = models.PositiveIntegerField(default=30)

    class Meta:
        db_table = "sales_settings"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id"], name="sales_settings_company_uq"),
        ]


class DiscountLimit(CompanyModel, AuditedModel):
    """Maximum discount a principal may grant by manual override without approval (D-024)."""

    principal_id = models.UUIDField()
    max_discount_pct = PctField()

    class Meta:
        db_table = "sales_discount_limit"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "principal_id"], name="sales_discount_limit_uq"),
        ]


# --------------------------------------------------------------------------- pricing (F-SAL-PRICING)


class ItemSalesPrice(CompanyModel, AuditedModel):
    """Item base price (the lowest precedence level of D-024), per currency and validity."""

    item_id = models.UUIDField()
    currency = CurrencyField()
    base_price = PriceField()
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    category = models.CharField(max_length=60, blank=True, default="")
    tax_code = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        db_table = "sales_item_price"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "item_id", "currency", "valid_from"], name="sales_item_price_uq"
            ),
            models.CheckConstraint(condition=models.Q(base_price__gte=0), name="sales_item_price_ck"),
        ]


class PriceList(CompanyModel, AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        INACTIVE = "INACTIVE"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    currency = CurrencyField()
    customer_group = models.CharField(max_length=40, blank=True, default="")  # "" = any group
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "sales_price_list"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="sales_price_list_code_uq"),
        ]


class PriceListLine(CompanyModel):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="lines")
    item_id = models.UUIDField()
    min_qty = QtyField(default=Decimal(0))  # quantity break (base UoM)
    unit_price = PriceField()

    class Meta:
        db_table = "sales_price_list_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["price_list", "item_id", "min_qty"], name="sales_price_list_line_uq"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="sales_price_list_line_ck"),
        ]


class PriceFormula(CompanyModel, AuditedModel):
    """Binding of an ACTIVE ``workflow`` pricing formula (domain ``pricing``) to a scope (D-024 level 2)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        INACTIVE = "INACTIVE"

    code = models.CharField(max_length=60)
    name = models.CharField(max_length=200, blank=True, default="")
    formula_id = models.UUIDField()  # workflow.Formula (any version of the code)
    formula_code = models.CharField(max_length=80)
    #: {item_id, category, customer_group, customer_id, currency, channel} — absent key = any.
    scope = models.JSONField(default=dict)
    priority = models.IntegerField(default=100)  # higher wins
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "sales_price_formula"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="sales_price_formula_code_uq"),
        ]


# --------------------------------------------------------------------------- documents


class OverrideStatus(models.TextChoices):
    NONE = "NONE"
    WITHIN_LIMIT = "WITHIN_LIMIT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SalesDocument(CompanyModel, AuditedModel):
    #: Row policy "own customers" (T-P10-12): role assignments scoped by ``salesperson_ids``.
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"salesperson_ids": "salesperson_id"}

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))  # COP per unit of the document currency at document date
    channel = models.CharField(max_length=20, default="internal")
    salesperson_id = models.UUIDField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    subtotal = AmountField(default=Decimal(0))
    tax_total = AmountField(default=Decimal(0))
    total = AmountField(default=Decimal(0))
    approval_request_id = models.UUIDField(null=True, blank=True)  # pending price-override approval
    source = models.JSONField(null=True, blank=True)  # DocumentRef of the origin document

    class Meta:
        abstract = True


class SalesLineBase(CompanyModel):
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField()
    uom = models.CharField(max_length=20, default="UND")
    unit_price = PriceField()
    discount_pct = PctField(default=Decimal(0))
    amount = AmountField(default=Decimal(0))  # net of discount, before tax, document currency
    tax_code = models.CharField(max_length=40, blank=True, default="")
    warehouse_id = models.UUIDField(null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)
    price_source = models.CharField(max_length=10, default="BASE")  # OVERRIDE|FORMULA|LIST|BASE
    price_snapshot = models.JSONField(default=dict)  # D-024 snapshot (formula id+version+hash, inputs, trace)
    override_status = models.CharField(max_length=20, choices=OverrideStatus.choices, default=OverrideStatus.NONE)

    class Meta:
        abstract = True


class Quote(SalesDocument):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SENT = "SENT"
        ACCEPTED = "ACCEPTED"
        ORDERED = "ORDERED"
        CANCELLED = "CANCELLED"

    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    order_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "sales_quote"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_quote_number_uq"),
        ]


class QuoteLine(SalesLineBase):
    document = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="lines")

    class Meta:
        db_table = "sales_quote_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["document", "line_no"], name="sales_quote_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="sales_quote_line_qty_ck"),
        ]


class SalesOrder(SalesDocument):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"  # credit limit / overdue approval
        CONFIRMED = "CONFIRMED"
        PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
        DELIVERED = "DELIVERED"
        INVOICED = "INVOICED"
        CANCELLED = "CANCELLED"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    credit_approval_request_id = models.UUIDField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_order"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_order_number_uq"),
        ]


class SalesOrderLine(SalesLineBase):
    document = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    delivered_qty = QtyField(default=Decimal(0))
    invoiced_qty = QtyField(default=Decimal(0))
    reservation_ids = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "sales_order_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["document", "line_no"], name="sales_order_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="sales_order_line_qty_ck"),
        ]


class Delivery(CompanyModel, AuditedModel, ImmutableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        DONE = "DONE"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    warehouse_id = models.UUIDField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    cost_total = AmountField(default=Decimal(0))  # COP, confidential
    journal_entry_id = models.UUIDField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "sales_delivery"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_delivery_number_uq"),
        ]


class DeliveryLine(CompanyModel, ImmutableModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField()
    quantity = QtyField()
    lot_id = models.UUIDField(null=True, blank=True)
    cost_value = AmountField(default=Decimal(0))  # COP at issue (components for kits)
    components = models.JSONField(default=list, blank=True)  # kit explosion actually issued

    class Meta:
        db_table = "sales_delivery_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["delivery", "line_no"], name="sales_delivery_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="sales_delivery_line_qty_ck"),
        ]


class Invoice(SalesDocument, ImmutableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    due_date = models.DateField(null=True, blank=True)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    deliver_on_invoice = models.BooleanField(default=False)
    warehouse_id = models.UUIDField(null=True, blank=True)  # deliver-on-invoice warehouse
    branch_id = models.UUIDField(null=True, blank=True)
    cost_total = AmountField(default=Decimal(0))  # COGS posted with the invoice (deliver on invoice)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)
    tax_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "sales_invoice"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_invoice_number_uq"),
        ]


class InvoiceLine(SalesLineBase, ImmutableModel):
    document = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cost_value = AmountField(default=Decimal(0))
    components = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "sales_invoice_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["document", "line_no"], name="sales_invoice_line_no_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="sales_invoice_line_qty_ck"),
        ]


class SalesNote(CompanyModel, AuditedModel, ImmutableModel):
    """Credit note (``CREDIT``) or debit note (``DEBIT``) linked to a posted invoice (C1-5, annex reason codes)."""

    class Kind(models.TextChoices):
        CREDIT = "CREDIT"
        DEBIT = "DEBIT"

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"

    kind = models.CharField(max_length=6, choices=Kind.choices)
    number = models.CharField(max_length=60)
    document_date = models.DateField()
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="+")
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    reason = models.CharField(max_length=40)  # devolucion|anulacion|rebaja|descuento|ajuste_precio|otro
    reason_text = models.TextField(blank=True, default="")
    with_return = models.BooleanField(default=False)
    warehouse_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    subtotal = AmountField(default=Decimal(0))
    tax_total = AmountField(default=Decimal(0))
    total = AmountField(default=Decimal(0))
    cost_total = AmountField(default=Decimal(0))
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_note"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "kind", "number"], name="sales_note_number_uq"),
        ]


class SalesNoteLine(CompanyModel, ImmutableModel):
    note = models.ForeignKey(SalesNote, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True, default="")
    quantity = QtyField(default=Decimal(0))
    amount = AmountField()
    tax_code = models.CharField(max_length=40, blank=True, default="")
    cost_value = AmountField(default=Decimal(0))

    class Meta:
        db_table = "sales_note_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["note", "line_no"], name="sales_note_line_no_uq"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sales_note_line_amount_ck"),
        ]


class Receipt(CompanyModel, AuditedModel, ImmutableModel):
    """Customer receipt applied to AR open items (withholdings certified by the customer, shared/07 §1)."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    amount = AmountField()  # cash received (document currency)
    bank_role = models.CharField(max_length=40, default="BANK")
    withholdings = models.JSONField(default=list, blank=True)  # [{tax_code, amount}] certified by the customer
    applications = models.JSONField(default=list, blank=True)  # [{open_item_id, amount}]
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_receipt"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_receipt_number_uq"),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="sales_receipt_amount_ck"),
        ]


class ArOpenItem(CompanyModel, AuditedModel):
    """AR sub-ledger item created in the same unit of work as the invoice/debit note (GL AR = Σ balance_cop)."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        PARTIAL = "PARTIAL"
        PAID = "PAID"

    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    document_type = models.CharField(max_length=80)
    document_id = models.UUIDField()
    document_number = models.CharField(max_length=60)
    document_date = models.DateField()
    due_date = models.DateField()
    currency = CurrencyField()
    fx_rate = FxRateField(default=Decimal(1))
    amount = AmountField()
    balance = AmountField()
    amount_cop = AmountField()
    balance_cop = AmountField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "sales_ar_open_item"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "document_type", "document_id"], name="sales_ar_item_doc_uq"
            ),
            models.CheckConstraint(condition=models.Q(balance__gte=0), name="sales_ar_item_balance_ck"),
        ]


class ArApplication(CompanyModel):
    """Append-only application of a receipt/credit note/write-off to an open item (``reversed`` = unapplied)."""

    open_item = models.ForeignKey(ArOpenItem, on_delete=models.PROTECT, related_name="applications")
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    source_number = models.CharField(max_length=60, blank=True, default="")
    amount = AmountField()
    amount_cop = AmountField()
    applied_on = models.DateField()
    reversed_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "sales_ar_application"
        constraints: ClassVar = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sales_ar_application_amount_ck"),
        ]


class WriteOff(CompanyModel, AuditedModel, ImmutableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        POSTED = "POSTED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    open_item = models.ForeignKey(ArOpenItem, on_delete=models.PROTECT, related_name="+")
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    amount = AmountField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approval_request_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "sales_write_off"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_write_off_number_uq"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sales_write_off_amount_ck"),
        ]


class SalesReturn(CompanyModel, AuditedModel):
    """RMA: DRAFT → SUBMITTED → APPROVED → POSTED (posting creates the credit note with goods back at cost)."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        POSTED = "POSTED"

    number = models.CharField(max_length=60)
    document_date = models.DateField()
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="+")
    warehouse_id = models.UUIDField()
    lines = models.JSONField(default=list)  # [{invoice_line_id, quantity}]
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    approval_request_id = models.UUIDField(null=True, blank=True)
    credit_note_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "sales_return"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="sales_return_number_uq"),
        ]


__all__ = [
    "ArApplication",
    "ArOpenItem",
    "Delivery",
    "DeliveryLine",
    "DiscountLimit",
    "Invoice",
    "InvoiceLine",
    "ItemSalesPrice",
    "OverrideStatus",
    "Party",
    "PriceFormula",
    "PriceList",
    "PriceListLine",
    "Quote",
    "QuoteLine",
    "Receipt",
    "SalesNote",
    "SalesNoteLine",
    "SalesOrder",
    "SalesOrderLine",
    "SalesReturn",
    "SalesSettings",
    "WriteOff",
]
