"""Purchasing template (owner P11b): internal requisitions — imported as **DRAFT** only (F-INV-REQ).

The owner service checks ``purchasing.internal_requisition.create|update`` itself (the engine checks them too),
assigns the number from the company sequence and the requester from the importing principal. An import never
submits, approves or sources a requisition; a non-DRAFT requisition is never modified by an import. Warehouse
and items are resolved by code inside the job's company only.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q, QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from inventory.models import Item, Warehouse
from kernel.interfaces.common import DocumentRef
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, current_uow
from purchasing.requisitions import service as requisitions
from purchasing.requisitions.models import InternalRequisition

SOURCE = "dataio.import"


class InternalRequisitionsTemplate(Template):
    entity = "internal-requisitions"
    label = "Requisiciones internas (se importan en BORRADOR)"
    module = "purchasing"
    owner = "P11b"
    feature = "F-INV-REQ"
    natural_key = ("reference",)
    read_permission = "purchasing.internal_requisition.read"
    create_permission = "purchasing.internal_requisition.create"
    update_permission = "purchasing.internal_requisition.update"
    document = True
    columns = (
        Column(
            "reference",
            required=True,
            max_length=40,
            help="Referencia del documento en el archivo (agrupa sus líneas; al exportar = número)",
            example="REQ-IMP-1",
        ),
        Column("document_date", type="date", help="Fecha del documento", example="2026-11-09"),
        Column("needed_by", type="date", required=True, help="Fecha requerida", example="2026-11-20"),
        Column(
            "requesting_warehouse_code",
            required=True,
            max_length=40,
            help="Bodega solicitante (misma empresa)",
            example="PRINCIPAL",
        ),
        Column("priority", allowed=("LOW", "NORMAL", "HIGH", "URGENT"), help="Prioridad", example="NORMAL"),
        Column("notes", max_length=10000, help="Notas"),
        Column("item_code", required=True, max_length=40, line=True, help="Ítem solicitado", example="ARROZ"),
        Column("quantity", type="decimal", required=True, line=True, help="Cantidad (unidad base)", example="10"),
        Column("description", max_length=255, line=True, help="Descripción de la línea"),
        Column(
            "status",
            allowed=(
                "DRAFT",
                "SUBMITTED",
                "APPROVED",
                "REJECTED",
                "IN_PROGRESS",
                "FULFILLED",
                "PARTIALLY_FULFILLED_CLOSED",
                "CANCELLED",
            ),
            help="Siempre DRAFT al importar (el resto solo se exporta como información)",
            example="DRAFT",
        ),
    )
    examples = (
        {
            "reference": "REQ-IMP-1",
            "needed_by": "2026-11-20",
            "requesting_warehouse_code": "PRINCIPAL",
            "priority": "NORMAL",
            "item_code": "ARROZ",
            "quantity": "10",
            "status": "DRAFT",
        },
    )
    notes = (
        "Los documentos importados quedan en DRAFT; enviarlos y aprobarlos sigue el flujo normal "
        "(segregación de funciones)."
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        ref = str(rec.values["reference"])
        return (
            InternalRequisition.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
            .filter(Q(number__iexact=ref) | Q(source__type=SOURCE, source__number=ref))
            .order_by("created_at")
            .first()
        )

    def current(self, obj: InternalRequisition) -> dict[str, Any]:
        src = obj.source or {}
        return {
            "reference": src.get("number") if src.get("type") == SOURCE and src.get("number") else obj.number,
            "document_date": obj.document_date,
            "needed_by": obj.needed_by,
            "requesting_warehouse_code": Warehouse.objects.filter(
                company_id=obj.company_id, id=obj.requesting_warehouse_id
            )
            .values_list("code", flat=True)
            .first(),
            "priority": obj.priority,
            "notes": obj.notes or None,
            "status": obj.status,
        }

    def current_lines(self, obj: InternalRequisition) -> list[dict[str, Any]]:
        codes = dict(Item.objects.filter(company_id=obj.company_id).values_list("id", "code"))
        return [
            {"item_code": codes.get(ln.item_id), "quantity": ln.quantity, "description": ln.description or None}
            for ln in obj.lines.order_by("line_no")
        ]

    def _data(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, base: dict[str, Any]) -> dict[str, Any]:
        v = {**base, **{k: rec.values.get(k) for k in rec.present}}
        wh = Warehouse.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=str(v.get("requesting_warehouse_code") or "")
        ).first()
        if wh is None:
            raise RowError("not_found", "bodega solicitante no existe en esta empresa", "requesting_warehouse_code")
        lines = []
        for i, ln in enumerate(rec.lines, start=1):
            item = Item.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=str(ln.get("item_code") or "")
            ).first()
            if item is None:
                raise RowError("not_found", f"ítem {ln.get('item_code')!r} no existe en esta empresa", "item_code")
            line: dict[str, Any] = {"line_no": i, "item_id": str(item.id), "quantity": str(ln["quantity"])}
            if ln.get("description"):
                line["description"] = ln["description"]
            lines.append(line)
        data: dict[str, Any] = {
            "requesting_warehouse_id": str(wh.id),
            "needed_by": v["needed_by"].isoformat(),
            "lines": lines,
        }
        if v.get("document_date"):
            data["document_date"] = v["document_date"].isoformat()
        if v.get("priority"):
            data["priority"] = v["priority"]
        if v.get("notes"):
            data["notes"] = v["notes"]
        return data

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        if rec.values.get("status") not in (None, "DRAFT"):
            raise RowError(
                "invalid_state_transition",
                "los documentos se importan solo en DRAFT (no se puede saltar la aprobación)",
                "status",
            )
        outer = current_uow()
        ref = DocumentRef(SOURCE, outer.id if outer is not None else uuid.uuid4(), str(rec.values["reference"]))
        return requisitions.create(uow, company_id, self._data(uow.ctx, company_id, rec, {}), source=ref)

    def update(self, uow: UnitOfWork, obj: InternalRequisition, rec: Record, changed: set[str]) -> Any:
        if obj.status != "DRAFT":
            raise RowError(
                "invalid_state_transition",
                f"la requisición {obj.number} está en {obj.status}; una importación solo modifica borradores",
                "status",
            )
        if rec.values.get("status") not in (None, "DRAFT"):
            raise RowError(
                "invalid_state_transition",
                "los documentos se importan solo en DRAFT (no se puede saltar la aprobación)",
                "status",
            )
        data = self._data(uow.ctx, obj.company_id, rec, self.current(obj))
        if "__lines__" not in changed and not rec.lines:
            data.pop("lines", None)
        return requisitions.update(uow, obj, data)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return InternalRequisition.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("number")


TEMPLATES = (InternalRequisitionsTemplate,)
