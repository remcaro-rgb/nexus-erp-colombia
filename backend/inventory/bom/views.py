"""Recipes / BOM API (contracts/openapi/modules/inventory.yaml § /api/v1/inventory/boms).

``GET/POST /boms`` · ``GET/PATCH/DELETE /boms/{id}`` · ``POST /boms/{id}:activate|:obsolete|:copy-version`` ·
``GET /boms/{id}/explode?qty=&date=&levels=multi|single``. Permissions ``inventory.bom.<verb>`` (deny by default),
lists through ``scope_queryset``; cost fields of the explosion are ``confidential`` (field policy).
``components`` is an object ``{"lines": [...], "byproducts": [...]}`` (a bare list of lines is accepted too).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from inventory.api.base import InventoryView, body, decimal, money, s, uuid_or_none
from inventory.bom import core
from inventory.bom import service as boms
from inventory.bom.models import Bom, BomLine
from inventory.transformations.registry import register_fields
from kernel import clock
from kernel.errors import NotFound, ValidationFailed

register_fields()

HEADER_FIELDS = {
    "code",
    "name",
    "item_id",
    "presentation_id",
    "bom_type",
    "valid_from",
    "valid_to",
    "output_qty",
    "yield_percent",
    "normal_waste_percent",
    "notes",
    "components",
}


def _date(value: Any, field: str, default: dt.date | None = None) -> dt.date | None:
    if value in (None, ""):
        return default
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be YYYY-MM-DD", pointer=f"/{field}", reason="format") from None


def _qty(value: Any, field: str, required: bool = True) -> Decimal | None:
    raw = value.get("value") if isinstance(value, dict) else value
    return decimal(raw, field, required=required)


def _only(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(
            f"unknown fields: {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="additional_property"
        )


def _line(raw: Any, ptr: str, byproduct: bool) -> boms.LineInput:
    if not isinstance(raw, dict):
        raise ValidationFailed("line must be an object", pointer=ptr, reason="type")
    _only(
        raw,
        {
            "item_id",
            "quantity",
            "scrap_percent",
            "cost_share_percent",
            "presentation_id",
            "is_optional",
            "substitute_group",
            "is_byproduct",
            "role",
            "line_no",
            "uom",
        },
    )
    item_id = uuid_or_none(raw.get("item_id"), f"{ptr}/item_id")
    if item_id is None:
        raise ValidationFailed("item_id required", pointer=f"{ptr}/item_id", reason="required")
    is_bp = byproduct or bool(raw.get("is_byproduct")) or raw.get("role") == BomLine.Role.BYPRODUCT
    return boms.LineInput(
        item_id=item_id,
        quantity=_qty(raw.get("quantity"), f"{ptr}/quantity") or Decimal(0),
        scrap_percent=decimal(raw.get("scrap_percent"), f"{ptr}/scrap_percent", required=False) or Decimal(0),
        is_byproduct=is_bp,
        cost_share_percent=decimal(raw.get("cost_share_percent"), f"{ptr}/cost_share_percent", required=False),
        presentation_id=uuid_or_none(raw.get("presentation_id"), f"{ptr}/presentation_id"),
        is_optional=bool(raw.get("is_optional")),
        substitute_group=str(raw.get("substitute_group") or ""),
    )


def parse_components(value: Any) -> tuple[boms.LineInput, ...]:
    if isinstance(value, list):
        value = {"lines": value}
    if not isinstance(value, dict):
        raise ValidationFailed("components must be an object with lines", pointer="/components", reason="type")
    _only(value, {"lines", "byproducts"})
    lines = value.get("lines") or []
    byproducts = value.get("byproducts") or []
    if not isinstance(lines, list) or not isinstance(byproducts, list):
        raise ValidationFailed("components.lines / byproducts must be arrays", pointer="/components", reason="type")
    return (
        *(_line(r, f"/components/lines/{i}", False) for i, r in enumerate(lines)),
        *(_line(r, f"/components/byproducts/{i}", True) for i, r in enumerate(byproducts)),
    )


def parse_bom(data: dict[str, Any], base: Bom | None = None) -> boms.BomInput:
    _only(data, HEADER_FIELDS)

    def pick(key: str, current: Any) -> Any:
        return data.get(key, current)

    item_id = uuid_or_none(pick("item_id", base.item_id if base else None), "item_id")
    if item_id is None:
        raise ValidationFailed("item_id required", pointer="/item_id", reason="required")
    valid_from = _date(pick("valid_from", base.valid_from.isoformat() if base else None), "valid_from")
    if valid_from is None:
        raise ValidationFailed("valid_from required", pointer="/valid_from", reason="required")
    lines = (
        parse_components(data["components"])
        if "components" in data or base is None
        else boms.copy_lines(base)  # PATCH without components keeps them
    )
    return boms.BomInput(
        code=str(pick("code", base.code if base else "") or ""),
        name=str(pick("name", base.name if base else "") or ""),
        item_id=item_id,
        valid_from=valid_from,
        valid_to=_date(pick("valid_to", base.valid_to.isoformat() if base and base.valid_to else None), "valid_to"),
        output_qty=_qty(pick("output_qty", s(base.output_qty) if base else None), "output_qty") or Decimal(0),
        yield_percent=decimal(pick("yield_percent", s(base.yield_percent) if base else "100"), "yield_percent")
        or Decimal(0),
        normal_waste_percent=decimal(
            pick("normal_waste_percent", s(base.normal_waste_percent) if base else "0"), "normal_waste_percent"
        )
        or Decimal(0),
        bom_type=str(pick("bom_type", base.bom_type if base else Bom.BomType.PRODUCTION)),
        presentation_id=uuid_or_none(
            pick("presentation_id", base.presentation_id if base else None), "presentation_id"
        ),
        notes=str(pick("notes", base.notes if base else "") or ""),
        lines=lines,
    )


def bom_dict(b: Bom) -> dict[str, Any]:
    lines = list(BomLine.objects.filter(bom_id=b.id).select_related("item__base_uom").order_by("line_no"))

    def line(ln: BomLine) -> dict[str, Any]:
        return {
            "line_no": ln.line_no,
            "item_id": s(ln.item_id),
            "item_code": ln.item.code,
            "quantity": {"value": s(ln.quantity), "uom": ln.item.base_uom.code},
            "scrap_percent": s(ln.scrap_percent),
            "cost_share_percent": s(ln.cost_share_percent),
            "presentation_id": s(ln.presentation_id),
            "is_optional": ln.is_optional,
            "substitute_group": ln.substitute_group,
        }

    return {
        "id": s(b.id),
        "tenant_id": s(b.tenant_id),
        "company_id": s(b.company_id),
        "code": b.code,
        "name": b.name,
        "status": "ARCHIVED" if b.bom_status == Bom.BomStatus.OBSOLETE else "ACTIVE",
        "bom_status": b.bom_status,
        "bom_type": b.bom_type,
        "item_id": s(b.item_id),
        "presentation_id": s(b.presentation_id),
        "version_no": b.version_no,
        "valid_from": b.valid_from.isoformat(),
        "valid_to": b.valid_to.isoformat() if b.valid_to else None,
        "output_qty": s(b.output_qty),
        "yield_percent": s(b.yield_percent),
        "normal_waste_percent": s(b.normal_waste_percent),
        "notes": b.notes,
        "components": {
            "lines": [line(ln) for ln in lines if ln.role == BomLine.Role.COMPONENT],
            "byproducts": [line(ln) for ln in lines if ln.role == BomLine.Role.BYPRODUCT],
        },
        "version": b.version,
    }


def _node(n: core.Node, codes: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "item_id": s(n.item_id),
        "item_code": codes.get(n.item_id, ""),
        "level": n.level,
        "quantity": s(n.quantity),
        "bom_id": s(n.bom_id),
        "is_byproduct": n.is_byproduct,
        "scrap_percent": s(n.scrap_percent),
        "children": [_node(c, codes) for c in n.children],
    }


class BomListView(InventoryView):
    perm, resource_type = "inventory.bom", "inventory.bom"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return bom_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", Bom.objects.all(), company)
        return self.paginate(
            request,
            qs,
            search_fields=("code", "name"),
            filter_fields={"bom_status": "bom_status", "item_id": "item_id", "bom_type": "bom_type", "code": "code"},
            sort_fields=("code", "version_no", "valid_from"),
        )

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = parse_bom(body(request))
        return self.command(
            request, "inventory.bom.create", lambda u: (self.serialize(boms.create(u, company, data)), 201)
        )


class BomDetailView(BomListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        b = self.one("read", Bom.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(b), b)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        b = self.one("update", Bom.objects.all(), self.company(request), pk)
        self.if_match(request, b)
        data = parse_bom(body(request), base=b)
        return self.command(request, "inventory.bom.update", lambda u: (self.serialize(boms.update(u, b, data)), 200))

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        b = self.one("delete", Bom.objects.all(), self.company(request), pk)

        def run(u: Any) -> tuple[Any, int]:
            boms.delete(u, b)
            return None, 204

        return self.command(request, "inventory.bom.delete", run)


BOM_ACTIONS = {"activate": "update", "obsolete": "void", "copy-version": "create"}


class BomActionView(BomListView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:  # type: ignore[override]
        if action not in BOM_ACTIONS:
            raise NotFound("unknown action")
        company = self.company(request)
        b = self.one(BOM_ACTIONS[action], Bom.objects.all(), company, pk)
        data = body(request)
        if action == "activate":
            _only(data, {"supersede"})
            supersede = bool(data.get("supersede"))
            return self.command(
                request,
                "inventory.bom.activate",
                lambda u: (self.serialize(boms.activate(u, b, supersede=supersede)), 200),
            )
        if action == "obsolete":
            _only(data, {"reason"})
            return self.command(request, "inventory.bom.obsolete", lambda u: (self.serialize(boms.obsolete(u, b)), 200))
        _only(data, {"valid_from", "valid_to"})
        start, end = _date(data.get("valid_from"), "valid_from"), _date(data.get("valid_to"), "valid_to")
        return self.command(
            request,
            "inventory.bom.copy_version",
            lambda u: (self.serialize(boms.copy_version(u, b, valid_from=start, valid_to=end)), 201),
        )


class BomExplodeView(InventoryView):
    perm, resource_type = "inventory.bom", "inventory.bom_explosion"

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        b = self.one("read", Bom.objects.all(), company, pk)
        qty = decimal(request.query_params.get("qty") or s(b.output_qty * b.yield_percent / 100), "qty") or Decimal(0)
        on = _date(request.query_params.get("date"), "date", clock.business_date()) or clock.business_date()
        levels = request.query_params.get("levels", "multi")
        if levels not in ("multi", "single"):
            raise ValidationFailed("levels must be multi or single", pointer="/levels", reason="enum")
        exp = boms.explode(b, qty, on, multi_level=levels == "multi")
        codes: dict[uuid.UUID, str] = {c.item_id: c.item_code for c in exp.leaves}
        from inventory.models import Item

        codes.update(dict(Item.objects.filter(company_id=company, id__in=_ids(exp.tree)).values_list("id", "code")))
        data = {
            "id": s(b.id),
            "bom_id": s(b.id),
            "quantity": s(qty),
            "date": on.isoformat(),
            "tree": _node(exp.tree, codes),
            "leaves": [
                {
                    "item_id": s(c.item_id),
                    "item_code": c.item_code,
                    "quantity": s(c.quantity),
                    "unit_cost": s(c.unit_cost),
                    "value": money(c.value),
                }
                for c in exp.leaves
            ],
            "total_cost": money(exp.total_cost),
            "unit_cost": s(boms.unit_cost_of(exp)),
        }
        out = self.serialize(data)
        if "leaves" in out and "total_cost" not in out:  # cost fields hidden: strip them inside the leaves too
            out["leaves"] = [
                {k: v for k, v in leaf.items() if k not in ("unit_cost", "value")} for leaf in out["leaves"]
            ]
        return Response(out)


def _ids(n: core.Node) -> list[uuid.UUID]:
    return [n.item_id, *(i for c in n.children for i in _ids(c))]
