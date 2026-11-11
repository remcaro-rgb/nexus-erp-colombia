"""Catalog snapshot for the POS PWA read cache (``GET /pos/catalog-snapshot``; read-only, no cost fields).

Rows: sellable active items with their base price (D-024 base/list level for the POS price list), tax code, and
their sellable presentations with factor and barcode. ``since`` (ISO date-time) returns only rows changed after it
(delta read-sync); the caller compares the ETag. Prices here are DISPLAY data for the offline catalog: every
checkout recomputes on the server (ADR-015).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from inventory.models import Item, Presentation
from kernel import clock
from kernel.errors import ValidationFailed
from kernel.tenancy import TenantContext
from pos.models import PosConfig
from sales.models import ItemSalesPrice, PriceListLine
from sales.services.common import uuid_in


def _since(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationFailed("since must be an ISO 8601 date-time", code="filter_invalid") from exc
    if value.tzinfo is None:
        raise ValidationFailed("since needs a time-zone offset", code="filter_invalid")
    return value


def snapshot(ctx: TenantContext, company_id: uuid.UUID, config_id: str | None, since: str | None) -> dict[str, Any]:
    ctx.require_company(company_id)
    today = clock.business_date()
    cfg = None
    cid = uuid_in(config_id, "config_id", required=False)
    if cid is not None:
        cfg = PosConfig.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=cid).first()
        if cfg is None:
            raise ValidationFailed("config_id is not a POS configuration of the company", field="config_id")
    items = Item.objects.filter(company_id=company_id, status="ACTIVE").select_related("base_uom").order_by("code")
    changed = _since(since)
    if changed is not None:
        items = items.filter(updated_at__gt=changed)
    base: dict[uuid.UUID, Decimal] = {}
    for row in (
        ItemSalesPrice.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, currency="COP", valid_from__lte=today
        )
        .exclude(valid_to__lt=today)
        .order_by("item_id", "valid_from")
    ):
        base[row.item_id] = Decimal(row.base_price)  # latest valid_from wins (ordered ascending)
    if cfg is not None and cfg.price_list_id is not None:
        for line in PriceListLine.objects.filter(price_list_id=cfg.price_list_id, min_qty__lte=1).order_by(
            "item_id", "min_qty"
        ):
            base[line.item_id] = Decimal(line.unit_price)
    pres: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for p in Presentation.objects.filter(company_id=company_id, sellable=True, status="ACTIVE").order_by("code"):
        pres.setdefault(p.item_id, []).append(
            {
                "id": str(p.id),
                "code": p.code,
                "name": p.name,
                "factor": f"{p.factor:f}",
                "barcode": p.barcode,
                "precision": p.precision,
            }
        )
    rows = [
        {
            "id": str(i.id),
            "code": i.code,
            "name": i.name,
            "category": i.category,
            "uom": i.base_uom.code,
            "gtin": i.gtin,
            "tax_code": i.sales_tax_code,
            "price": f"{base[i.id]:f}" if i.id in base else None,
            "presentations": pres.get(i.id, []),
        }
        for i in items
        if str(i.item_type) != "SERVICE" or i.id in base
    ]
    return {
        "report": "pos.catalog_snapshot",
        "parameters": {"config_id": str(cfg.id) if cfg else None, "since": since},
        "columns": [
            {"key": "code", "label": "Código", "type": "string"},
            {"key": "name", "label": "Producto", "type": "string"},
            {"key": "price", "label": "Precio", "type": "money"},
        ],
        "rows": rows,
        "totals": {"count": len(rows), "generated_at": clock.now().isoformat()},
        "warnings": [{"code": "display_only", "message": "Precios de referencia: el servidor recalcula al finalizar"}],
        "synthetic": False,
    }
