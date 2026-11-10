"""Inventory core models (P09a): masters, stock ledger, quants, valuation, reservations, transfers.

Rules enforced here and in the migrations (DB is the second line of defense):

* every table is tenant + company scoped (forced RLS, composite FK to ``iam_company``);
* ``Warehouse.branch_id`` is required and references a branch of the same company
  (composite FK ``(tenant_id, company_id, branch_id) → iam_branch``, F-ACC-BRANCH);
* a ``Location`` always belongs to one warehouse of the same company and its parent is in the
  same warehouse (composite FK ``(warehouse_id, parent_id) → (warehouse_id, id)``); a DB
  trigger refuses cycles;
* ``StockMove`` rows are append-only once ``posted`` (state DONE) — corrections are new moves;
* quantities are NUMERIC(20,6) in the item's base UoM (D-013); unit costs NUMERIC(24,8);
  values NUMERIC(20,4).

Sub-feature parts (P09b issues/barcodes, P09c bom/transformations) add their models in
``inventory/<part>/models.py``; they are imported at the end of this module so Django registers
them under the ``inventory`` app (see ``docs/handoffs/P09a.md`` § Extension points).
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import ClassVar

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from kernel.fields import AmountField, QtyField, UnitCostField
from kernel.models import AuditedModel, CompanyModel, ImmutableModel


class Status(models.TextChoices):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


# --------------------------------------------------------------------------- masters


class Uom(CompanyModel, AuditedModel):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    precision = models.PositiveSmallIntegerField(default=0)  # decimals allowed (0..6)
    category = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_uom"
        constraints = [
            models.UniqueConstraint(Lower("code"), "tenant_id", "company_id", name="inventory_uom_code_uq"),
            models.CheckConstraint(condition=Q(precision__lte=6), name="inventory_uom_precision_ck"),
        ]


class Item(CompanyModel, AuditedModel):
    class ItemType(models.TextChoices):
        STOCKABLE = "STOCKABLE"
        CONSUMABLE = "CONSUMABLE"
        SERVICE = "SERVICE"
        KIT = "KIT"

    class Valuation(models.TextChoices):
        AVERAGE = "AVERAGE"
        FIFO = "FIFO"

    class Tracking(models.TextChoices):
        NONE = "NONE"
        LOT = "LOT"
        SERIAL = "SERIAL"

    code = models.CharField(max_length=60)
    name = models.CharField(max_length=200)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.STOCKABLE)
    base_uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    category = models.CharField(max_length=60, blank=True, default="")
    valuation_method = models.CharField(max_length=10, choices=Valuation.choices, default=Valuation.AVERAGE)
    tracking = models.CharField(max_length=10, choices=Tracking.choices, default=Tracking.NONE)
    sales_tax_code = models.CharField(max_length=40, null=True, blank=True)
    purchase_tax_code = models.CharField(max_length=40, null=True, blank=True)
    gtin = models.CharField(max_length=14, null=True, blank=True)
    default_bin = models.ForeignKey("Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    standard_cost = UnitCostField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_item"
        constraints = [
            models.UniqueConstraint(Lower("code"), "tenant_id", "company_id", name="inventory_item_code_uq"),
        ]


class Presentation(CompanyModel, AuditedModel):
    """Packaging identity of an item (D-013): exact factor to the base UoM, own barcode and precision."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="presentations")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    uom = models.CharField(max_length=20)  # packaging unit label (caja, bolsa)
    factor = QtyField()  # base units per package, > 0
    precision = models.PositiveSmallIntegerField(default=0)  # decimals of packages (0 = whole packages)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    is_default_purchase = models.BooleanField(default=False)
    is_default_sale = models.BooleanField(default=False)
    sellable = models.BooleanField(default=True)
    purchasable = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_presentation"
        constraints = [
            models.UniqueConstraint(Lower("code"), "item", name="inventory_presentation_code_uq"),
            models.UniqueConstraint(
                "tenant_id", "company_id", "barcode", condition=Q(barcode__isnull=False),
                name="inventory_presentation_barcode_uq",
            ),
            models.CheckConstraint(condition=Q(factor__gt=0), name="inventory_presentation_factor_ck"),
            models.CheckConstraint(condition=Q(precision__lte=6), name="inventory_presentation_precision_ck"),
        ]


