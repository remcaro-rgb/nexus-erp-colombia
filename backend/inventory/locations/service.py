"""Location tree inside a warehouse (F-INV-LOC, D-014). Every write runs in the caller's unit of work.

* Strict tree per warehouse: the parent is in the same warehouse (``location_cross_warehouse``;
  composite FK in the DB), no cycles (service check + DB trigger), VIEW/INTERNAL parents only.
* Stock posts only to ACTIVE leaf bins of type INTERNAL/TRANSIT/SCRAP (``location_not_leaf``).
  A bin that holds stock or reservations cannot receive children (``location_not_empty``).
* Default tree per warehouse: root VIEW ``<warehouse code>`` with RECEPCION, ALMACEN, DESPACHO
  (INTERNAL), TRANSITO (TRANSIT) and SCRAP (SCRAP).
* Putaway (D-014): active rule for the item (highest priority) → rule for the item category →
  the item's default bin when it is in that warehouse → RECEPCION.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Q, Sum

from inventory.common import audit
from inventory.errors import error
from inventory.models import Item, Location, PutawayRule, Reservation, Status, StockQuant, Warehouse
from kernel.errors import ValidationFailed
from kernel.uow import UnitOfWork

POSTABLE_TYPES = (Location.LocationType.INTERNAL, Location.LocationType.TRANSIT, Location.LocationType.SCRAP)
PARENT_TYPES = (Location.LocationType.VIEW, Location.LocationType.INTERNAL)
DEFAULT_BINS = (
    ("RECEPCION", "Recepción", Location.LocationType.INTERNAL),
    ("ALMACEN", "Almacenamiento", Location.LocationType.INTERNAL),
    ("DESPACHO", "Despacho", Location.LocationType.INTERNAL),
    ("TRANSITO", "En tránsito", Location.LocationType.TRANSIT),
    ("SCRAP", "Desechos", Location.LocationType.SCRAP),
)
RECEIVING_CODE = "RECEPCION"
TRANSIT_CODE = "TRANSITO"
MAX_DEPTH = 10


@dataclass(frozen=True, slots=True)
class LocationInput:
    code: str
    name: str
    location_type: str
    parent_id: uuid.UUID | None = None
    barcode: str | None = None
    capacity: Decimal | None = None
    allow_mixed_lots: bool = True


def _event(uow: UnitOfWork, loc: Location, change: str) -> None:
    uow.emit(
        "inventory.location.changed.v1",
        {"location_id": str(loc.id), "warehouse_id": str(loc.warehouse_id), "change": change},
        company_id=loc.company_id,
    )


def has_children(loc: Location) -> bool:
    return Location.objects.filter(parent_id=loc.id, status=Status.ACTIVE).exists()


def holds_stock(loc: Location) -> bool:
    if StockQuant.objects.filter(location_id=loc.id).filter(~Q(on_hand=0) | ~Q(reserved=0)).exists():
        return True
    return Reservation.objects.filter(location_id=loc.id, state=Reservation.State.ACTIVE).exists()


def _path(parent: Location | None, code: str) -> str:
    return f"{parent.path}/{code}" if parent is not None else code


def _check_parent(warehouse: Warehouse, parent: Location | None) -> None:
    if parent is None:
        return
    if parent.warehouse_id != warehouse.id:
        raise error("location_cross_warehouse", "the parent location belongs to another warehouse")
    if parent.status != Status.ACTIVE:
        raise ValidationFailed("the parent location is archived", pointer="/parent_id", reason="archived")
    if parent.location_type not in PARENT_TYPES:
        raise error("location_not_leaf", "TRANSIT and SCRAP bins cannot have children")
    if parent.path.count("/") + 1 >= MAX_DEPTH:
        raise ValidationFailed("location tree too deep", pointer="/parent_id", reason="depth")
    if holds_stock(parent):
        raise error("location_not_empty", "the parent bin holds stock or reservations; it cannot become a group")


def create_location(uow: UnitOfWork, warehouse: Warehouse, data: LocationInput) -> Location:
    if data.location_type not in Location.LocationType.values:
        raise ValidationFailed("unknown location_type", pointer="/location_type", reason="enum")
    code = data.code.strip()
    if not code or len(code) > 40 or "/" in code:
        raise ValidationFailed("code required (1–40 characters, no '/')", pointer="/code", reason="format")
    parent_id = data.parent_id or warehouse.root_location_id
    parent = Location.objects.select_for_update().filter(id=parent_id).first() if parent_id else None
    if parent_id and parent is None:
        raise error("location_cross_warehouse", "unknown parent location")
    _check_parent(warehouse, parent)
    if Location.objects.filter(warehouse_id=warehouse.id, code__iexact=code).exists():
        raise ValidationFailed("location code already used in this warehouse", pointer="/code", reason="duplicate")
    loc = Location.objects.create(
        tenant_id=warehouse.tenant_id,
        company_id=warehouse.company_id,
        warehouse=warehouse,
        parent=parent,
        code=code,
        name=data.name.strip()[:200] or code,
        location_type=data.location_type,
        barcode=data.barcode or None,
        capacity=data.capacity,
        allow_mixed_lots=data.allow_mixed_lots,
        path=_path(parent, code),
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    audit(uow, "create", "inventory.location", loc, {"code": code, "parent_id": str(parent.id) if parent else None})
    _event(uow, loc, "created")
    return loc


def create_default_tree(uow: UnitOfWork, warehouse: Warehouse) -> Location:
    root = Location.objects.create(
        tenant_id=warehouse.tenant_id,
        company_id=warehouse.company_id,
        warehouse=warehouse,
        parent=None,
        code=warehouse.code,
        name=warehouse.name[:200],
        location_type=Location.LocationType.VIEW,
        path=warehouse.code,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    _event(uow, root, "created")
    warehouse.root_location = root
    transit: Location | None = None
    for code, name, typ in DEFAULT_BINS:
        loc = create_location(uow, warehouse, LocationInput(code=code, name=name, location_type=typ, parent_id=root.id))
        if code == TRANSIT_CODE:
            transit = loc
    warehouse.transit_location = transit
    Warehouse.objects.filter(id=warehouse.id).update(root_location=root, transit_location=transit)
    return root


def ancestors(loc: Location) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    cur_id = loc.parent_id
    while cur_id is not None and len(out) <= 64:
        out.append(cur_id)
        cur_id = Location.objects.filter(id=cur_id).values_list("parent_id", flat=True).first()
    return out


def move_location(uow: UnitOfWork, loc: Location, new_parent_id: uuid.UUID) -> Location:
    warehouse = loc.warehouse
    parent = Location.objects.select_for_update().filter(id=new_parent_id).first()
    if parent is None:
        raise error("location_cross_warehouse", "unknown parent location")
    if parent.id == loc.id or loc.id in ancestors(parent):
        raise ValidationFailed("the move would create a cycle", pointer="/parent_id", reason="location_cycle")
    _check_parent(warehouse, parent)
    old_path = loc.path
    loc.parent = parent
    loc.path = _path(parent, loc.code)
    loc.save_versioned(uow.ctx.principal_id, ["parent", "path"])
    for child in Location.objects.filter(warehouse_id=warehouse.id, path__startswith=old_path + "/"):
        child.path = loc.path + child.path[len(old_path) :]
        child.save_versioned(uow.ctx.principal_id, ["path"])
    audit(uow, "move", "inventory.location", loc, {"parent_id": str(parent.id)})
    _event(uow, loc, "updated")
    return loc


def update_location(uow: UnitOfWork, loc: Location, changes: dict[str, Any]) -> Location:
    fields: list[str] = []
    for key in ("name", "barcode", "capacity", "allow_mixed_lots"):
        if key in changes:
            setattr(loc, key, changes[key])
            fields.append(key)
    if "location_type" in changes and changes["location_type"] != loc.location_type:
        new_type = changes["location_type"]
        if new_type not in Location.LocationType.values:
            raise ValidationFailed("unknown location_type", pointer="/location_type", reason="enum")
        if holds_stock(loc) or (has_children(loc) and new_type not in PARENT_TYPES):
            raise error("location_not_empty", "the type of a bin with stock or children cannot change")
        loc.location_type = new_type
        fields.append("location_type")
    if fields:
        loc.save_versioned(uow.ctx.principal_id, fields)
        audit(uow, "update", "inventory.location", loc, {f: str(getattr(loc, f)) for f in fields})
        _event(uow, loc, "updated")
    if "parent_id" in changes and changes["parent_id"] != loc.parent_id:
        loc = move_location(uow, loc, changes["parent_id"])
    return loc


def archive_location(uow: UnitOfWork, loc: Location) -> Location:
    if loc.warehouse.root_location_id == loc.id:
        raise error("location_not_empty", "the root location of a warehouse cannot be archived")
    if holds_stock(loc) or has_children(loc):
        raise error("location_not_empty", "the location holds stock, reservations or active children")
    loc.status = Status.ARCHIVED
    loc.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "archive", "inventory.location", loc)
    _event(uow, loc, "archived")
    return loc


def is_leaf(loc: Location) -> bool:
    return not has_children(loc)


def assert_postable(loc: Location, warehouse_id: uuid.UUID | None = None) -> Location:
    """Stock may post only to ACTIVE leaf INTERNAL/TRANSIT/SCRAP bins of the given warehouse."""
    if warehouse_id is not None and loc.warehouse_id != warehouse_id:
        raise error("location_cross_warehouse", f"location {loc.code} belongs to another warehouse")
    if loc.location_type not in POSTABLE_TYPES or loc.status != Status.ACTIVE or has_children(loc):
        raise error("location_not_leaf", f"location {loc.code} is not an active leaf bin")
    return loc


def by_code(warehouse: Warehouse, code: str) -> Location:
    loc = Location.objects.filter(warehouse_id=warehouse.id, code__iexact=code, status=Status.ACTIVE).first()
    if loc is None:
        raise error("location_not_leaf", f"warehouse {warehouse.code} has no active {code} bin")
    return loc


def putaway(warehouse: Warehouse, item: Item) -> Location:
    """Target bin for a receipt line without an explicit location (D-014)."""
    rules = PutawayRule.objects.filter(warehouse_id=warehouse.id, active=True).select_related("location")
    for rule in rules.filter(item_id=item.id).order_by("-priority", "id"):
        if rule.location.status == Status.ACTIVE and not has_children(rule.location):
            return rule.location
    if item.category:
        for rule in rules.filter(item__isnull=True, category=item.category).order_by("-priority", "id"):
            if rule.location.status == Status.ACTIVE and not has_children(rule.location):
                return rule.location
    if item.default_bin_id:
        default = Location.objects.filter(id=item.default_bin_id, warehouse_id=warehouse.id).first()
        if default is not None and default.status == Status.ACTIVE and not has_children(default):
            return default
    return by_code(warehouse, RECEIVING_CODE)


def tree(warehouse: Warehouse) -> list[dict[str, Any]]:
    rows = list(Location.objects.filter(warehouse_id=warehouse.id).order_by("path"))
    on_hand = dict(
        StockQuant.objects.filter(warehouse_id=warehouse.id)
        .values("location_id")
        .annotate(total=Sum("on_hand"))
        .values_list("location_id", "total")
    )
    nodes: dict[uuid.UUID, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for r in rows:
        nodes[r.id] = {
            "id": str(r.id),
            "code": r.code,
            "name": r.name,
            "location_type": r.location_type,
            "status": r.status,
            "path": r.path,
            "parent_id": str(r.parent_id) if r.parent_id else None,
            "on_hand": format(on_hand.get(r.id) or Decimal(0), "f"),
            "children": [],
        }
    for r in rows:
        node = nodes[r.id]
        parent = nodes.get(r.parent_id) if r.parent_id else None
        (parent["children"] if parent is not None else roots).append(node)
    for node in nodes.values():
        node["is_leaf"] = not any(c["status"] == Status.ACTIVE for c in node["children"])
    return roots


def validate_putaway_target(warehouse: Warehouse, location: Location) -> None:
    assert_postable(location, warehouse.id)
    if location.location_type != Location.LocationType.INTERNAL:
        raise error("location_not_leaf", "putaway targets must be INTERNAL leaf bins")
