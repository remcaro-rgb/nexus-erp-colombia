"""POS data model (P12, ADR-015: every sale is final only after the server checkout transaction commits).

Money NUMERIC(20,4), quantities NUMERIC(20,6), unit prices NUMERIC(20,6), unit costs NUMERIC(24,8),
percentages NUMERIC(9,6) (C2-2, D-001). Every table carries ``tenant_id`` + ``company_id`` with forced row-level
security (migration 0002). ``PosConfig.branch_id`` references a branch of the SAME company through the composite
FK ``(tenant_id, company_id, branch_id) → iam_branch`` (F-ACC-BRANCH, T-ACC-BRANCH-04).

Posted tickets, ticket lines, tenders, returns, return lines and cash movements are immutable (``posted`` flag + DB
trigger): corrections are returns / reversal movements, never updates or deletes. A ``DraftCart`` exists only on
the client (unposted, no number); a server-side DRAFT ticket is a parked cart (no number, no stock, no GL).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models

from kernel.fields import AmountField, CurrencyField, PctField, QtyField, UnitCostField
from kernel.models import AuditedModel, CompanyModel, ImmutableModel


class PriceField(QtyField):
    """Unit price NUMERIC(20,6) (D-001)."""


TENDER_TYPES = ("CASH", "CARD", "TRANSFER", "QR", "VOUCHER")


class PosConfig(CompanyModel, AuditedModel):
    """Counter configuration: branch (required, same company), warehouse/bin, price list, tenders, DEE range."""

    name = models.CharField(max_length=200)
    active = models.BooleanField(default=True)
    branch_id = models.UUIDField()  # composite FK → iam_branch (same company), migration 0002
    warehouse_id = models.UUIDField()
    location_id = models.UUIDField(null=True, blank=True)  # POS bin (F-INV-LOC); None = putaway/FEFO pick
    price_list_id = models.UUIDField(null=True, blank=True)
    numbering_range_id = models.UUIDField(null=True, blank=True)  # kernel.sequences fiscal row (DEE POS range)
    allowed_tenders = models.JSONField(default=list)
    receipt_profile = models.JSONField(default=dict)  # printer profile (ESC/POS width, copies) — [A] hardware
    cash_limit = AmountField(null=True, blank=True)  # cash in drawer above → warning on the session
    allow_returns = models.BooleanField(default=True)
    blind_count = models.BooleanField(default=True)
    default_party_id = models.UUIDField(null=True, blank=True)  # None = consumidor final

    class Meta:
        db_table = "pos_config"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "name"], name="pos_config_name_uq"),
        ]


class Terminal(CompanyModel, AuditedModel):
    """Registered device. No pre-allocated number blocks: numbers are assigned server-side at commit."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    config = models.ForeignKey(PosConfig, on_delete=models.PROTECT, related_name="terminals")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    device_fingerprint = models.CharField(max_length=255, blank=True, default="")  # sha256 of the device key
    registered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pos_terminal"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "code"], name="pos_terminal_code_uq"),
        ]


class CashSession(CompanyModel, AuditedModel):
    """Cash session (turno): opening float, expected vs counted by tender, difference posting, Z report."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        CLOSING = "CLOSING"
        CLOSED = "CLOSED"

    number = models.CharField(max_length=40)
    terminal = models.ForeignKey(Terminal, on_delete=models.PROTECT, related_name="sessions")
    config = models.ForeignKey(PosConfig, on_delete=models.PROTECT, related_name="sessions")
    cashier_id = models.UUIDField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    business_date = models.DateField()
    opening_cash = AmountField(default=Decimal(0))
    expected = models.JSONField(default=dict)  # tender → amount (filled at close)
    counted = models.JSONField(default=dict)  # tender → amount
    denominations = models.JSONField(default=dict)  # cash count by denomination
    expected_cash = AmountField(null=True, blank=True)
    counted_cash = AmountField(null=True, blank=True)
    difference = AmountField(null=True, blank=True)  # counted − expected (cash)
    deposit_amount = AmountField(default=Decimal(0))
    blind = models.BooleanField(default=True)
    journal_entry_ids = models.JSONField(default=list)
    z_report = models.JSONField(default=dict)

    class Meta:
        db_table = "pos_session"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="pos_session_number_uq"),
            models.UniqueConstraint(
                fields=["terminal"], condition=~models.Q(status="CLOSED"), name="pos_session_one_open_uq"
            ),
            models.CheckConstraint(condition=models.Q(opening_cash__gte=0), name="pos_session_opening_ck"),
        ]


class Ticket(CompanyModel, AuditedModel, ImmutableModel):
    """POS ticket. DRAFT = parked cart (no number); POSTED only through ``tickets:checkout``."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"
        CANCELLED = "CANCELLED"

    class FiscalDocType(models.TextChoices):
        DEE_POS = "DEE_POS"
        FE = "FE"

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="tickets")
    client_uuid = models.UUIDField()
    number = models.CharField(max_length=40, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    document_date = models.DateField()
    cashier_id = models.UUIDField()
    branch_id = models.UUIDField()
    warehouse_id = models.UUIDField()
    party_id = models.UUIDField(null=True, blank=True)  # None = consumidor final
    currency = CurrencyField(default="COP")
    notes = models.TextField(blank=True, default="")
    subtotal = AmountField(default=Decimal(0))
    tax_total = AmountField(default=Decimal(0))
    total = AmountField(default=Decimal(0))
    change_due = AmountField(default=Decimal(0))
    cost_total = AmountField(default=Decimal(0))  # confidential
    fiscal_doc_type = models.CharField(max_length=10, choices=FiscalDocType.choices, default=FiscalDocType.DEE_POS)
    fiscal_status = models.CharField(max_length=20, default="PENDING")
    sequence_code = models.CharField(max_length=40, blank=True, default="")
    journal_entry_id = models.UUIDField(null=True, blank=True)
    tax_snapshot = models.JSONField(default=dict)
    draft_payload = models.JSONField(default=dict)  # parked cart (DRAFT only)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pos_ticket"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "client_uuid"], name="pos_ticket_client_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "number"],
                condition=models.Q(number__isnull=False),
                name="pos_ticket_number_uq",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="POSTED") | models.Q(number__isnull=False),
                name="pos_ticket_posted_number_ck",
            ),
        ]


