"""Sales template (owner P10a): price formulas (F-SAL-FORMULA bindings of approved workflow formulas).

``sales.pricing.service`` checks ``sales.price_formula.create|update`` itself; the engine checks them too. The
workflow formula and the scoped item are resolved by code inside the job's company only.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.models import PriceFormula
from sales.pricing import service as pricing

SCOPE_TEXT = ("category", "customer_group", "channel", "currency")


class PriceFormulasTemplate(Template):
    entity = "price-formulas"
    label = "Fórmulas de precio (vínculo a fórmulas aprobadas)"
    module = "sales"
    owner = "P10a"
    feature = "F-SAL-FORMULA"
    natural_key = ("code",)
    read_permission = "sales.price_formula.read"
    create_permission = "sales.price_formula.create"
    update_permission = "sales.price_formula.update"
    columns = (
        Column("code", required=True, max_length=60, help="Código del vínculo (no cambia)", example="MAYORISTA"),
        Column("name", max_length=200, help="Nombre", example="Precio mayorista"),
        Column(
            "formula_code",
            required=True,
            max_length=80,
            help="Código de la fórmula de precios aprobada (Flujos › Fórmulas)",
            example="PRECIO_MAYOR",
        ),
        Column("priority", type="integer", help="Prioridad (mayor gana; −10000…10000)", example="100"),
        Column("valid_from", type="date", help="Vigente desde", example="2026-01-01"),
        Column("valid_to", type="date", help="Vigente hasta"),
        Column("scope_item_code", max_length=40, help="Alcance: código de ítem (misma empresa)"),
        Column("scope_category", max_length=60, help="Alcance: categoría"),
        Column("scope_customer_group", max_length=60, help="Alcance: grupo de clientes", example="MAYORISTAS"),
        Column("scope_channel", max_length=60, help="Alcance: canal"),
        Column("scope_currency", max_length=3, help="Alcance: moneda ISO 4217", example="COP"),
        Column("status", allowed=("ACTIVE", "INACTIVE"), help="ACTIVE o INACTIVE", example="ACTIVE"),
    )
    examples = (
        {
            "code": "MAYORISTA",
            "name": "Precio mayorista",
            "formula_code": "PRECIO_MAYOR",
            "priority": "100",
            "valid_from": "2026-01-01",
            "scope_customer_group": "MAYORISTAS",
            "scope_currency": "COP",
            "status": "ACTIVE",
        },
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return PriceFormula.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"]
        ).first()

    def current(self, obj: PriceFormula) -> dict[str, Any]:
        from inventory.models import Item

        scope = obj.scope or {}
        item = scope.get("item_id")
        item_code = (
            Item.objects.filter(company_id=obj.company_id, id=item).values_list("code", flat=True).first()
            if isinstance(item, str)
            else None
        )
        return {
            "code": obj.code,
            "name": obj.name or None,
            "formula_code": obj.formula_code,
            "priority": obj.priority,
            "valid_from": obj.valid_from,
            "valid_to": obj.valid_to,
            "scope_item_code": item_code,
            **{f"scope_{k}": scope.get(k) for k in SCOPE_TEXT},
            "status": obj.status,
        }

    def _scope(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, base: dict[str, Any]) -> dict[str, Any]:
        from inventory.models import Item

        v = {**base, **{k: rec.values.get(k) for k in rec.present}}
        scope: dict[str, Any] = {k: v.get(f"scope_{k}") for k in SCOPE_TEXT if v.get(f"scope_{k}")}
        code = v.get("scope_item_code")
        if code:
            item = (
                Item.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code)
                .values_list("id", flat=True)
                .first()
            )
            if item is None:
                raise RowError("not_found", f"ítem {code!r} no existe en esta empresa", "scope_item_code")
            scope["item_id"] = str(item)
        return scope

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        data: dict[str, Any] = {
            "code": v["code"],
            "formula_code": v["formula_code"],
            "scope": self._scope(uow.ctx, company_id, rec, {}),
        }
        for n in ("name", "priority", "valid_from", "valid_to"):
            if v.get(n) is not None:
                data[n] = v[n].isoformat() if n.startswith("valid") else v[n]
        row = pricing.create_price_formula(uow, company_id, data)
        if v.get("status") == "INACTIVE":
            row = pricing.update_price_formula(uow, row, {"status": "INACTIVE"})
        return row

    def update(self, uow: UnitOfWork, obj: PriceFormula, rec: Record, changed: set[str]) -> Any:
        v = rec.values
        data: dict[str, Any] = {}
        for n in ("name", "priority", "status", "formula_code"):
            if n in changed:
                data[n] = v.get(n) if n != "name" else (v.get(n) or "")
        for n in ("valid_from", "valid_to"):
            if n in changed:
                data[n] = v[n].isoformat() if v.get(n) else None
        if any(n.startswith("scope_") for n in changed):
            data["scope"] = self._scope(uow.ctx, obj.company_id, rec, self.current(obj))
        return pricing.update_price_formula(uow, obj, data)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return PriceFormula.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code")


TEMPLATES = (PriceFormulasTemplate,)
