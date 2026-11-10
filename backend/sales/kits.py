"""Kit sales (shared/07 §3 "Kit sale: issue components; COGS = sum of component costs"; T-INV-BOM-03).

A sold item of type KIT is never issued itself: its ACTIVE ``KIT`` BOM (F-INV-BOM, owned by P09 in
``backend/inventory/bom/**``) is exploded for the sold quantity on the document date and every component is
issued at its current valuation through ``StockService.issue`` in the document's unit of work; the line's cost
(and the COGS posting) is the sum of the component values the stock service returns.

The explosion is reached through the :class:`KitExplosion` port registered in ``kernel.services`` (P09c
implements it over its BOM model — CCR P10a-2). Without an implementation, or without an active KIT BOM,
a kit sale fails closed (``bom_component_inactive``): it never silently issues the kit item itself.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel import services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import StockLine, StockService
from kernel.money import q_amount, q_qty
from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class KitComponent:
    item_id: uuid.UUID
    quantity: Decimal  # base UoM of the component, for the exploded (sold) quantity


@runtime_checkable
class KitExplosion(Protocol):
    def is_kit(self, company_id: uuid.UUID, item_id: uuid.UUID) -> bool: ...

    def explode(self, company_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal, on: dt.date) -> list[KitComponent]:
        """Components of the ACTIVE KIT BOM of ``item_id`` on ``on`` for ``quantity`` units (multi-level flattened)."""
        ...


def item_is_kit(company_id: uuid.UUID, item_id: uuid.UUID | None) -> bool:
    """True when the inventory item type is KIT (P09a ``Item.ItemType.KIT``) or the kit port says so."""
    if item_id is None:
        return False
    from django.apps import apps

    if apps.is_installed("inventory"):
        item = apps.get_model("inventory", "Item").objects.filter(company_id=company_id, id=item_id).first()
        if item is not None and item.item_type == "KIT":
            return True
    port = services.optional(KitExplosion)
    return bool(port is not None and port.is_kit(company_id, item_id))


def is_stocked(company_id: uuid.UUID, item_id: uuid.UUID | None) -> bool:
    """Stockable/consumable items move stock; services do not; kits move their components."""
    if item_id is None:
        return False
    from django.apps import apps

    if not apps.is_installed("inventory"):
        return True
    item = apps.get_model("inventory", "Item").objects.filter(company_id=company_id, id=item_id).first()
    if item is None:
        raise ValidationFailed("item_id is not an item of the company", field="item_id")
    return str(item.item_type) in ("STOCKABLE", "CONSUMABLE", "KIT")


def components(company_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal, on: dt.date) -> list[KitComponent]:
    port = services.optional(KitExplosion)
    if port is None:
        raise ValidationFailed(
            "kit sale needs the BOM explosion service (F-INV-BOM KIT); none is registered",
            field="item_id",
            code="bom_component_inactive",
        )
    parts = port.explode(company_id, item_id, quantity, on)
    if not parts or any(p.quantity <= 0 for p in parts):
        raise ValidationFailed(
            "the kit has no ACTIVE KIT BOM on the document date", field="item_id", code="bom_component_inactive"
        )
    return parts


@dataclass(frozen=True, slots=True)
class IssuedLine:
    line_ref: str
    value: Decimal  # COP cost issued for the line (sum of components for kits)
    components: list[dict[str, str]]
    move_ids: tuple[uuid.UUID, ...]


def _values_by_ref(result_move_ids: tuple[uuid.UUID, ...]) -> dict[str, Decimal] | None:
    """Issued value per stock ``line_ref`` from P09's moves (``ref#n`` bin splits folded into ``ref``)."""
    from django.apps import apps

    if not apps.is_installed("inventory") or not result_move_ids:
        return None
    rows = list(apps.get_model("inventory", "StockMove").objects.filter(id__in=result_move_ids).values_list("line_ref", "value"))
    if len(rows) != len(result_move_ids):
        return None
    out: dict[str, Decimal] = {}
    for ref, value in rows:
        key = str(ref).split("#", 1)[0]
        out[key] = out.get(key, Decimal(0)) + abs(Decimal(value))
    return out


def issue_lines(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    source: DocumentRef,
    lines: list[tuple[str, uuid.UUID, Decimal, uuid.UUID, uuid.UUID | None]],
    on: dt.date,
) -> dict[str, IssuedLine]:
    """Issue ``(line_ref, item_id, qty, warehouse_id, lot_id)`` lines; kits are exploded into components.

    ONE ``StockService.issue`` call for the whole document (the service is idempotent per source document and
    locks the valuations in its fixed order): kit components travel as ``<ref>.k<n>`` stock lines. The value of
    each document line is the sum of its stock lines' issued values (P09 moves); with a stock double that has
    no move rows the total is allocated by quantity.
    """
    if not lines:
        return {}
    stock_lines: list[StockLine] = []
    owners: dict[str, str] = {}
    detail: dict[str, list[tuple[str, uuid.UUID, Decimal]]] = {}
    for ref, item_id, qty, warehouse_id, lot_id in lines:
        if item_is_kit(company_id, item_id):
            parts = components(company_id, item_id, qty, on)
            for n, part in enumerate(sorted(parts, key=lambda p: p.item_id), start=1):
                sub_ref = f"{ref}.k{n}"
                stock_lines.append(StockLine(sub_ref, part.item_id, warehouse_id, q_qty(part.quantity)))
                owners[sub_ref] = ref
                detail.setdefault(ref, []).append((sub_ref, part.item_id, q_qty(part.quantity)))
        else:
            stock_lines.append(StockLine(ref, item_id, warehouse_id, qty, lot_id=lot_id))
            owners[ref] = ref
    res = services.get(StockService).issue(uow, company_id, source, stock_lines, on)
    values = _values_by_ref(res.move_ids)
    if values is None:
        total_qty = sum((ln.quantity for ln in stock_lines), Decimal(0))
        values = {ln.line_ref: res.total_value * ln.quantity / total_qty for ln in stock_lines} if total_qty else {}
    out: dict[str, IssuedLine] = {}
    for ref, _item, _qty, _wh, _lot in lines:
        subs = [ln.line_ref for ln in stock_lines if owners[ln.line_ref] == ref]
        value = q_amount(sum((values.get(r, Decimal(0)) for r in subs), Decimal(0)))
        parts_out = [
            {"item_id": str(item), "quantity": f"{q:f}", "value": f"{q_amount(values.get(sub, Decimal(0))):f}"}
            for sub, item, q in detail.get(ref, [])
        ]
        out[ref] = IssuedLine(ref, value, parts_out, tuple(res.move_ids))
    return out
