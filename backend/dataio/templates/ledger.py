"""Ledger templates (owner P05): the four dimension masters and the dimension rule matrix (DIMENSIONS-ADDENDUM §5).

Dimensions: code, description, node_type, parent_code, valid_from, valid_to, status. Two-pass parent resolution
(children may come before parents); cycles, cross-company parents and invalid parent types reject the whole
batch; export → import is a no-op. Writes go through ``ledger.dimensions.service`` only.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet
from django.db.models.functions import Lower

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger import rules as ledger_rules
from ledger.dimensions import service as dims
from ledger.models import DIMENSIONS, DimensionNode, DimensionRule


class DimensionsTemplate(Template):
    entity = "dimensions"
    label = "Dimensiones contables (centros de costo, unidades de negocio, auxiliares abiertos, ítems contables)"
    module = "ledger"
    owner = "P05"
    feature = "F-ACC-DIM"
    natural_key = ("dimension", "code")
    read_permission = "ledger.dimension.read"
    create_permission = "ledger.dimension.create"
    update_permission = "ledger.dimension.update"
    parent_column = "parent_code"
    columns = (
        Column("dimension", required=True, allowed=DIMENSIONS, help="cost_center, business_unit, aux_abierto o item_contable", example="cost_center", synonyms=("dimension_contable",)),
        Column("code", required=True, max_length=40, help="Código único por empresa y dimensión (sin distinguir mayúsculas)", example="ADM"),
        Column("description", required=True, max_length=200, help="Descripción", example="Administración"),
        Column("node_type", required=True, allowed=("AGRUPADOR", "DETALLE"), help="AGRUPADOR (no imputable) o DETALLE (imputable)", example="AGRUPADOR"),
        Column("parent_code", max_length=40, help="Código del AGRUPADOR padre (misma empresa y dimensión); puede venir después en el archivo", example=""),
        Column("valid_from", type="date", help="Vigente desde (AAAA-MM-DD)", example="2026-01-01"),
        Column("valid_to", type="date", help="Vigente hasta (opcional)"),
        Column("status", allowed=("ACTIVE", "ARCHIVED"), help="ACTIVE o ARCHIVED", example="ACTIVE"),
    )
    examples = (
        {"dimension": "cost_center", "code": "ADM", "description": "Administración", "node_type": "AGRUPADOR", "parent_code": "", "valid_from": "2026-01-01", "status": "ACTIVE"},
        {"dimension": "cost_center", "code": "ADM-01", "description": "Gerencia", "node_type": "DETALLE", "parent_code": "ADM", "valid_from": "2026-01-01", "status": "ACTIVE"},
    )

    def _qs(self, ctx: TenantContext, company_id: uuid.UUID, dimension: str) -> QuerySet[DimensionNode]:
        return DimensionNode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return (
            self._qs(ctx, company_id, rec.values["dimension"])
            .annotate(lc=Lower("code"))
            .filter(lc=str(rec.values["code"]).lower())
            .first()
        )

    def _parent_id(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> str | None:
        code = rec.values.get("parent_code")
        if not code:
            return None
        parent = self._qs(ctx, company_id, rec.values["dimension"]).annotate(lc=Lower("code")).filter(lc=str(code).lower()).first()
        if parent is None:
            # Never looked up in another company: a code of another company is simply unknown here.
            raise RowError("dimension_parent_unknown", f"padre {code!r} no existe en esta empresa y dimensión", "parent_code")
        return str(parent.id)

    def current(self, obj: DimensionNode) -> dict[str, Any]:
        parent = None
        if obj.parent_id:
            parent = DimensionNode.objects.filter(company_id=obj.company_id, id=obj.parent_id).values_list("code", flat=True).first()
        return {
            "dimension": obj.dimension,
            "code": obj.code,
            "description": obj.description,
            "node_type": obj.node_type,
            "parent_code": parent,
            "valid_from": obj.valid_from,
            "valid_to": obj.valid_to,
            "status": obj.status,
        }

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        node = dims.create_node(
            uow,
            company_id,
            v["dimension"],
            dims.NodeInput(
                code=v["code"],
                description=v["description"],
                node_type=v["node_type"],
                parent_id=self._parent_id(uow.ctx, company_id, rec),
                valid_from=v.get("valid_from"),
                valid_to=v.get("valid_to"),
            ),
        )
        if v.get("status") == "ARCHIVED":
            dims.archive_node(uow, node, "importación")
        return node

    def update(self, uow: UnitOfWork, obj: DimensionNode, rec: Record, changed: set[str]) -> Any:
        v = rec.values
        if "dimension" in changed:
            raise RowError("immutable", "la dimensión de un nodo no cambia", "dimension")
        patch: dict[str, Any] = {}
        for name in ("code", "description", "node_type", "valid_from", "valid_to"):
            if name in changed:
                patch[name] = v.get(name)
        if "parent_code" in changed:
            patch["parent_id"] = self._parent_id(uow.ctx, obj.company_id, rec)
        if patch:
            obj = dims.update_node(uow, obj, patch, obj.etag)
        if "status" in changed:
            obj = dims.archive_node(uow, obj, "importación") if v.get("status") == "ARCHIVED" else dims.unarchive_node(uow, obj, "importación")
        return obj

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return DimensionNode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("dimension", "path")


class DimensionRulesTemplate(Template):
    entity = "dimension-rules"
    label = "Matriz de reglas de dimensiones (requerida / permitida / prohibida)"
    module = "ledger"
    owner = "P05"
    feature = "F-ACC-DIM"
    natural_key = ("dimension", "scope_type", "scope_ref")
    read_permission = "ledger.dimension_rule.read"
    create_permission = "ledger.dimension_rule.update"
    update_permission = "ledger.dimension_rule.update"
    columns = (
        Column("dimension", required=True, allowed=DIMENSIONS, help="Dimensión", example="cost_center"),
        Column("scope_type", required=True, allowed=tuple(DimensionRule.ScopeType.values), help="account, account_range, document_type, concept, journal o stock_document", example="account_range"),
        Column("scope_ref", required=True, max_length=255, help="Cuenta, rango ('5105-5199' o '51*'), tipo de documento, concepto o diario", example="51*"),
        Column("mode", required=True, allowed=ledger_rules.MODES, help="REQUIRED, ALLOWED o FORBIDDEN", example="REQUIRED"),
        Column("priority", type="integer", help="Prioridad (mayor gana; empate → más restrictiva)", example="10"),
    )
    examples = ({"dimension": "cost_center", "scope_type": "account_range", "scope_ref": "51*", "mode": "REQUIRED", "priority": "10"},)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        return DimensionRule.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, dimension=v["dimension"], scope_type=v["scope_type"], scope_ref=v["scope_ref"]
        ).first()

    def current(self, obj: DimensionRule) -> dict[str, Any]:
        return {"dimension": obj.dimension, "scope_type": obj.scope_type, "scope_ref": obj.scope_ref, "mode": obj.mode, "priority": obj.priority}

    def _replace(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record, keep_existing: bool) -> None:
        ctx = uow.ctx
        v = rec.values
        new = ledger_rules.Rule(v["dimension"], v["scope_type"], v["scope_ref"], v["mode"], int(v.get("priority") or 0))
        rules = ledger_rules.load_rules(ctx, company_id)
        key = (new.dimension, new.scope_type, new.scope_ref)
        out = [new if (r.dimension, r.scope_type, r.scope_ref) == key else r for r in rules]
        if not keep_existing:
            out.append(new)
        ledger_rules.validate_rules([r.to_json() for r in out])
        rs = ledger_rules.rule_set(ctx, company_id, lock=True)
        ledger_rules.replace_rules(uow, company_id, out, rs.etag)

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        self._replace(uow, company_id, rec, keep_existing=False)

    def update(self, uow: UnitOfWork, obj: DimensionRule, rec: Record, changed: set[str]) -> Any:
        if "priority" not in rec.present:
            rec.values["priority"] = obj.priority
        self._replace(uow, obj.company_id, rec, keep_existing=True)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return DimensionRule.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("position")


TEMPLATES = (DimensionsTemplate, DimensionRulesTemplate)
