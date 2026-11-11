"""Treasury template (owner P13a): treasury concepts (F-TRE-RC import of concepts).

``treasury.services.masters`` checks ``treasury.concept.create|update`` itself; the engine checks them too.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from treasury.models import TreasuryConcept
from treasury.services import masters

UPDATABLE = ("code", "name", "direction", "account_role", "requires_dimension")


class TreasuryConceptsTemplate(Template):
    entity = "treasury-concepts"
    label = "Conceptos de tesorería"
    module = "treasury"
    owner = "P13a"
    feature = "F-TRE-RC"
    natural_key = ("code",)
    read_permission = "treasury.concept.read"
    create_permission = "treasury.concept.create"
    update_permission = "treasury.concept.update"
    columns = (
        Column("code", required=True, max_length=40, help="Código del concepto", example="PRESTAMO"),
        Column("name", required=True, max_length=200, help="Nombre", example="Préstamo recibido"),
        Column("direction", required=True, allowed=("IN", "OUT", "BOTH"), help="IN (recibo), OUT (egreso) o BOTH", example="IN"),
        Column("account_role", required=True, max_length=120, help="Rol de determinación contable o 'account:<código PUC>'", example="account:210505"),
        Column("requires_dimension", type="boolean", help="Exige dimensión en la línea", example="false"),
        Column("requires_party", type="boolean", help="Exige tercero (solo al crear)", example="true"),
    )
    examples = ({"code": "PRESTAMO", "name": "Préstamo recibido", "direction": "IN", "account_role": "account:210505", "requires_dimension": "false", "requires_party": "true"},)

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return TreasuryConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"]).first()

    def current(self, obj: TreasuryConcept) -> dict[str, Any]:
        return {
            "code": obj.code,
            "name": obj.name,
            "direction": obj.direction,
            "account_role": obj.account_role,
            "requires_dimension": obj.requires_dimension,
            "requires_party": obj.requires_party,
        }

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        data = {k: v for k, v in rec.values.items() if k in rec.present and v is not None}
        return masters.create_concept(uow, company_id, data, extended=True)

    def update(self, uow: UnitOfWork, obj: TreasuryConcept, rec: Record, changed: set[str]) -> Any:
        fixed = changed - set(UPDATABLE)
        if fixed:
            raise RowError("not_updatable", f"{', '.join(sorted(fixed))} solo se define al crear el concepto", sorted(fixed)[0])
        return masters.update_concept(uow, obj, {k: rec.values.get(k) for k in changed})

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return TreasuryConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code")


TEMPLATES = (TreasuryConceptsTemplate,)