class Warehouse(CompanyModel, AuditedModel):
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"warehouse_ids": "id", "branch_ids": "branch_id"}

    class ValuationScope(models.TextChoices):
        COMPANY = "COMPANY"
        WAREHOUSE = "WAREHOUSE"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    branch_id = models.UUIDField()  # composite FK → iam_branch (same company), migration 0002
    address = models.CharField(max_length=255, blank=True, default="")
    municipality_code = models.CharField(max_length=5, null=True, blank=True)
    allow_negative = models.BooleanField(default=False)  # D-032
    valuation_scope = models.CharField(max_length=10, choices=ValuationScope.choices, default=ValuationScope.COMPANY)
    root_location = models.ForeignKey("Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    transit_location = models.ForeignKey(
        "Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_warehouse"
        constraints = [
            models.UniqueConstraint(Lower("code"), "tenant_id", "company_id", name="inventory_warehouse_code_uq"),
            models.UniqueConstraint(fields=["tenant_id", "company_id", "id"], name="inventory_warehouse_company_uq"),
        ]


class Location(CompanyModel, AuditedModel):
    class LocationType(models.TextChoices):
        VIEW = "VIEW"
        INTERNAL = "INTERNAL"
        TRANSIT = "TRANSIT"
        SCRAP = "SCRAP"

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="locations")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    location_type = models.CharField(max_length=10, choices=LocationType.choices)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    capacity = QtyField(null=True, blank=True)
    allow_mixed_lots = models.BooleanField(default=True)
    path = models.CharField(max_length=1000, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_location"
        constraints = [
            models.UniqueConstraint(Lower("code"), "warehouse", name="inventory_location_code_uq"),
            models.UniqueConstraint(fields=["warehouse", "id"], name="inventory_location_wh_id_uq"),
            models.CheckConstraint(condition=~Q(parent=models.F("id")), name="inventory_location_not_self_ck"),
        ]


class PutawayRule(CompanyModel, AuditedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="putaway_rules")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    category = models.CharField(max_length=60, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    priority = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_putaway_rule"
        constraints = [
            models.CheckConstraint(
                condition=Q(item__isnull=False) | Q(category__isnull=False), name="inventory_putaway_target_ck"
            ),
        ]


class Lot(CompanyModel, AuditedModel):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="lots")
    code = models.CharField(max_length=60)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_lot"
        constraints = [models.UniqueConstraint(Lower("code"), "item", name="inventory_lot_code_uq")]


class Serial(CompanyModel, AuditedModel):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="serials")
    code = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "inventory_serial"
        constraints = [models.UniqueConstraint(Lower("code"), "item", name="inventory_serial_code_uq")]


# --------------------------------------------------------------------------- stock ledger


class StockMove(CompanyModel, ImmutableModel):
    """One movement ``from_location → to_location`` in base qty. Append-only once DONE (``posted``)."""

    class Kind(models.TextChoices):
        RECEIPT = "RECEIPT"
        ISSUE = "ISSUE"
        INTERNAL = "INTERNAL"
        TRANSFER_OUT = "TRANSFER_OUT"
        TRANSFER_IN = "TRANSFER_IN"
        RETURN = "RETURN"
        REPACK = "REPACK"

    class State(models.TextChoices):
        DRAFT = "draft"
        RESERVED = "reserved"
        DONE = "done"
        CANCELLED = "cancelled"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    state = models.CharField(max_length=10, choices=State.choices, default=State.DONE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")  # scoping warehouse
    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    qty_packages = QtyField(null=True, blank=True)
    quantity = QtyField()  # base UoM, > 0
    uom = models.CharField(max_length=20)
    unit_cost = UnitCostField(default=0)
    value = AmountField(default=0)  # signed effect on valuation (+ in, − out, 0 internal)
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    source_number = models.CharField(max_length=60, blank=True, default="")
    line_ref = models.CharField(max_length=60)
    original_move = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    move_date = models.DateField()
    done_at = models.DateTimeField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "inventory_stock_move"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "source_type", "source_id", "line_ref", "kind"],
                name="inventory_move_source_uq",
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_move_qty_ck"),
            models.CheckConstraint(
                condition=Q(from_location__isnull=False) | Q(to_location__isnull=False),
                name="inventory_move_endpoint_ck",
            ),
        ]
        indexes = [models.Index(fields=["tenant_id", "company_id", "item", "move_date"], name="inventory_move_item_ix")]


