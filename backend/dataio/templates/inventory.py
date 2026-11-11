"""Inventory templates (owners P09a/P09b/P09c): issue concepts, presentations, locations, imported GTINs, BOMs and
portioning templates (F-INV-ISSUE / F-INV-PRES / F-INV-LOC / F-INV-BARCODE / F-INV-BOM / F-INV-PORTION).

Inventory services leave policy to their views; the engine checks the same ``inventory.<resource>.<verb>``
codes per row. Items, warehouses and presentations are resolved by code inside the job's company only.
BOMs import as DRAFT: a changed ACTIVE/OBSOLETE BOM gets a new DRAFT version (activation stays a user action).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from inventory import masters
from inventory.barcodes import service as barcodes
from inventory.barcodes.models import Barcode
from inventory.bom import service as boms
from inventory.bom.models import Bom
from inventory.issues import service as issues
from inventory.issues.models import IssueConcept
from inventory.locations import service as locations
from inventory.models import Item, Location, Presentation, Warehouse
from inventory.transformations import service as portioning
from inventory.transformations.models import PortioningTemplate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

DIMS = ("cost_center", "business_unit", "aux_abierto", "item_contable")
D = Decimal


def item_by_code(ctx: TenantContext, company_id: uuid.UUID, code: Any, column: str) -> Item:
    item = (
        Item.objects.select_related("base_uom")
        .filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=str(code or ""))
        .first()
    )
    if item is None:
        raise RowError("not_found", f"ítem {code!r} no existe en esta empresa", column)
    return item


def warehouse_by_code(ctx: TenantContext, company_id: uuid.UUID, code: Any, column: str) -> Warehouse:
    wh = Warehouse.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=str(code or "")).first()
    if wh is None:
        raise RowError("not_found", f"bodega {code!r} no existe en esta empresa", column)
    return wh


def _code_of(model: Any, company_id: uuid.UUID, pk: Any) -> str | None:
    if pk is None:
        return None
    value: str | None = model.objects.filter(company_id=company_id, id=pk).values_list("code", flat=True).first()
    return value


# --------------------------------------------------------------------------- issue concepts


class IssueConceptsTemplate(Template):
    entity = "issue-concepts"
    label = "Conceptos de salida de inventario"
    module = "inventory"
    owner = "P09b"
    feature = "F-INV-ISSUE"
    natural_key = ("code",)
    read_permission = "inventory.issue_concept.read"
    create_permission = "inventory.issue_concept.create"
    update_permission = "inventory.issue_concept.update"
    columns = (
        Column("code", required=True, max_length=40, help="Código (se guarda en mayúsculas)", example="CONSUMO"),
        Column("name", required=True, max_length=200, help="Nombre", example="Consumo interno"),
        Column("kind", allowed=tuple(IssueConcept.Kind.values), help="Tipo de salida", example="CONSUMPTION"),
        Column("account_role", required=True, max_length=60, help="Rol contable de la contrapartida (débito)", example="EXPENSE"),
        Column("requires_dimension", type="boolean", help="Exige centro de costo", example="true"),
        Column("required_dims", type="list", allowed=DIMS, unordered=True, help="Dimensiones adicionales exigidas, separadas por |", example="cost_center"),
        Column("requires_reason", type="boolean", help="Exige motivo", example="false"),
        Column("reason_codes", type="list", help="Códigos de motivo permitidos, separados por |"),
        Column("requires_approval", type="boolean", help="Requiere aprobación", example="false"),
        Column("approval_threshold_value", type="decimal", help="Valor desde el cual se aprueba (COP)"),
        Column("status", allowed=("ACTIVE", "ARCHIVED"), help="ACTIVE o ARCHIVED", example="ACTIVE"),
    )
    examples = ({"code": "CONSUMO", "name": "Consumo interno", "kind": "CONSUMPTION", "account_role": "EXPENSE", "requires_dimension": "true", "required_dims": "cost_center", "status": "ACTIVE"},)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return IssueConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"]).first()

    def current(self, obj: IssueConcept) -> dict[str, Any]:
        return {
            "code": obj.code,
            "name": obj.name,
            "kind": obj.kind,
            "account_role": obj.account_role,
            "requires_dimension": obj.requires_dimension,
            "required_dims": tuple(sorted(obj.required_dims or ())),
            "requires_reason": obj.requires_reason,
            "reason_codes": tuple(obj.reason_codes or ()),
            "requires_approval": obj.requires_approval,
            "approval_threshold_value": obj.approval_threshold_value,
            "status": obj.status,
        }

    def _data(self, rec: Record, names: set[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for n in names - {"status"}:
            v = rec.values.get(n)
            if n in ("required_dims", "reason_codes"):
                v = list(v or ())
            elif n in ("requires_dimension", "requires_reason", "requires_approval"):
                v = bool(v)
            elif n == "approval_threshold_value":
                v = None if v is None else str(v)
            elif n == "kind" and v is None:
                continue
            out[n] = v
        return out

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        c = issues.create_concept(uow, company_id, self._data(rec, set(rec.present)))
        if rec.values.get("status") == "ARCHIVED":
            c = issues.archive_concept(uow, c)
        return c

    def update(self, uow: UnitOfWork, obj: IssueConcept, rec: Record, changed: set[str]) -> Any:
        data = self._data(rec, changed)
        if data:
            obj = issues.update_concept(uow, obj, data)
        if "status" in changed:
            if rec.values.get("status") != "ARCHIVED":
                raise RowError("not_updatable", "un concepto archivado no se reactiva por importación", "status")
            obj = issues.archive_concept(uow, obj)
        return obj

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return IssueConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code")


# --------------------------------------------------------------------------- presentations


class PresentationsTemplate(Template):
    entity = "presentations"
    label = "Presentaciones de ítems (empaques con factor a la unidad base)"
    module = "inventory"
    owner = "P09a"
    feature = "F-INV-PRES"
    natural_key = ("item_code", "code")
    read_permission = "inventory.presentation.read"
    create_permission = "inventory.presentation.create"
    update_permission = "inventory.presentation.update"
    columns = (
        Column("item_code", required=True, max_length=40, help="Código del ítem (misma empresa)", example="ARROZ"),
        Column("code", required=True, max_length=40, help="Código de la presentación (único por ítem)", example="BOLSA500"),
        Column("name", required=True, max_length=120, help="Nombre", example="Bolsa 500 g"),
        Column("uom", required=True, max_length=20, help="Unidad de empaque (caja, bolsa…)", example="bolsa"),
        Column("factor", type="decimal", required=True, help="Unidades base por empaque (> 0, exacto)", example="0.5"),
        Column("precision", type="integer", help="Decimales permitidos del empaque (0–6)", example="0"),
        Column("barcode", max_length=64, help="Código de barras del empaque (opcional)"),
        Column("is_default_purchase", type="boolean", help="Presentación de compra por defecto", example="false"),
        Column("is_default_sale", type="boolean", help="Presentación de venta por defecto", example="false"),
        Column("status", allowed=("ACTIVE", "ARCHIVED"), help="ACTIVE o ARCHIVED", example="ACTIVE"),
    )
    examples = ({"item_code": "ARROZ", "code": "BOLSA500", "name": "Bolsa 500 g", "uom": "bolsa", "factor": "0.5", "precision": "0", "status": "ACTIVE"},)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        item = item_by_code(ctx, company_id, rec.values["item_code"], "item_code")
        return Presentation.objects.filter(company_id=company_id, item=item, code__iexact=rec.values["code"]).first()

    def current(self, obj: Presentation) -> dict[str, Any]:
        return {
            "item_code": _code_of(Item, obj.company_id, obj.item_id),
            "code": obj.code,
            "name": obj.name,
            "uom": obj.uom,
            "factor": obj.factor,
            "precision": obj.precision,
            "barcode": obj.barcode,
            "is_default_purchase": obj.is_default_purchase,
            "is_default_sale": obj.is_default_sale,
            "status": obj.status,
        }

    def _data(self, rec: Record, names: set[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for n in names - {"status", "item_code", "code"}:
            v = rec.values.get(n)
            if n in ("is_default_purchase", "is_default_sale"):
                v = bool(v)
            elif n == "precision" and v is None:
                v = 0
            elif n == "factor":
                v = str(v)
            out[n] = v
        return out

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        item = item_by_code(uow.ctx, company_id, rec.values["item_code"], "item_code")
        p = masters.create_presentation(uow, item, {"code": rec.values["code"], **self._data(rec, set(rec.present))})
        if rec.values.get("status") == "ARCHIVED":
            p = masters.set_presentation_status(uow, p, "ARCHIVED")
        return p

    def update(self, uow: UnitOfWork, obj: Presentation, rec: Record, changed: set[str]) -> Any:
        if "code" in changed:
            raise RowError("immutable", "el código de la presentación no cambia", "code")
        data = self._data(rec, changed)
        if data:
            obj = masters.update_presentation(uow, obj, data)
        if "status" in changed:
            obj = masters.set_presentation_status(uow, obj, rec.values.get("status") or "ACTIVE")
        return obj

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return Presentation.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("item__code", "code")


# --------------------------------------------------------------------------- locations


class LocationsTemplate(Template):
    entity = "locations"
    label = "Ubicaciones de bodega (árbol de ubicaciones)"
    module = "inventory"
    owner = "P09a"
    feature = "F-INV-LOC"
    natural_key = ("warehouse_code", "code")
    read_permission = "inventory.location.read"
    create_permission = "inventory.location.create"
    update_permission = "inventory.location.update"
    parent_column = "parent_code"
    columns = (
        Column("warehouse_code", required=True, max_length=40, help="Código de la bodega (misma empresa)", example="PRINCIPAL"),
        Column("code", required=True, max_length=40, help="Código de la ubicación (único por bodega, sin '/')", example="A-01"),
        Column("name", required=True, max_length=200, help="Nombre", example="Pasillo A, estante 1"),
        Column("location_type", required=True, allowed=tuple(Location.LocationType.values), help="VIEW, INTERNAL, TRANSIT o SCRAP", example="INTERNAL"),
        Column("parent_code", max_length=40, help="Código de la ubicación padre (vacío = raíz de la bodega); puede venir después", example="ALMACEN"),
        Column("barcode", max_length=64, help="Código de barras de la ubicación"),
        Column("capacity", type="decimal", help="Capacidad (opcional)"),
        Column("allow_mixed_lots", type="boolean", help="Permite mezclar lotes", example="true"),
    )
    examples = ({"warehouse_code": "PRINCIPAL", "code": "A-01", "name": "Pasillo A, estante 1", "location_type": "INTERNAL", "parent_code": "ALMACEN", "allow_mixed_lots": "true"},)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        wh = warehouse_by_code(ctx, company_id, rec.values["warehouse_code"], "warehouse_code")
        return Location.objects.filter(company_id=company_id, warehouse=wh, code__iexact=rec.values["code"]).first()

    def _parent(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> uuid.UUID | None:
        code = rec.values.get("parent_code")
        wh = warehouse_by_code(ctx, company_id, rec.values["warehouse_code"], "warehouse_code")
        if not code:
            return wh.root_location_id
        parent = Location.objects.filter(company_id=company_id, warehouse=wh, code__iexact=code).first()
        if parent is None:
            raise RowError("not_found", f"ubicación padre {code!r} no existe en la bodega {wh.code}", "parent_code")
        return parent.id

    def current(self, obj: Location) -> dict[str, Any]:
        return {
            "warehouse_code": _code_of(Warehouse, obj.company_id, obj.warehouse_id),
            "code": obj.code,
            "name": obj.name,
            "location_type": obj.location_type,
            "parent_code": _code_of(Location, obj.company_id, obj.parent_id),
            "barcode": obj.barcode,
            "capacity": obj.capacity,
            "allow_mixed_lots": obj.allow_mixed_lots,
        }

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        wh = warehouse_by_code(uow.ctx, company_id, v["warehouse_code"], "warehouse_code")
        mixed = v.get("allow_mixed_lots")
        return locations.create_location(
            uow,
            wh,
            locations.LocationInput(
                code=v["code"],
                name=v["name"],
                location_type=v["location_type"],
                parent_id=self._parent(uow.ctx, company_id, rec),
                barcode=v.get("barcode"),
                capacity=v.get("capacity"),
                allow_mixed_lots=True if mixed is None else bool(mixed),
            ),
        )

    def update(self, uow: UnitOfWork, obj: Location, rec: Record, changed: set[str]) -> Any:
        if obj.parent_id is None and changed & {"parent_code", "location_type"}:
            raise RowError("immutable", "la raíz de la bodega no se mueve ni cambia de tipo", "parent_code")
        changes: dict[str, Any] = {}
        for n in ("name", "barcode", "capacity", "allow_mixed_lots", "location_type"):
            if n in changed:
                changes[n] = rec.values.get(n) if n != "allow_mixed_lots" else bool(rec.values.get(n))
        if "parent_code" in changed:
            changes["parent_id"] = self._parent(uow.ctx, obj.company_id, rec)
        return locations.update_location(uow, obj, changes)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return Location.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("warehouse__code", "path")


# --------------------------------------------------------------------------- GTIN (barcodes import)


class GtinsTemplate(Template):
    entity = "gtins"
    label = "Códigos GTIN importados (validados mod-10, propiedad no reclamada)"
    module = "inventory"
    owner = "P09c"
    feature = "F-INV-BARCODE"
    natural_key = ("value",)
    read_permission = "inventory.barcode.read"
    create_permission = "inventory.barcode.create"
    update_permission = "inventory.barcode.create"
    updatable = False
    columns = (
        Column("value", required=True, max_length=14, help="GTIN-8/12/13/14 existente (dígito de control válido)", example="7701234567897"),
        Column("owner_type", required=True, allowed=("item", "presentation"), help="item o presentation", example="item"),
        Column("item_code", required=True, max_length=40, help="Código del ítem (misma empresa)", example="ARROZ"),
        Column("presentation_code", max_length=40, help="Código de la presentación (si owner_type = presentation)"),
    )
    examples = ({"value": "7701234567897", "owner_type": "item", "item_code": "ARROZ"},)
    notes = "D-004: los GTIN importados se validan pero nunca se declaran propios."

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return Barcode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, value=str(rec.values["value"]).strip()).first()

    def current(self, obj: Barcode) -> dict[str, Any]:
        pres = None
        if obj.owner_type == "presentation":
            pres = _code_of(Presentation, obj.company_id, obj.owner_id)
        return {
            "value": obj.value,
            "owner_type": obj.owner_type,
            "item_code": _code_of(Item, obj.company_id, obj.item_id),
            "presentation_code": pres,
        }

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        item = item_by_code(uow.ctx, company_id, v["item_code"], "item_code")
        owner_id = item.id
        if v["owner_type"] == "presentation":
            p = Presentation.objects.filter(company_id=company_id, item=item, code__iexact=str(v.get("presentation_code") or "")).first()
            if p is None:
                raise RowError("not_found", "presentación no existe para el ítem en esta empresa", "presentation_code")
            owner_id = p.id
        return barcodes.register_imported(uow, company_id, v["owner_type"], owner_id, v["value"])

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return Barcode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, source="IMPORTED").order_by("value")


# --------------------------------------------------------------------------- BOMs


class BomsTemplate(Template):
    entity = "boms"
    label = "Listas de materiales (BOM) — se importan en BORRADOR"
    module = "inventory"
    owner = "P09c"
    feature = "F-INV-BOM"
    natural_key = ("code",)
    read_permission = "inventory.bom.read"
    create_permission = "inventory.bom.create"
    update_permission = "inventory.bom.update"
    document = True
    columns = (
        Column("code", required=True, max_length=40, help="Código de la lista (una fila por componente)", example="PAN-01"),
        Column("name", required=True, max_length=200, help="Nombre", example="Pan tajado"),
        Column("item_code", required=True, max_length=40, help="Ítem producido", example="PAN"),
        Column("bom_type", allowed=("PRODUCTION", "KIT"), help="PRODUCTION o KIT", example="PRODUCTION"),
        Column("output_qty", type="decimal", required=True, help="Cantidad producida por lote (unidad base)", example="10"),
        Column("valid_from", type="date", required=True, help="Vigente desde", example="2026-01-01"),
        Column("valid_to", type="date", help="Vigente hasta"),
        Column("yield_percent", type="decimal", help="Rendimiento % (por defecto 100)", example="100"),
        Column("normal_waste_percent", type="decimal", help="Merma normal %", example="0"),
        Column("component_code", required=True, max_length=40, line=True, help="Ítem componente", example="HARINA"),
        Column("quantity", type="decimal", required=True, line=True, help="Cantidad por lote", example="5"),
        Column("scrap_percent", type="decimal", line=True, help="Desperdicio % del componente", example="0"),
        Column("is_byproduct", type="boolean", line=True, help="true si es subproducto", example="false"),
        Column("cost_share_percent", type="decimal", line=True, help="Participación en el costo (solo subproductos)"),
        Column("status", allowed=("DRAFT", "ACTIVE", "OBSOLETE"), help="La importación crea o modifica solo en DRAFT (ACTIVE/OBSOLETE se exportan como información; activar es una acción del usuario)", example="DRAFT"),
    )
    examples = (
        {"code": "PAN-01", "name": "Pan tajado", "item_code": "PAN", "bom_type": "PRODUCTION", "output_qty": "10", "valid_from": "2026-01-01", "component_code": "HARINA", "quantity": "5", "status": "DRAFT"},
        {"code": "PAN-01", "name": "Pan tajado", "item_code": "PAN", "bom_type": "PRODUCTION", "output_qty": "10", "valid_from": "2026-01-01", "component_code": "LEVADURA", "quantity": "0.1", "status": "DRAFT"},
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return (
            Bom.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"])
            .order_by("-version_no")
            .first()
        )

    def current(self, obj: Bom) -> dict[str, Any]:
        return {
            "code": obj.code,
            "name": obj.name,
            "item_code": _code_of(Item, obj.company_id, obj.item_id),
            "bom_type": obj.bom_type,
            "output_qty": obj.output_qty,
            "valid_from": obj.valid_from,
            "valid_to": obj.valid_to,
            "yield_percent": obj.yield_percent,
            "normal_waste_percent": obj.normal_waste_percent,
            "status": obj.bom_status,
        }

    def current_lines(self, obj: Bom) -> list[dict[str, Any]]:
        return [
            {
                "component_code": _code_of(Item, obj.company_id, ln.item_id),
                "quantity": ln.quantity,
                "scrap_percent": ln.scrap_percent,
                "is_byproduct": ln.role == "BYPRODUCT",
                "cost_share_percent": ln.cost_share_percent,
            }
            for ln in obj.lines.order_by("line_no")
        ]

    def _input(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, base: dict[str, Any]) -> boms.BomInput:
        v = {**base, **{k: rec.values.get(k) for k in rec.present}}
        wanted = rec.values.get("status")
        if wanted not in (None, "DRAFT") and wanted != base.get("status"):
            raise RowError("invalid_state_transition", "las listas se importan solo en DRAFT (activar es una acción del usuario)", "status")
        lines = tuple(
            boms.LineInput(
                item_id=item_by_code(ctx, company_id, ln.get("component_code"), "component_code").id,
                quantity=ln["quantity"],
                scrap_percent=ln.get("scrap_percent") or D(0),
                is_byproduct=bool(ln.get("is_byproduct")),
                cost_share_percent=ln.get("cost_share_percent"),
            )
            for ln in rec.lines
        )
        return boms.BomInput(
            code=v["code"],
            name=v["name"],
            item_id=item_by_code(ctx, company_id, v["item_code"], "item_code").id,
            valid_from=v["valid_from"],
            output_qty=v["output_qty"],
            lines=lines,
            valid_to=v.get("valid_to"),
            yield_percent=v.get("yield_percent") or D(100),
            normal_waste_percent=v.get("normal_waste_percent") or D(0),
            bom_type=v.get("bom_type") or "PRODUCTION",
        )

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        return boms.create(uow, company_id, self._input(uow.ctx, company_id, rec, {}))

    def update(self, uow: UnitOfWork, obj: Bom, rec: Record, changed: set[str]) -> Any:
        data = self._input(uow.ctx, obj.company_id, rec, self.current(obj))
        if obj.bom_status == "DRAFT":
            return boms.update(uow, obj, data)
        # ACTIVE/OBSOLETE are never edited: a new DRAFT version (approval/activation stays with the user).
        return boms.create(uow, obj.company_id, data)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        latest = []
        seen: set[str] = set()
        for b in Bom.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code", "-version_no").only("id", "code"):
            if b.code.lower() not in seen:
                seen.add(b.code.lower())
                latest.append(b.id)
        return Bom.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id__in=latest).order_by("code")


# --------------------------------------------------------------------------- portioning templates


class PortioningTemplatesTemplate(Template):
    entity = "portioning-templates"
    label = "Plantillas de porcionado / transformación"
    module = "inventory"
    owner = "P09c"
    feature = "F-INV-PORTION"
    natural_key = ("code",)
    read_permission = "inventory.portioning_template.read"
    create_permission = "inventory.portioning_template.create"
    update_permission = "inventory.portioning_template.update"
    columns = (
        Column("code", required=True, max_length=40, help="Código (una fila por producto de salida)", example="RES-01"),
        Column("name", required=True, max_length=200, help="Nombre", example="Despiece de res"),
        Column("input_item_code", required=True, max_length=40, help="Ítem de entrada", example="RES"),
        Column("tolerance_percent", type="decimal", help="Tolerancia de rendimiento % (D-015, por defecto 2)", example="2"),
        Column("waste_percent", type="decimal", help="Merma esperada %", example="5"),
        Column("output_code", required=True, max_length=40, line=True, help="Ítem de salida", example="LOMO"),
        Column("expected_yield_percent", type="decimal", required=True, line=True, help="Rendimiento esperado %", example="20"),
        Column("cost_share_percent", type="decimal", line=True, help="Participación en el costo % (todas o ninguna; suman 100)"),
        Column("is_main", type="boolean", line=True, help="Salida principal (recibe el redondeo)", example="true"),
    )
    examples = (
        {"code": "RES-01", "name": "Despiece de res", "input_item_code": "RES", "tolerance_percent": "2", "waste_percent": "5", "output_code": "LOMO", "expected_yield_percent": "20", "is_main": "true"},
        {"code": "RES-01", "name": "Despiece de res", "input_item_code": "RES", "tolerance_percent": "2", "waste_percent": "5", "output_code": "MOLIDA", "expected_yield_percent": "75", "is_main": "false"},
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return PortioningTemplate.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"]).first()

    def current(self, obj: PortioningTemplate) -> dict[str, Any]:
        return {
            "code": obj.code,
            "name": obj.name,
            "input_item_code": _code_of(Item, obj.company_id, obj.input_item_id),
            "tolerance_percent": obj.tolerance_percent,
            "waste_percent": obj.waste_percent,
        }

    def current_lines(self, obj: PortioningTemplate) -> list[dict[str, Any]]:
        return [
            {
                "output_code": _code_of(Item, obj.company_id, o.item_id),
                "expected_yield_percent": o.expected_yield_percent,
                "cost_share_percent": o.cost_share_percent,
                "is_main": o.is_main,
            }
            for o in obj.outputs.order_by("line_no")
        ]

    def _input(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, base: dict[str, Any]) -> portioning.TemplateInput:
        v = {**base, **{k: rec.values.get(k) for k in rec.present}}
        outputs = tuple(
            portioning.TemplateOutputInput(
                item_id=item_by_code(ctx, company_id, o.get("output_code"), "output_code").id,
                expected_yield_percent=o["expected_yield_percent"],
                cost_share_percent=o.get("cost_share_percent"),
                is_main=bool(o.get("is_main")),
            )
            for o in rec.lines
        )
        tol = v.get("tolerance_percent")
        return portioning.TemplateInput(
            code=v["code"],
            name=v["name"],
            input_item_id=item_by_code(ctx, company_id, v["input_item_code"], "input_item_code").id,
            outputs=outputs,
            tolerance_percent=portioning.DEFAULT_TOLERANCE if tol is None else tol,
            waste_percent=v.get("waste_percent") or D(0),
        )

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        return portioning.create_template(uow, company_id, self._input(uow.ctx, company_id, rec, {}))

    def update(self, uow: UnitOfWork, obj: PortioningTemplate, rec: Record, changed: set[str]) -> Any:
        return portioning.update_template(uow, obj, self._input(uow.ctx, obj.company_id, rec, self.current(obj)))

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return PortioningTemplate.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code")


TEMPLATES = (
    IssueConceptsTemplate,
    PresentationsTemplate,
    LocationsTemplate,
    GtinsTemplate,
    BomsTemplate,
    PortioningTemplatesTemplate,
)