class TicketLine(CompanyModel, ImmutableModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.UUIDField()
    presentation_id = models.UUIDField(null=True, blank=True)
    barcode = models.CharField(max_length=80, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    quantity = QtyField()  # sold units (packages when ``presentation_id`` is set, else base units)
    factor = QtyField(default=Decimal(1))  # base units per sold unit (D-013)
    base_qty = QtyField()  # canonical quantity issued (quantity × factor)
    unit_price = PriceField()  # per sold unit, before discount and tax
    discount_pct = PctField(default=Decimal(0))
    amount = AmountField()  # net of discount, before tax
    tax_code = models.CharField(max_length=40, blank=True, default="")
    tax_amount = AmountField(default=Decimal(0))
    cost_value = AmountField(default=Decimal(0))  # confidential (issue cost, base qty)
    unit_cost = UnitCostField(default=Decimal(0))  # confidential (cost per base unit at issue)
    price_source = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "pos_ticket_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["ticket", "line_no"], name="pos_ticket_line_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="pos_ticket_line_qty_ck"),
        ]


class Tender(CompanyModel, ImmutableModel):
    """One payment of a ticket (mixed tenders = several rows); ``amount`` is what settles the ticket."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="tenders")
    line_no = models.PositiveIntegerField()
    tender_type = models.CharField(max_length=10)
    amount = AmountField()
    tendered = AmountField()  # cash handed over (≥ amount); others = amount
    reference = models.CharField(max_length=80, blank=True, default="")  # external card terminal / QR reference

    class Meta:
        db_table = "pos_tender"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["ticket", "line_no"], name="pos_tender_uq"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="pos_tender_amount_ck"),
        ]


class CashMovement(CompanyModel, AuditedModel, ImmutableModel):
    """Pay-in / pay-out of the drawer with a reason (posted in-line)."""

    class Type(models.TextChoices):
        IN = "IN"
        OUT = "OUT"

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="movements")
    number = models.CharField(max_length=40)
    movement_type = models.CharField(max_length=3, choices=Type.choices)
    amount = AmountField()
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default="POSTED")
    journal_entry_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "pos_cash_movement"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="pos_cash_movement_uq"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="pos_cash_movement_amount_ck"),
        ]


class PosReturn(CompanyModel, AuditedModel, ImmutableModel):
    """Return referencing the original ticket: refund in the session, stock back at the ORIGINAL cost."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"
        CANCELLED = "CANCELLED"

    number = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="returns")
    original = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="returns")
    document_date = models.DateField()
    refund_tender = models.CharField(max_length=10, default="CASH")
    reason = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    subtotal = AmountField(default=Decimal(0))
    tax_total = AmountField(default=Decimal(0))
    total = AmountField(default=Decimal(0))
    cost_total = AmountField(default=Decimal(0))  # confidential
    journal_entry_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "pos_return"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "number"], name="pos_return_number_uq"),
        ]


class ReturnLine(CompanyModel, ImmutableModel):
    pos_return = models.ForeignKey(PosReturn, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    ticket_line = models.ForeignKey(TicketLine, on_delete=models.PROTECT, related_name="+")
    quantity = QtyField()  # sold units returned
    base_qty = QtyField()
    amount = AmountField()
    tax_amount = AmountField(default=Decimal(0))
    cost_value = AmountField(default=Decimal(0))  # confidential: original unit cost × base qty

    class Meta:
        db_table = "pos_return_line"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["pos_return", "line_no"], name="pos_return_line_uq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="pos_return_line_qty_ck"),
        ]


class InvoiceRequest(CompanyModel, AuditedModel):
    """Buyer request to convert a posted POS ticket into an electronic invoice (R-FE-03 buyer data).

    The posted ticket stays immutable; P15 issues the FE from the outbox event and handles the DEE per the annex
    (whether an issued DEE needs an annulment note is an annex rule — flagged B-06, see docs/handoffs/P12.md).
    """

    ticket = models.OneToOneField(Ticket, on_delete=models.PROTECT, related_name="invoice_request")
    buyer = models.JSONField(default=dict)  # restricted-pii: id type/number, name, e-mail, address
    status = models.CharField(max_length=20, default="REQUESTED")

    class Meta:
        db_table = "pos_invoice_request"