class StockQuant(CompanyModel):
    """Materialized on-hand / reserved per (item, location, lot) in base qty, updated in-line under row lock."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    on_hand = QtyField(default=0)
    reserved = QtyField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "inventory_stock_quant"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "location", "lot"], name="inventory_quant_uq", nulls_distinct=False
            ),
            models.CheckConstraint(condition=Q(reserved__gte=0), name="inventory_quant_reserved_ck"),
        ]


class PackagedQuant(CompanyModel):
    """Sub-ledger of :class:`StockQuant` per presentation: Σ(qty_packages × factor) ≤ quant.on_hand (D-013)."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    qty_packages = QtyField(default=0)
    reserved_packages = QtyField(default=0)

    class Meta:
        db_table = "inventory_packaged_quant"
        constraints = [
            models.UniqueConstraint(
                fields=["presentation", "location", "lot"], name="inventory_packaged_uq", nulls_distinct=False
            ),
            models.CheckConstraint(condition=Q(qty_packages__gte=0), name="inventory_packaged_qty_ck"),
            models.CheckConstraint(
                condition=Q(reserved_packages__gte=0) & Q(reserved_packages__lte=models.F("qty_packages")),
                name="inventory_packaged_reserved_ck",
            ),
        ]


class Reservation(CompanyModel, AuditedModel):
    class State(models.TextChoices):
        ACTIVE = "ACTIVE"
        RELEASED = "RELEASED"
        CONSUMED = "CONSUMED"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    qty_packages = QtyField(null=True, blank=True)
    quantity = QtyField()  # base qty
    source_type = models.CharField(max_length=80)
    source_id = models.UUIDField()
    line_ref = models.CharField(max_length=60)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)

    class Meta:
        db_table = "inventory_reservation"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "company_id", "source_type", "source_id", "line_ref"],
                condition=Q(state="ACTIVE"),
                name="inventory_reservation_active_uq",
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_reservation_qty_ck"),
        ]


class ItemValuation(CompanyModel):
    """Lockable valuation row per (company, item) — company-wide scope (ADR-008 default)."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=10, choices=Item.Valuation.choices)
    qty = QtyField(default=0)
    value = AmountField(default=0)
    avg_cost = UnitCostField(default=0)
    last_cost = UnitCostField(default=0)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "inventory_item_valuation"
        constraints = [models.UniqueConstraint(fields=["tenant_id", "company_id", "item"], name="inventory_val_uq")]


class ValuationLayer(CompanyModel):
    """Receipt layer (FIFO) / audit trail of valuation changes; remaining_* consumed oldest-first."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    move = models.ForeignKey(StockMove, on_delete=models.PROTECT, related_name="+")
    qty = QtyField()
    unit_cost = UnitCostField()
    value = AmountField()
    remaining_qty = QtyField(default=0)
    remaining_value = AmountField(default=0)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "inventory_valuation_layer"
        indexes = [models.Index(fields=["tenant_id", "company_id", "item", "created_at"], name="inventory_layer_item_ix")]


# --------------------------------------------------------------------------- transfers


class Transfer(CompanyModel, AuditedModel):
    class TransferStatus(models.TextChoices):
        DRAFT = "DRAFT"
        SUBMITTED = "SUBMITTED"
        APPROVED = "APPROVED"
        SHIPPED = "SHIPPED"
        RECEIVED = "RECEIVED"
        CANCELLED = "CANCELLED"

    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"warehouse_ids": "from_warehouse_id"}

    number = models.CharField(max_length=40, blank=True, default="")
    document_date = models.DateField()
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    transit_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=TransferStatus.choices, default=TransferStatus.DRAFT)
    notes = models.CharField(max_length=500, blank=True, default="")
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    requisition_type = models.CharField(max_length=80, blank=True, default="")
    requisition_id = models.UUIDField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    ship_entry_id = models.UUIDField(null=True, blank=True)
    receive_entry_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "inventory_transfer"
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_warehouse=models.F("to_warehouse")), name="inventory_transfer_two_wh_ck"
            ),
        ]


class TransferLine(CompanyModel):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    qty_packages = QtyField(null=True, blank=True)
    quantity = QtyField()
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    unit_cost = UnitCostField(null=True, blank=True)
    value = AmountField(null=True, blank=True)

    class Meta:
        db_table = "inventory_transfer_line"
        constraints = [
            models.UniqueConstraint(fields=["transfer", "line_no"], name="inventory_transfer_line_uq"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_transfer_line_qty_ck"),
        ]


# --------------------------------------------------------------------------- extension parts (P09b / P09c)

EXTENSION_PARTS = ("issues", "barcodes", "bom", "transformations")

for _part in EXTENSION_PARTS:
    if importlib.util.find_spec(f"inventory.{_part}") is not None and importlib.util.find_spec(
        f"inventory.{_part}.models"
    ) is not None:
        importlib.import_module(f"inventory.{_part}.models")
