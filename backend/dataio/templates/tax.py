"""Tax template (owner P06): exógena format packs (F-ACC-EXO import of format packs).

A pack is one JSON document per row (``pack`` column, schema-validated by ``tax.exogena.loader``); packs are
immutable (``store_pack`` has no update), so a changed pack must get a new ``version``. Packs imported here are
bound to the job's company. Real DIAN packs are an EXTERNAL input (D-019, B-14): never invented here.
Concept mappings (``exogena-mappings``) have no owner service yet — CCR P18-2.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q, QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template, same
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from tax.exogena import service as exogena
from tax.exogena.models import ExogenaFormatPack


class ExogenaPacksTemplate(Template):
    entity = "exogena-format-packs"
    label = "Paquetes de formatos de exógena (datos externos; SINTÉTICO en dev)"
    module = "tax"
    owner = "P06"
    feature = "F-ACC-EXO"
    natural_key = ("pack_code", "year", "version")
    read_permission = "tax.exogena_format_pack.read"
    create_permission = "tax.exogena_format_pack.create"
    update_permission = "tax.exogena_format_pack.create"
    updatable = False
    columns = (
        Column("pack_code", required=True, max_length=40, help="Código del paquete", example="SYN-EXO-1001"),
        Column("year", type="integer", required=True, help="Año gravable", example="2026"),
        Column(
            "version",
            required=True,
            max_length=40,
            help="Versión del paquete (un cambio exige versión nueva)",
            example="1",
        ),
        Column(
            "pack", type="json", required=True, help="Documento JSON completo del paquete (validado contra el esquema)"
        ),
    )
    notes = (
        "Los paquetes oficiales de la DIAN son un insumo externo (B-14); "
        "en dev solo el paquete SINTÉTICO 'no válido ante la DIAN'."
    )

    def _qs(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[ExogenaFormatPack]:
        return ExogenaFormatPack.objects.filter(tenant_id=ctx.tenant_id).filter(
            Q(company_id=company_id) | Q(company_id__isnull=True)
        )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        return (
            self._qs(ctx, company_id)
            .filter(code=v["pack_code"], year=v["year"], pack_version=str(v["version"]))
            .first()
        )

    def current(self, obj: ExogenaFormatPack) -> dict[str, Any]:
        return {"pack_code": obj.code, "year": obj.year, "version": obj.pack_version, "pack": obj.pack}

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        v = rec.values
        pack = v["pack"]
        if not isinstance(pack, dict):
            raise RowError("type", "pack debe ser un objeto JSON", "pack")
        if (
            pack.get("pack_code") != v["pack_code"]
            or str(pack.get("year")) != str(v["year"])
            or str(pack.get("version")) != str(v["version"])
        ):
            raise RowError(
                "pack_mismatch", "pack_code/year/version deben coincidir con el contenido del paquete", "pack"
            )
        taken = ExogenaFormatPack.objects.filter(
            tenant_id=uow.ctx.tenant_id, code=v["pack_code"], year=v["year"], pack_version=str(v["version"])
        ).exists()
        if taken:
            # Bound to another company of the tenant: never re-used or revealed across companies.
            raise RowError("duplicate", "el paquete ya existe en el inquilino; use una versión nueva")
        row = exogena.store_pack(uow, _plain(pack), company_id=company_id)
        if not same(row.pack, _plain(pack)) and row.company_id != company_id:
            raise RowError("duplicate", "el paquete ya existe", "pack")
        return row

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return self._qs(ctx, company_id).order_by("code", "year", "pack_version")


def _plain(value: Any) -> Any:
    """JSON with Decimals turned back into ints where integral (the loader expects JSON numbers)."""
    from decimal import Decimal

    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    return value


TEMPLATES = (ExogenaPacksTemplate,)
