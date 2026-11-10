"""Inventory core API (contracts/openapi/modules/inventory.yaml): UoMs, items, presentations, warehouses, locations
(+tree), putaway rules, lots, moves (+``:internal``), quants, availability, transfers (+submit/approve/ship/receive/cancel)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Exists, OuterRef, Q
from rest_framework.request import Request
from rest_framework.response import Response

from inventory import masters
from inventory.api.base import InventoryView, body, decimal, money, qty, s, uuid_or_none
from inventory.locations import service as locations
from inventory.models import (
    Item,
    Location,
    Lot,
    Presentation,
    PutawayRule,
    Status,
    StockMove,
    StockQuant,
    Transfer,
    TransferLine,
    Uom,
    Warehouse,
)
from inventory.stock.service import SERVICE as STOCK
from inventory.transfers import service as transfers
from kernel import clock, ids
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork


def _date(value: Any, field: str, default: dt.date | None = None) -> dt.date:
    if value in (None, ""):
        if default is not None:
            return default
        raise ValidationFailed(f"{field} is required", pointer=f"/{field}", reason="required")
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be YYYY-MM-DD", pointer=f"/{field}", reason="format") from None


def _only(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown fields: {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="additional_property")


# =========================================================================== UoM


def uom_dict(u: Uom) -> dict[str, Any]:
    return {"id": s(u.id), "tenant_id": s(u.tenant_id), "company_id": s(u.company_id), "code": u.code, "name": u.name,
            "precision": u.precision, "category": u.category, "status": u.status, "version": u.version}  # fmt: skip


class UomListView(InventoryView):
    perm, resource_type = "inventory.uom", "inventory.uom"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return uom_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Uom.objects.all(), company)
        return self.paginate(request, qs, search_fields=("code", "name"), filter_fields={"status": "status", "code": "code"}, sort_fields=("code",))

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, {"code", "name", "precision", "category"})
        return self.command(request, "inventory.uom.create", lambda u: (self.serialize(masters.create_uom(u, company, data)), 201))


class UomDetailView(InventoryView):
    perm, resource_type = "inventory.uom", "inventory.uom"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return uom_dict(obj)

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        u = self.one("read", Uom.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(u), u)


# =========================================================================== items


def item_dict(i: Item) -> dict[str, Any]:
    return {
        "id": s(i.id), "tenant_id": s(i.tenant_id), "company_id": s(i.company_id), "code": i.code, "name": i.name,
        "status": i.status, "base_uom": i.base_uom.code, "item_type": i.item_type, "category": i.category,
        "valuation_method": i.valuation_method, "tracking": i.tracking, "sales_tax_code": i.sales_tax_code,
        "purchase_tax_code": i.purchase_tax_code, "gtin": i.gtin, "default_bin_id": s(i.default_bin_id),
        "standard_cost": s(i.standard_cost), "version": i.version,
    }  # fmt: skip


ITEM_FIELDS = {"code", "name", "item_type", "base_uom", "category", "valuation_method", "tracking", "sales_tax_code",
               "purchase_tax_code", "gtin", "default_bin_id", "standard_cost"}  # fmt: skip


class ItemListView(InventoryView):
    perm, resource_type = "inventory.item", "inventory.item"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return item_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Item.objects.select_related("base_uom"), company)
        return self.paginate(
            request, qs, search_fields=("code", "name"),
            filter_fields={"status": "status", "item_type": "item_type", "category": "category", "code": "code"},
            sort_fields=("code", "name"),
        )  # fmt: skip

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, ITEM_FIELDS)
        self.check_writable(data, ITEM_FIELDS)
        return self.command(request, "inventory.item.create", lambda u: (self.serialize(masters.create_item(u, company, data)), 201))


class ItemDetailView(InventoryView):
    perm, resource_type = "inventory.item", "inventory.item"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return item_dict(obj)

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        i = self.one("read", Item.objects.select_related("base_uom"), self.company(request), pk)
        return self.etagged(self.serialize(i), i)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        item = self.one("update", Item.objects.select_related("base_uom"), company, pk)
        self.if_match(request, item)
        data = body(request)
        _only(data, ITEM_FIELDS - {"code"})
        self.check_writable(data, ITEM_FIELDS)
        return self.command(request, "inventory.item.update", lambda u: (self.serialize(masters.update_item(u, item, data)), 200))


# =========================================================================== presentations


def presentation_dict(p: Presentation) -> dict[str, Any]:
    return {
        "id": s(p.id), "tenant_id": s(p.tenant_id), "company_id": s(p.company_id), "item_id": s(p.item_id), "code": p.code,
        "name": p.name, "status": p.status, "factor": s(p.factor), "uom": p.uom, "barcode": p.barcode,
        "precision": p.precision, "is_default_purchase": p.is_default_purchase, "is_default_sale": p.is_default_sale,
        "version": p.version,
    }  # fmt: skip


PRESENTATION_FIELDS = {"code", "name", "factor", "uom", "barcode", "precision", "is_default_purchase", "is_default_sale"}


class _PresentationBase(InventoryView):
    perm, resource_type = "inventory.presentation", "inventory.presentation"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return presentation_dict(obj)

    def item(self, request: Request, item_id: uuid.UUID, verb: str = "read") -> Item:
        company = self.company(request)
        self.allow(verb)
        item = Item.objects.select_related("base_uom").filter(company_id=company, id=item_id).first()
        if item is None or not self.scoped("read", Item.objects.filter(id=item_id), company).exists():
            raise NotFound("item not found")
        return item


class PresentationListView(_PresentationBase):
    def get(self, request: Request, item_id: uuid.UUID) -> Response:
        item = self.item(request, item_id)
        qs = self.scoped("read", Presentation.objects.filter(item_id=item.id), item.company_id)
        return self.paginate(request, qs, search_fields=("code", "name", "barcode"), filter_fields={"status": "status"}, sort_fields=("code",))

    def post(self, request: Request, item_id: uuid.UUID) -> Response:
        item = self.item(request, item_id, "create")
        data = body(request)
        _only(data, PRESENTATION_FIELDS)
        return self.command(
            request, "inventory.presentation.create", lambda u: (self.serialize(masters.create_presentation(u, item, data)), 201)
        )


class PresentationDetailView(_PresentationBase):
    def get(self, request: Request, item_id: uuid.UUID, pk: uuid.UUID) -> Response:
        item = self.item(request, item_id)
        p = self.one("read", Presentation.objects.filter(item_id=item.id), item.company_id, pk)
        return self.etagged(self.serialize(p), p)

    def patch(self, request: Request, item_id: uuid.UUID, pk: uuid.UUID) -> Response:
        item = self.item(request, item_id, "update")
        p = self.one("update", Presentation.objects.select_related("item__base_uom").filter(item_id=item.id), item.company_id, pk)
        self.if_match(request, p)
        data = body(request)
        _only(data, PRESENTATION_FIELDS - {"code"})
        return self.command(
            request, "inventory.presentation.update", lambda u: (self.serialize(masters.update_presentation(u, p, data)), 200)
        )


class PresentationActionView(_PresentationBase):
    def post(self, request: Request, item_id: uuid.UUID, pk: uuid.UUID, action: str) -> Response:
        if action not in ("archive", "unarchive"):
            raise NotFound("unknown action")
        item = self.item(request, item_id, "update")
        p = self.one("update", Presentation.objects.filter(item_id=item.id), item.company_id, pk)
        status = Status.ARCHIVED if action == "archive" else Status.ACTIVE
        return self.command(
            request, f"inventory.presentation.{action}",
            lambda u: (self.serialize(masters.set_presentation_status(u, p, status)), 200),
        )  # fmt: skip


# =========================================================================== warehouses


def warehouse_dict(w: Warehouse) -> dict[str, Any]:
    return {
        "id": s(w.id), "tenant_id": s(w.tenant_id), "company_id": s(w.company_id), "code": w.code, "name": w.name,
        "status": w.status, "branch_id": s(w.branch_id), "allow_negative": w.allow_negative, "address": w.address,
        "root_location_id": s(w.root_location_id), "transit_location_id": s(w.transit_location_id),
        "valuation_scope": w.valuation_scope, "version": w.version,
    }  # fmt: skip


WAREHOUSE_FIELDS = {"code", "name", "branch_id", "allow_negative", "address"}


class WarehouseListView(InventoryView):
    perm, resource_type = "inventory.warehouse", "inventory.warehouse"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return warehouse_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Warehouse.objects.all(), company)
        return self.paginate(request, qs, search_fields=("code", "name"), filter_fields={"status": "status", "branch_id": "branch_id"}, sort_fields=("code", "name"))

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, WAREHOUSE_FIELDS)
        return self.command(request, "inventory.warehouse.create", lambda u: (self.serialize(masters.create_warehouse(u, company, data)), 201))


class WarehouseDetailView(InventoryView):
    perm, resource_type = "inventory.warehouse", "inventory.warehouse"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return warehouse_dict(obj)

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        w = self.one("read", Warehouse.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(w), w)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        w = self.one("update", Warehouse.objects.all(), company, pk)
        self.if_match(request, w)
        data = body(request)
        _only(data, WAREHOUSE_FIELDS - {"code"})
        return self.command(request, "inventory.warehouse.update", lambda u: (self.serialize(masters.update_warehouse(u, w, data)), 200))


# =========================================================================== locations


def _with_leaf(qs: Any) -> Any:
    children = Location.objects.filter(parent_id=OuterRef("pk"), status=Status.ACTIVE)
    return qs.annotate(has_children=Exists(children))


def location_dict(loc: Location) -> dict[str, Any]:
    has_children = getattr(loc, "has_children", None)
    if has_children is None:
        has_children = locations.has_children(loc)
    return {
        "id": s(loc.id), "tenant_id": s(loc.tenant_id), "company_id": s(loc.company_id), "warehouse_id": s(loc.warehouse_id),
        "code": loc.code, "name": loc.name, "status": loc.status, "parent_id": s(loc.parent_id),
        "location_type": loc.location_type, "is_leaf": not has_children, "barcode": loc.barcode,
        "capacity": s(loc.capacity), "path": loc.path, "allow_mixed_lots": loc.allow_mixed_lots, "version": loc.version,
    }  # fmt: skip


class _LocationBase(InventoryView):
    perm, resource_type = "inventory.location", "inventory.location"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return location_dict(obj)

    def warehouse(self, request: Request, warehouse_id: uuid.UUID, verb: str = "read") -> Warehouse:
        company = self.company(request)
        self.allow(verb)
        wh = _proxy(self, "inventory.warehouse").scoped("read", Warehouse.objects.all(), company).filter(id=warehouse_id).first()
        if wh is None:
            raise NotFound("warehouse not found")
        return wh


LOCATION_FIELDS = {"code", "name", "location_type", "parent_id", "barcode", "capacity", "allow_mixed_lots"}


def _location_input(data: dict[str, Any]) -> locations.LocationInput:
    return locations.LocationInput(
        code=str(data.get("code") or ""),
        name=str(data.get("name") or data.get("code") or ""),
        location_type=str(data.get("location_type") or ""),
        parent_id=uuid_or_none(data.get("parent_id"), "parent_id"),
        barcode=data.get("barcode") or None,
        capacity=decimal(data.get("capacity"), "capacity", required=False),
        allow_mixed_lots=bool(data.get("allow_mixed_lots", True)),
    )


class LocationListView(_LocationBase):
    def get(self, request: Request, warehouse_id: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id)
        qs = _with_leaf(self.scoped("read", Location.objects.filter(warehouse_id=wh.id), wh.company_id))
        return self.paginate(
            request, qs, search_fields=("code", "name", "path"),
            filter_fields={"status": "status", "location_type": "location_type", "parent_id": "parent_id"},
            sort_fields=("code", "path"),
        )  # fmt: skip

    def post(self, request: Request, warehouse_id: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id, "create")
        data = body(request)
        _only(data, LOCATION_FIELDS)
        inp = _location_input(data)
        return self.command(request, "inventory.location.create", lambda u: (self.serialize(locations.create_location(u, wh, inp)), 201))


class LocationTreeView(_LocationBase):
    def get(self, request: Request, warehouse_id: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id)
        self.allow("read")
        return Response({"warehouse_id": str(wh.id), "items": locations.tree(wh)})


class LocationDetailView(_LocationBase):
    def get(self, request: Request, warehouse_id: uuid.UUID, pk: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id)
        loc = self.one("read", Location.objects.filter(warehouse_id=wh.id), wh.company_id, pk)
        return self.etagged(self.serialize(loc), loc)

    def patch(self, request: Request, warehouse_id: uuid.UUID, pk: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id, "update")
        loc = self.one("update", Location.objects.select_related("warehouse").filter(warehouse_id=wh.id), wh.company_id, pk)
        self.if_match(request, loc)
        data = body(request)
        _only(data, LOCATION_FIELDS - {"code"})
        changes = dict(data)
        if "parent_id" in changes:
            changes["parent_id"] = uuid_or_none(changes["parent_id"], "parent_id")
        if "capacity" in changes:
            changes["capacity"] = decimal(changes["capacity"], "capacity", required=False)
        return self.command(request, "inventory.location.update", lambda u: (self.serialize(locations.update_location(u, loc, changes)), 200))


class LocationArchiveView(_LocationBase):
    def post(self, request: Request, warehouse_id: uuid.UUID, pk: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id, "update")
        loc = self.one("update", Location.objects.select_related("warehouse").filter(warehouse_id=wh.id), wh.company_id, pk)
        return self.command(request, "inventory.location.archive", lambda u: (self.serialize(locations.archive_location(u, loc)), 200))


class LocationSearchView(_LocationBase):
    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = _with_leaf(self.scoped("read", Location.objects.all(), company))
        return self.paginate(
            request, qs, search_fields=("code", "name", "path", "barcode"),
            filter_fields={"warehouse_id": "warehouse_id", "location_type": "location_type", "status": "status", "barcode": "barcode"},
            sort_fields=("code", "path"),
        )  # fmt: skip


# =========================================================================== putaway rules


def putaway_dict(r: PutawayRule) -> dict[str, Any]:
    return {"id": s(r.id), "tenant_id": s(r.tenant_id), "company_id": s(r.company_id), "warehouse_id": s(r.warehouse_id),
            "item_id": s(r.item_id), "category": r.category, "location_id": s(r.location_id), "priority": r.priority,
            "active": r.active, "version": r.version}  # fmt: skip


class PutawayRuleListView(_LocationBase):
    perm, resource_type = "inventory.putaway_rule", "inventory.putaway_rule"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return putaway_dict(obj)

    def get(self, request: Request, warehouse_id: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id)
        qs = self.scoped("read", PutawayRule.objects.filter(warehouse_id=wh.id), wh.company_id)
        return self.paginate(request, qs, filter_fields={"item_id": "item_id", "category": "category"}, sort_fields=("priority",))

    def post(self, request: Request, warehouse_id: uuid.UUID) -> Response:
        wh = self.warehouse(request, warehouse_id, "create")
        data = body(request)
        _only(data, {"item_id", "category", "location_id", "priority", "active"})
        return self.command(request, "inventory.putaway_rule.create", lambda u: (self.serialize(masters.create_putaway_rule(u, wh, data)), 201))


# =========================================================================== lots


def lot_dict(lot: Lot) -> dict[str, Any]:
    return {"id": s(lot.id), "tenant_id": s(lot.tenant_id), "company_id": s(lot.company_id), "item_id": s(lot.item_id),
            "code": lot.code, "expiry_date": s(lot.expiry_date.isoformat() if lot.expiry_date else None),
            "status": lot.status, "version": lot.version}  # fmt: skip


class LotListView(InventoryView):
    perm, resource_type = "inventory.lot", "inventory.lot"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return lot_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Lot.objects.all(), company)
        return self.paginate(request, qs, search_fields=("code",), filter_fields={"item_id": "item_id", "status": "status"}, sort_fields=("code", "expiry_date"))

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, {"code", "item_id", "expiry_date"})
        item = Item.objects.filter(company_id=company, id=uuid_or_none(data.get("item_id"), "item_id")).first()
        if item is None:
            raise ValidationFailed("unknown item", pointer="/item_id", reason="not_found")
        return self.command(request, "inventory.lot.create", lambda u: (self.serialize(masters.create_lot(u, company, item, data)), 201))


# =========================================================================== moves / quants / availability


def move_dict(m: StockMove) -> dict[str, Any]:
    return {
        "id": s(m.id), "tenant_id": s(m.tenant_id), "company_id": s(m.company_id), "status": m.state, "kind": m.kind,
        "item_id": s(m.item_id), "warehouse_id": s(m.warehouse_id), "quantity": qty(m.quantity, m.uom),
        "qty_packages": s(m.qty_packages), "presentation_id": s(m.presentation_id),
        "from_location_id": s(m.from_location_id), "to_location_id": s(m.to_location_id), "lot_id": s(m.lot_id),
        "unit_cost": s(m.unit_cost), "value": money(m.value),
        "source": {"type": m.source_type, "id": s(m.source_id), "number": m.source_number or None},
        "move_date": m.move_date.isoformat(), "journal_entry_id": s(m.journal_entry_id), "version": 1,
    }  # fmt: skip


class MoveListView(InventoryView):
    perm, resource_type = "inventory.move", "inventory.move"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return move_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", StockMove.objects.all(), company)
        return self.paginate(
            request, qs,
            filter_fields={"item_id": "item_id", "warehouse_id": "warehouse_id", "kind": "kind", "lot_id": "lot_id",
                           "move_date": "move_date", "source_id": "source_id", "from_location_id": "from_location_id",
                           "to_location_id": "to_location_id"},
            sort_fields=("move_date", "created_at"),
        )  # fmt: skip


class MoveDetailView(MoveListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        m = self.one("read", StockMove.objects.all(), self.company(request), pk)
        return Response(self.serialize(m))


class MoveInternalView(MoveListView):
    """``POST /moves:internal`` — bin-to-bin move inside one warehouse (no journal entry, valuation unchanged)."""

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("post")
        data = body(request)
        _only(data, {"item_id", "from_location_id", "to_location_id", "quantity", "lot_id", "presentation_id", "qty_packages", "move_date", "reference"})
        item_id = uuid_or_none(data.get("item_id"), "item_id")
        src = uuid_or_none(data.get("from_location_id"), "from_location_id")
        dst = uuid_or_none(data.get("to_location_id"), "to_location_id")
        if item_id is None or src is None or dst is None:
            raise ValidationFailed("item_id, from_location_id and to_location_id are required", pointer="/", reason="required")
        src_loc = Location.objects.filter(company_id=company, id=src).first()
        if src_loc is None or not self.scoped("post", Location.objects.filter(id=src), company).exists():
            raise NotFound("location not found")
        item = Item.objects.select_related("base_uom").filter(company_id=company, id=item_id).first()
        if item is None:
            raise NotFound("item not found")
        presentation = None
        if data.get("presentation_id"):
            presentation = Presentation.objects.filter(
                company_id=company, id=uuid_or_none(data.get("presentation_id"), "presentation_id"), item_id=item.id
            ).first()
            if presentation is None:
                raise ValidationFailed("unknown presentation", pointer="/presentation_id", reason="not_found")
        packages = decimal(data.get("qty_packages"), "qty_packages", required=False)
        raw_qty = decimal(data.get("quantity"), "quantity", required=packages is None)
        quantity = masters.quantity_in_base(item, presentation, raw_qty or Decimal(0), packages)
        move_date = _date(data.get("move_date"), "move_date", clock.business_date())
        doc = DocumentRef("inventory.internal_move", ids.new_id(), str(data.get("reference") or "")[:60] or None)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            m = STOCK.internal_move(
                u, company, doc, line_ref="1", item_id=item.id, from_location_id=src, to_location_id=dst,
                quantity=quantity, move_date=move_date, lot_id=uuid_or_none(data.get("lot_id"), "lot_id"),
                presentation_id=presentation.id if presentation else None,
            )  # fmt: skip
            return {"move": self.serialize(m)}, 201

        return self.command(request, "inventory.move.internal", fn)


def quant_dict(q: StockQuant) -> dict[str, Any]:
    return {"id": s(q.id), "tenant_id": s(q.tenant_id), "company_id": s(q.company_id), "status": "ACTIVE",
            "item_id": s(q.item_id), "warehouse_id": s(q.warehouse_id), "location_id": s(q.location_id),
            "lot_id": s(q.lot_id), "on_hand": s(q.on_hand), "reserved": s(q.reserved),
            "available": s(q.on_hand - q.reserved), "version": q.version}  # fmt: skip


class QuantListView(InventoryView):
    perm, resource_type = "inventory.quant", "inventory.quant"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return quant_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", StockQuant.objects.filter(~Q(on_hand=0) | ~Q(reserved=0)), company)
        return self.paginate(
            request, qs,
            filter_fields={"item_id": "item_id", "warehouse_id": "warehouse_id", "location_id": "location_id", "lot_id": "lot_id"},
        )  # fmt: skip


class QuantDetailView(QuantListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        q = self.one("read", StockQuant.objects.all(), self.company(request), pk)
        return Response(self.serialize(q))


class AvailabilityView(InventoryView):
    """``GET /availability?item=&presentation=&warehouse=&location=&lot=`` — base, packaged per presentation, loose."""

    perm, resource_type = "inventory.availability", "inventory.availability"
    PARAMS = {"item": "item_id", "item_id": "item_id", "presentation": "presentation_id", "presentation_id": "presentation_id",
              "warehouse": "warehouse_id", "warehouse_id": "warehouse_id", "location": "location_id",
              "location_id": "location_id", "lot": "lot_id", "lot_id": "lot_id"}  # fmt: skip

    def get(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("read")
        args: dict[str, uuid.UUID | None] = {}
        for key, value in request.query_params.items():
            if key not in self.PARAMS:
                raise ValidationFailed(f"unknown parameter {key}", pointer=f"/query/{key}", reason="filter_invalid")
            args[self.PARAMS[key]] = uuid_or_none(value, key)
        visible = set(
            _proxy(self, "inventory.warehouse").scoped("read", Warehouse.objects.all(), company).values_list("id", flat=True)
        )
        rows = [r for r in STOCK.detailed_availability(company, **args) if r.warehouse_id in visible]  # type: ignore[arg-type]
        items = [
            self.serialize(
                {
                    "item_id": s(r.item_id), "warehouse_id": s(r.warehouse_id), "location_id": s(r.location_id),
                    "location_code": r.location_code, "location_type": r.location_type, "lot_id": s(r.lot_id),
                    "on_hand": s(r.on_hand), "reserved": s(r.reserved), "available": s(r.available),
                    "packaged_base": s(r.packaged_base), "loose": s(r.loose),
                    "packaged": [
                        {"presentation_id": s(p.presentation_id), "code": p.code, "factor": s(p.factor), "packages": s(p.packages),
                         "reserved_packages": s(p.reserved_packages), "available_packages": s(p.available_packages)}
                        for p in r.packaged
                    ],
                }
            )
            for r in rows
        ]  # fmt: skip
        return Response({"items": items, "next_cursor": None})


def _proxy(view: InventoryView, perm: str) -> InventoryView:
    other = InventoryView()
    other.request = view.request
    other.perm = perm
    return other


# =========================================================================== transfers


def transfer_dict(t: Transfer) -> dict[str, Any]:
    lines = list(TransferLine.objects.filter(transfer_id=t.id).select_related("item__base_uom").order_by("line_no"))
    total = sum(((ln.value or 0) for ln in lines), 0)
    return {
        "id": s(t.id), "tenant_id": s(t.tenant_id), "company_id": s(t.company_id), "number": t.number,
        "document_date": t.document_date.isoformat(), "status": t.status, "from_warehouse_id": s(t.from_warehouse_id),
        "to_warehouse_id": s(t.to_warehouse_id), "transit_location_id": s(t.transit_location_id), "notes": t.notes,
        "source": {"type": t.source_type, "id": s(t.source_id)} if t.source_id else None,
        "requisition": {"type": t.requisition_type, "id": s(t.requisition_id)} if t.requisition_id else None,
        "total": money(total) if any(ln.value is not None for ln in lines) else None,
        "ship_entry_id": s(t.ship_entry_id), "receive_entry_id": s(t.receive_entry_id),
        "shipped_at": t.shipped_at.isoformat() if t.shipped_at else None,
        "received_at": t.received_at.isoformat() if t.received_at else None,
        "lines": [
            {"line_no": ln.line_no, "item_id": s(ln.item_id), "quantity": qty(ln.quantity, ln.item.base_uom.code),
             "presentation_id": s(ln.presentation_id), "qty_packages": s(ln.qty_packages), "lot_id": s(ln.lot_id),
             "from_location_id": s(ln.from_location_id), "to_location_id": s(ln.to_location_id)}
            for ln in lines
        ],
        "version": t.version,
    }  # fmt: skip


class TransferListView(InventoryView):
    perm, resource_type = "inventory.transfer", "inventory.transfer"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return transfer_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Transfer.objects.all(), company)
        return self.paginate(
            request, qs, search_fields=("number",),
            filter_fields={"status": "status", "from_warehouse_id": "from_warehouse_id", "to_warehouse_id": "to_warehouse_id"},
            sort_fields=("document_date", "number"),
        )  # fmt: skip

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, {"from_warehouse_id", "to_warehouse_id", "document_date", "notes", "lines"})
        raw_lines = data.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > 1000:
            raise ValidationFailed("1–1000 lines required", pointer="/lines", reason="required")
        lines = []
        for i, raw in enumerate(raw_lines):
            if not isinstance(raw, dict):
                raise ValidationFailed("line must be an object", pointer=f"/lines/{i}", reason="type")
            q = raw.get("quantity")
            qvalue = q.get("value") if isinstance(q, dict) else q
            item_id = uuid_or_none(raw.get("item_id"), f"lines/{i}/item_id")
            if item_id is None:
                raise ValidationFailed("item_id required", pointer=f"/lines/{i}/item_id", reason="required")
            packages = decimal(raw.get("qty_packages"), f"lines/{i}/qty_packages", required=False)
            lines.append(
                transfers.TransferLineInput(
                    item_id=item_id,
                    quantity=decimal(qvalue, f"lines/{i}/quantity", required=packages is None) or Decimal(0),
                    presentation_id=uuid_or_none(raw.get("presentation_id"), f"lines/{i}/presentation_id"),
                    qty_packages=packages,
                    lot_id=uuid_or_none(raw.get("lot_id"), f"lines/{i}/lot_id"),
                    from_location_id=uuid_or_none(raw.get("from_location_id"), f"lines/{i}/from_location_id"),
                    to_location_id=uuid_or_none(raw.get("to_location_id"), f"lines/{i}/to_location_id"),
                )
            )
        inp = transfers.TransferInput(
            from_warehouse_id=uuid_or_none(data.get("from_warehouse_id"), "from_warehouse_id") or ids.new_id(),
            to_warehouse_id=uuid_or_none(data.get("to_warehouse_id"), "to_warehouse_id") or ids.new_id(),
            document_date=_date(data.get("document_date"), "document_date", clock.business_date()),
            lines=tuple(lines),
            notes=str(data.get("notes") or ""),
        )
        return self.command(request, "inventory.transfer.create", lambda u: (self.serialize(transfers.create(u, company, inp)), 201))


class TransferDetailView(TransferListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        t = self.one("read", Transfer.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(t), t)


ACTIONS = {
    "submit": ("update", transfers.submit),
    "approve": ("approve", transfers.approve),
    "ship": ("post", transfers.ship),
    "receive": ("post", transfers.receive),
    "cancel": ("void", transfers.cancel),
}


class TransferActionView(TransferListView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:  # type: ignore[override]
        if action not in ACTIONS:
            raise NotFound("unknown action")
        verb, fn = ACTIONS[action]
        company = self.company(request)
        t = self.one(verb, Transfer.objects.all(), company, pk)
        return self.command(request, f"inventory.transfer.{action}", lambda u: (self.serialize(fn(u, t)), 200))
