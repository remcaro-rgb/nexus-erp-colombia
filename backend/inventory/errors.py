"""Inventory error codes (contracts/errors.yaml, module inventory; RFC 9457 via kernel.errors)."""

from __future__ import annotations

from typing import Any

from kernel.errors import Conflict, DomainError, ValidationFailed


class InventoryError(DomainError):
    """422 business error with a stable inventory code."""

    code, status, title = "inventory_error", 422, "Inventory rule violated"


class InventoryConflict(Conflict):
    code, title = "inventory_conflict", "Inventory conflict"


#: code → HTTP status (contracts/errors.yaml)
STATUS = {
    "stock_insufficient": 422,
    "reservation_conflict": 409,
    "lot_required": 422,
    "location_not_leaf": 422,
    "location_cross_warehouse": 422,
    "location_not_empty": 409,
    "presentation_factor_invalid": 422,
    "presentation_barcode_duplicate": 422,
    "packaged_exceeds_canonical": 422,
    "uom_precision_violation": 422,
    "transfer_cross_company": 422,
    "branch_cross_company": 422,
}


def error(code: str, detail: str, **extra: Any) -> DomainError:
    if STATUS.get(code, 422) == 409:
        return InventoryConflict(detail, code=code, **extra)
    return InventoryError(detail, code=code, **extra)


def validation(detail: str, field: str, reason: str = "invalid") -> ValidationFailed:
    return ValidationFailed(detail, pointer=f"/{field}", reason=reason)
