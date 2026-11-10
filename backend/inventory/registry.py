"""Registrations: field classifications (iam field policy), the warehouse branch reference, kernel services.

Cost and valuation fields are ``confidential`` (contract ``x-classification``): roles whose
``max_classification`` is ``internal`` (sales, POS, portal) never see them.
"""

from __future__ import annotations

from typing import Any

from kernel import services
from kernel.interfaces.stock import Inventory, StockService

COMMON = {"id": "internal", "tenant_id": "internal", "company_id": "internal", "version": "internal", "status": "internal"}

FIELDS: dict[str, dict[str, str]] = {
    "inventory.uom": {**COMMON, "code": "internal", "name": "internal", "precision": "internal", "category": "internal"},
    "inventory.item": {
        **COMMON, "code": "internal", "name": "internal", "base_uom": "internal", "item_type": "internal",
        "category": "internal", "valuation_method": "internal", "tracking": "internal", "sales_tax_code": "internal",
        "purchase_tax_code": "internal", "gtin": "internal", "default_bin_id": "internal", "standard_cost": "confidential",
    },
    "inventory.presentation": {
        **COMMON, "item_id": "internal", "code": "internal", "name": "internal", "factor": "internal", "uom": "internal",
        "barcode": "internal", "precision": "internal", "is_default_purchase": "internal", "is_default_sale": "internal",
    },
    "inventory.warehouse": {
        **COMMON, "code": "internal", "name": "internal", "branch_id": "internal", "allow_negative": "internal",
        "address": "internal", "root_location_id": "internal", "transit_location_id": "internal",
        "valuation_scope": "internal",
    },
    "inventory.location": {
        **COMMON, "warehouse_id": "internal", "code": "internal", "name": "internal", "parent_id": "internal",
        "location_type": "internal", "is_leaf": "internal", "barcode": "internal", "capacity": "internal",
        "path": "internal", "allow_mixed_lots": "internal",
    },
    "inventory.putaway_rule": {
        **COMMON, "warehouse_id": "internal", "item_id": "internal", "category": "internal", "location_id": "internal",
        "priority": "internal", "active": "internal",
    },
    "inventory.lot": {**COMMON, "item_id": "internal", "code": "internal", "expiry_date": "internal"},
    "inventory.move": {
        **COMMON, "kind": "internal", "item_id": "internal", "warehouse_id": "internal", "quantity": "internal",
        "qty_packages": "internal", "presentation_id": "internal", "from_location_id": "internal",
        "to_location_id": "internal", "lot_id": "internal", "unit_cost": "confidential", "value": "confidential",
        "source": "internal", "move_date": "internal", "journal_entry_id": "internal",
    },
    "inventory.quant": {
        **COMMON, "item_id": "internal", "warehouse_id": "internal", "location_id": "internal", "lot_id": "internal",
        "on_hand": "internal", "reserved": "internal", "available": "internal", "value": "confidential",
    },
    "inventory.availability": {
        "item_id": "internal", "warehouse_id": "internal", "location_id": "internal", "location_code": "internal",
        "location_type": "internal", "lot_id": "internal", "on_hand": "internal", "reserved": "internal",
        "available": "internal", "packaged_base": "internal", "loose": "internal", "packaged": "internal",
    },
    "inventory.transfer": {
        **COMMON, "number": "internal", "document_date": "internal", "from_warehouse_id": "internal",
        "to_warehouse_id": "internal", "transit_location_id": "internal", "notes": "internal", "lines": "internal",
        "source": "internal", "requisition": "internal", "total": "confidential", "ship_entry_id": "internal",
        "receive_entry_id": "internal", "shipped_at": "internal", "received_at": "internal",
    },
}  # fmt: skip


def register_fields() -> None:
    from iam.policy import fields as field_registry

    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)


def register_branch_reference() -> None:
    from iam.branches import services as branch_services

    branch_services.register_reference(
        branch_services.BranchReference(
            branch_services.WAREHOUSE_REFERENCE, "inventory_warehouse", active_sql="status = 'ACTIVE'"
        )
    )


def implementations() -> dict[Any, object]:
    from inventory.stock.service import SERVICE

    return {Inventory: SERVICE, StockService: SERVICE}


def register_services() -> None:
    for interface, impl in implementations().items():
        if services.optional(interface) is None:
            services.register(interface, impl)
