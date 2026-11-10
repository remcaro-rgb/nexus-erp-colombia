"""Master data services: UoMs, items, presentations (F-INV-PRES), warehouses (F-ACC-BRANCH link), lots,
putaway rules. Every write runs inside the caller's unit of work; inputs are already parsed by the API."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Model

from iam.branches import services as branch_services
from inventory.common import as_uuid, audit
from inventory.errors import error
from inventory.locations import service as locations
from inventory.models import Item, Location, Lot, PackagedQuant, Presentation, PutawayRule, Status, Uom, Warehouse
from inventory.presentations import core as pres
from kernel.errors import ValidationFailed
from kernel.uow import UnitOfWork


def _text(data: dict[str, Any], key: str, max_len: int, required: bool = True) -> str:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        return ""
    if not isinstance(value, str) or len(value.strip()) > max_len:
        raise ValidationFailed(f"{key} must be text up to {max_len} characters", pointer=f"/{key}", reason="format")
    return value.strip()


def _create[M: Model](
    model: type[M], uow: UnitOfWork, company_id: uuid.UUID, dup_msg: str, dup_field: str, **values: Any
) -> M:
    try:
        with transaction.atomic():
            obj: M = model._default_manager.create(
                tenant_id=uow.ctx.tenant_id,
                company_id=company_id,
                created_by=uow.ctx.principal_id,
                updated_by=uow.ctx.principal_id,
                **values,
            )
            return obj
    except IntegrityError as exc:
        raise ValidationFailed(dup_msg, pointer=f"/{dup_field}", reason="duplicate") from exc


# --------------------------------------------------------------------------- UoM


def create_uom(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> Uom:
    precision = pres.validate_precision(data.get("precision", 0))
    uom = _create(
        Uom,
        uow,
        company_id,
        "UoM code already exists",
        "code",
        code=_text(data, "code", 20),
        name=_text(data, "name", 120),
        precision=precision,
        category=_text(data, "category", 40, required=False),
    )
    audit(uow, "create", "inventory.uom", uom, {"code": uom.code})
    return uom


def uom_by_code(company_id: uuid.UUID, code: str) -> Uom:
    uom = Uom.objects.filter(company_id=company_id, code__iexact=code, status=Status.ACTIVE).first()
    if uom is None:
        raise ValidationFailed(f"unknown unit of measure {code}", pointer="/base_uom", reason="not_found")
    return uom


# --------------------------------------------------------------------------- items

ITEM_ENUMS = {
    "item_type": Item.ItemType.values,
    "valuation_method": Item.Valuation.values,
    "tracking": Item.Tracking.values,
}


def _item_values(company_id: uuid.UUID, data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, allowed in ITEM_ENUMS.items():
        if key in data:
            if data[key] not in allowed:
                raise ValidationFailed(f"{key} must be one of {', '.join(allowed)}", pointer=f"/{key}", reason="enum")
            out[key] = data[key]
    for key, size in (("category", 60), ("sales_tax_code", 40), ("purchase_tax_code", 40), ("gtin", 14)):
        if key in data:
            out[key] = _text(data, key, size, required=False) or (None if key != "category" else "")
    if "base_uom" in data or not partial:
        out["base_uom"] = uom_by_code(company_id, _text(data, "base_uom", 20))
    if "standard_cost" in data:
        out["standard_cost"] = (
            None
            if data["standard_cost"] in (None, "")
            else pres.parse_decimal(data["standard_cost"], "validation_failed")
        )
    if "default_bin_id" in data:
        bin_id = data["default_bin_id"]
        if bin_id in (None, ""):
            out["default_bin"] = None
        else:
            loc = Location.objects.filter(company_id=company_id, id=as_uuid(bin_id, "default_bin_id")).first()
            if loc is None:
                raise error("location_cross_warehouse", "unknown default bin")
            locations.validate_putaway_target(loc.warehouse, loc)
            out["default_bin"] = loc
    return out


def create_item(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> Item:
    values = _item_values(company_id, data, partial=False)
    item = _create(
        Item,
        uow,
        company_id,
        "item code already exists",
        "code",
        code=_text(data, "code", 60),
        name=_text(data, "name", 200),
        **values,
    )
    audit(uow, "create", "inventory.item", item, {"code": item.code})
    return item


def update_item(uow: UnitOfWork, item: Item, data: dict[str, Any]) -> Item:
    values = _item_values(item.company_id, data, partial=True)
    if "name" in data:
        values["name"] = _text(data, "name", 200)
    if "base_uom" in values and values["base_uom"].id != item.base_uom_id:
        from inventory.models import StockMove

        if StockMove.objects.filter(item_id=item.id).exists():
            raise error("uom_precision_violation", "the base UoM of an item with stock moves cannot change")
    for key, value in values.items():
        setattr(item, key, value)
    if values:
        item.save_versioned(uow.ctx.principal_id, list(values))
        audit(uow, "update", "inventory.item", item, {k: str(v) for k, v in values.items()})
    return item


# --------------------------------------------------------------------------- presentations


def _presentation_event(uow: UnitOfWork, p: Presentation) -> None:
    uow.emit(
        "inventory.presentation.changed.v1",
        {"presentation_id": str(p.id), "item_id": str(p.item_id), "factor": format(p.factor, "f")},
        company_id=p.company_id,
    )


def _check_barcode(company_id: uuid.UUID, barcode: str | None, exclude: uuid.UUID | None = None) -> None:
    if not barcode:
        return
    qs = Presentation.objects.filter(company_id=company_id, barcode=barcode)
    if exclude is not None:
        qs = qs.exclude(id=exclude)
    if qs.exists() or Item.objects.filter(company_id=company_id, gtin=barcode).exists():
        raise error("presentation_barcode_duplicate", "the barcode is already used in this company")


def create_presentation(uow: UnitOfWork, item: Item, data: dict[str, Any]) -> Presentation:
    factor = pres.validate_factor(data.get("factor"))
    precision = pres.validate_precision(data.get("precision", 0))
    pres.check_precision(factor, item.base_uom.precision, "factor")
    barcode = _text(data, "barcode", 64, required=False) or None
    _check_barcode(item.company_id, barcode)
    try:
        with transaction.atomic():
            p = Presentation.objects.create(
                tenant_id=item.tenant_id,
                company_id=item.company_id,
                item=item,
                code=_text(data, "code", 40),
                name=_text(data, "name", 120),
                uom=_text(data, "uom", 20),
                factor=factor,
                precision=precision,
                barcode=barcode,
                is_default_purchase=bool(data.get("is_default_purchase", False)),
                is_default_sale=bool(data.get("is_default_sale", False)),
                created_by=uow.ctx.principal_id,
                updated_by=uow.ctx.principal_id,
            )
    except IntegrityError as exc:
        raise ValidationFailed(
            "presentation code already exists for this item", pointer="/code", reason="duplicate"
        ) from exc
    audit(uow, "create", "inventory.presentation", p, {"code": p.code, "factor": format(factor, "f")})
    _presentation_event(uow, p)
    return p


def _has_packaged_stock(p: Presentation) -> bool:
    return PackagedQuant.objects.filter(presentation_id=p.id, qty_packages__gt=0).exists()


def update_presentation(uow: UnitOfWork, p: Presentation, data: dict[str, Any]) -> Presentation:
    fields: list[str] = []
    if "factor" in data:
        factor = pres.validate_factor(data["factor"])
        if factor != p.factor:
            if _has_packaged_stock(p):
                raise error(
                    "presentation_factor_invalid", "the factor of a presentation with packaged stock cannot change"
                )
            pres.check_precision(factor, p.item.base_uom.precision, "factor")
            p.factor = factor
            fields.append("factor")
    if "precision" in data:
        p.precision = pres.validate_precision(data["precision"])
        fields.append("precision")
    if "barcode" in data:
        barcode = _text(data, "barcode", 64, required=False) or None
        _check_barcode(p.company_id, barcode, exclude=p.id)
        p.barcode = barcode
        fields.append("barcode")
    for key in ("name", "uom"):
        if key in data:
            setattr(p, key, _text(data, key, 120 if key == "name" else 20))
            fields.append(key)
    for key in ("is_default_purchase", "is_default_sale"):
        if key in data:
            setattr(p, key, bool(data[key]))
            fields.append(key)
    if fields:
        p.save_versioned(uow.ctx.principal_id, fields)
        audit(uow, "update", "inventory.presentation", p, {f: str(getattr(p, f)) for f in fields})
        _presentation_event(uow, p)
    return p


def set_presentation_status(uow: UnitOfWork, p: Presentation, status: str) -> Presentation:
    if status == Status.ARCHIVED and _has_packaged_stock(p):
        raise error("packaged_exceeds_canonical", "a presentation with packaged stock cannot be archived")
    p.status = status
    p.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, status.lower(), "inventory.presentation", p)
    _presentation_event(uow, p)
    return p


# --------------------------------------------------------------------------- warehouses


def create_warehouse(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> Warehouse:
    """A warehouse requires an ACTIVE branch of the same company (T-ACC-BRANCH-03)."""
    raw_branch = data.get("branch_id")
    if raw_branch in (None, ""):
        raise ValidationFailed("branch_id is required", pointer="/branch_id", reason="required")
    try:
        branch_id = uuid.UUID(str(raw_branch))
    except ValueError:
        raise ValidationFailed("branch_id must be a UUID", pointer="/branch_id", reason="format") from None
    branch_services.validate_branch_link(uow.ctx, company_id, branch_id)
    wh = _create(
        Warehouse,
        uow,
        company_id,
        "warehouse code already exists",
        "code",
        code=_text(data, "code", 40),
        name=_text(data, "name", 200),
        branch_id=branch_id,
        address=_text(data, "address", 255, required=False),
        allow_negative=bool(data.get("allow_negative", False)),
    )
    locations.create_default_tree(uow, wh)
    audit(uow, "create", "inventory.warehouse", wh, {"code": wh.code, "branch_id": str(branch_id)})
    return wh


def update_warehouse(uow: UnitOfWork, wh: Warehouse, data: dict[str, Any]) -> Warehouse:
    fields: list[str] = []
    if "branch_id" in data:
        raw = data["branch_id"]
        if raw in (None, ""):
            raise ValidationFailed("branch_id is required", pointer="/branch_id", reason="required")
        branch_id = uuid.UUID(str(raw))
        branch_services.validate_branch_link(uow.ctx, wh.company_id, branch_id)
        wh.branch_id = branch_id
        fields.append("branch_id")
    for key, size in (("name", 200), ("address", 255)):
        if key in data:
            setattr(wh, key, _text(data, key, size, required=key == "name"))
            fields.append(key)
    if "allow_negative" in data:
        wh.allow_negative = bool(data["allow_negative"])
        fields.append("allow_negative")
    if fields:
        wh.save_versioned(uow.ctx.principal_id, fields)
        audit(uow, "update", "inventory.warehouse", wh, {f: str(getattr(wh, f)) for f in fields})
    return wh


# --------------------------------------------------------------------------- lots / putaway


def create_lot(uow: UnitOfWork, company_id: uuid.UUID, item: Item, data: dict[str, Any]) -> Lot:
    expiry = data.get("expiry_date")
    import datetime as dt

    expiry_date = dt.date.fromisoformat(str(expiry)) if expiry else None
    lot = _create(
        Lot,
        uow,
        company_id,
        "lot code already exists for this item",
        "code",
        item=item,
        code=_text(data, "code", 60),
        expiry_date=expiry_date,
    )
    audit(uow, "create", "inventory.lot", lot, {"code": lot.code})
    return lot


def create_putaway_rule(uow: UnitOfWork, wh: Warehouse, data: dict[str, Any]) -> PutawayRule:
    loc_id = data.get("location_id")
    loc = (
        Location.objects.filter(company_id=wh.company_id, id=as_uuid(loc_id, "location_id")).first() if loc_id else None
    )
    if loc is None:
        raise error("location_cross_warehouse", "unknown location")
    locations.validate_putaway_target(wh, loc)
    item = None
    if data.get("item_id"):
        item = Item.objects.filter(company_id=wh.company_id, id=as_uuid(data["item_id"], "item_id")).first()
        if item is None:
            raise ValidationFailed("unknown item", pointer="/item_id", reason="not_found")
    category = _text(data, "category", 60, required=False) or None
    if item is None and category is None:
        raise ValidationFailed("item_id or category is required", pointer="/item_id", reason="required")
    priority = data.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValidationFailed("priority must be an integer", pointer="/priority", reason="type")
    rule = _create(
        PutawayRule,
        uow,
        wh.company_id,
        "duplicate rule",
        "location_id",
        warehouse=wh,
        item=item,
        category=category,
        location=loc,
        priority=priority,
        active=bool(data.get("active", True)),
    )
    audit(uow, "create", "inventory.putaway_rule", rule, {"location_id": str(loc.id)})
    return rule


def quantity_in_base(item: Item, presentation: Presentation | None, qty: Decimal, packages: Decimal | None) -> Decimal:
    """Base quantity of a line given either base ``qty`` or ``packages`` of ``presentation``."""
    if presentation is None:
        return pres.check_precision(qty, item.base_uom.precision)
    if packages is not None:
        return pres.to_base(packages, presentation.factor, presentation.precision, item.base_uom.precision)
    pres.to_packages(qty, presentation.factor, presentation.precision)
    return qty
