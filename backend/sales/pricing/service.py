"""Pricing masters (price lists, item base prices, price-formula bindings) and ``pricing:quote`` (F-SAL-PRICING)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock
from kernel.errors import Conflict, ValidationFailed
from kernel.uow import UnitOfWork
from sales.models import ItemSalesPrice, Party, PriceFormula, PriceList, PriceListLine
from sales.pricing import engine
from sales.services.common import (
    audit,
    currency_in,
    date_in,
    decimal_in,
    lock,
    money_in,
    qty_in,
    require,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)

SCOPE_KEYS = ("item_id", "category", "service", "customer_group", "customer_id", "currency", "channel")
LIST_FIELDS = ("code", "name", "currency", "customer_group", "valid_from", "valid_to", "prices", "status")
FORMULA_FIELDS = ("code", "name", "formula_id", "formula_code", "scope", "priority", "valid_from", "valid_to", "status")
ITEM_PRICE_FIELDS = ("item_id", "currency", "base_price", "valid_from", "valid_to", "category", "tax_code")


def _validity(
    data: Mapping[str, Any], current: tuple[dt.date, dt.date | None] | None = None
) -> tuple[dt.date, dt.date | None]:
    start = date_in(data.get("valid_from"), "valid_from", current[0] if current else clock.business_date())
    end_raw = data.get("valid_to", current[1] if current else None)
    end = date_in(end_raw, "valid_to") if end_raw not in (None, "") else None
    if end is not None and end < start:
        raise ValidationFailed("valid_to must not be before valid_from", field="valid_to")
    return start, end


# --------------------------------------------------------------------------- price lists


def _set_prices(plist: PriceList, raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, list):
        raise ValidationFailed("prices must be a list of {item_id, min_qty, unit_price}", field="prices")
    PriceListLine.objects.filter(price_list=plist).delete()
    seen: set[tuple[uuid.UUID, Decimal]] = set()
    for i, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValidationFailed(f"prices[{i}] must be an object", field="prices")
        unknown_keys(row, ("item_id", "min_qty", "unit_price"), f" in prices[{i}]")
        item = uuid_req(row.get("item_id"), f"prices[{i}].item_id")
        min_qty = decimal_in(row.get("min_qty", "0"), f"prices[{i}].min_qty", minimum=Decimal(0))
        if (item, min_qty) in seen:
            raise ValidationFailed(f"prices[{i}] repeats item and quantity break", field="prices")
        seen.add((item, min_qty))
        PriceListLine.objects.create(
            tenant_id=plist.tenant_id,
            company_id=plist.company_id,
            price_list=plist,
            item_id=item,
            min_qty=min_qty,
            unit_price=money_in(row.get("unit_price"), f"prices[{i}].unit_price", plist.currency),
        )


def create_price_list(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PriceList:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.price_list.create", {"company_id": company_id})
    unknown_keys(data, LIST_FIELDS)
    code = text_in(data.get("code"), "code", 40).strip()
    if not code:
        raise ValidationFailed("code is required", field="code")
    if PriceList.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).exists():
        raise Conflict(f"price list {code} already exists")
    start, end = _validity(data)
    plist = PriceList.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=text_in(data.get("name") or code, "name", 200),
        currency=currency_in(data.get("currency")),
        customer_group=text_in(data.get("customer_group"), "customer_group", 40),
        valid_from=start,
        valid_to=end,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _set_prices(plist, data.get("prices") or [])
    audit(uow, "sales.price_list.create", "sales.price_list", plist.id, company_id, {"code": code})
    return plist


def update_price_list(uow: UnitOfWork, plist: PriceList, data: Mapping[str, Any]) -> PriceList:
    ctx = uow.ctx
    require(ctx, "sales.price_list.update", plist)
    unknown_keys(data, LIST_FIELDS)
    if "code" in data or "currency" in data:
        raise ValidationFailed("code and currency cannot be changed", field="code")
    plist = lock(PriceList.objects.filter(tenant_id=ctx.tenant_id), plist.id)
    plist.valid_from, plist.valid_to = _validity(data, (plist.valid_from, plist.valid_to))
    if "name" in data:
        plist.name = text_in(data["name"], "name", 200)
    if "customer_group" in data:
        plist.customer_group = text_in(data["customer_group"], "customer_group", 40)
    if "status" in data:
        if data["status"] not in PriceList.Status.values:
            raise ValidationFailed("status must be ACTIVE or INACTIVE", field="status")
        plist.status = data["status"]
    plist.version += 1
    plist.updated_by = ctx.principal_id
    plist.save()
    _set_prices(plist, data.get("prices"))
    audit(uow, "sales.price_list.update", "sales.price_list", plist.id, plist.company_id, {"fields": sorted(data)})
    return plist


# --------------------------------------------------------------------------- item base prices


def set_item_price(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> ItemSalesPrice:
    """Item base price (D-024 lowest level); one row per item+currency+valid_from (upsert)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.price_list.update", {"company_id": company_id})
    unknown_keys(data, ITEM_PRICE_FIELDS)
    currency = currency_in(data.get("currency"))
    start, end = _validity(data)
    row, _ = ItemSalesPrice.objects.update_or_create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        item_id=uuid_req(data.get("item_id"), "item_id"),
        currency=currency,
        valid_from=start,
        defaults={
            "base_price": money_in(data.get("base_price"), "base_price", currency),
            "valid_to": end,
            "category": text_in(data.get("category"), "category", 60),
            "tax_code": text_in(data.get("tax_code"), "tax_code", 40),
            "updated_by": ctx.principal_id,
        },
    )
    audit(uow, "sales.item_price.set", "sales.item_price", row.id, company_id, {"currency": currency})
    return row


# --------------------------------------------------------------------------- formula bindings


def _scope(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationFailed("scope must be an object", field="scope")
    unknown_keys(raw, SCOPE_KEYS, " in scope")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value in (None, "", []):
            continue
        if key in ("item_id", "customer_id", "service"):
            vals = value if isinstance(value, list) else [value]
            ids = [str(uuid_req(v, f"scope.{key}")) for v in vals]
            out[key] = ids if isinstance(value, list) else ids[0]
        elif key == "currency":
            out[key] = currency_in(value)
        else:
            out[key] = (
                [text_in(v, f"scope.{key}", 60) for v in value]
                if isinstance(value, list)
                else text_in(value, f"scope.{key}", 60)
            )
    return out


def _formula_row(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> tuple[uuid.UUID, str]:
    """The referenced workflow formula must exist in the company and be of domain ``pricing``."""
    from workflow.models import Formula

    fid = uuid_in(data.get("formula_id"), "formula_id", required=False)
    code = text_in(data.get("formula_code"), "formula_code", 80).strip()
    qs = Formula.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, domain="pricing")
    row = qs.filter(id=fid).first() if fid else qs.filter(code=code).order_by("-formula_version").first()
    if row is None:
        raise ValidationFailed("formula_id is not a pricing formula of the company", field="formula_id")
    return row.id, row.code


def create_price_formula(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PriceFormula:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.price_formula.create", {"company_id": company_id})
    unknown_keys(data, FORMULA_FIELDS)
    fid, fcode = _formula_row(uow, company_id, data)
    code = text_in(data.get("code") or fcode, "code", 60).strip()
    if PriceFormula.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).exists():
        raise Conflict(f"price formula binding {code} already exists")
    priority = data.get("priority", 100)
    if not isinstance(priority, int) or isinstance(priority, bool) or not -10000 <= priority <= 10000:
        raise ValidationFailed("priority must be an integer", field="priority")
    start, end = _validity(data)
    row = PriceFormula.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=text_in(data.get("name"), "name", 200),
        formula_id=fid,
        formula_code=fcode,
        scope=_scope(data.get("scope")),
        priority=priority,
        valid_from=start,
        valid_to=end,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(
        uow, "sales.price_formula.create", "sales.price_formula", row.id, company_id, {"code": code, "formula": fcode}
    )
    return row


def update_price_formula(uow: UnitOfWork, row: PriceFormula, data: Mapping[str, Any]) -> PriceFormula:
    ctx = uow.ctx
    require(ctx, "sales.price_formula.update", row)
    unknown_keys(data, FORMULA_FIELDS)
    row = lock(PriceFormula.objects.filter(tenant_id=ctx.tenant_id), row.id)
    if "code" in data:
        raise ValidationFailed("code cannot be changed", field="code")
    if "formula_id" in data or "formula_code" in data:
        row.formula_id, row.formula_code = _formula_row(uow, row.company_id, data)
    if "scope" in data:
        row.scope = _scope(data["scope"])
    if "priority" in data:
        if not isinstance(data["priority"], int) or isinstance(data["priority"], bool):
            raise ValidationFailed("priority must be an integer", field="priority")
        row.priority = data["priority"]
    if "name" in data:
        row.name = text_in(data["name"], "name", 200)
    if "status" in data:
        if data["status"] not in PriceFormula.Status.values:
            raise ValidationFailed("status must be ACTIVE or INACTIVE", field="status")
        row.status = data["status"]
    row.valid_from, row.valid_to = _validity(data, (row.valid_from, row.valid_to))
    row.version += 1
    row.updated_by = ctx.principal_id
    row.save()
    audit(uow, "sales.price_formula.update", "sales.price_formula", row.id, row.company_id, {"fields": sorted(data)})
    return row


# --------------------------------------------------------------------------- pricing:quote


QUOTE_FIELDS = ("party", "customer", "customer_id", "items", "date", "currency", "fx_rate", "channel")


def price_quote(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> dict[str, Any]:
    """``POST /api/v1/sales/pricing:quote`` → price + explanation per item (D-024). Nothing persists but the
    P07 evaluation snapshots, which are written in this UoW and rolled back by the caller (read-only call)."""
    from sales.party.service import get_customer

    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.pricing.read", {"company_id": company_id})
    unknown_keys(data, QUOTE_FIELDS)
    raw_party = data.get("party", data.get("customer", data.get("customer_id")))
    party: Party | None = get_customer(uow, company_id, raw_party) if raw_party not in (None, "") else None
    items = data.get("items")
    if not isinstance(items, list) or not items or len(items) > 200:
        raise ValidationFailed("items must be a list of 1–200 {item_id, quantity}", field="items")
    day = date_in(data.get("date"), "date", clock.business_date())
    currency = currency_in(data.get("currency"))
    fx = decimal_in(data["fx_rate"], "fx_rate", positive=True) if data.get("fx_rate") not in (None, "") else None
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, Mapping):
            raise ValidationFailed(f"items[{i}] must be an object", field="items")
        unknown_keys(it, ("item_id", "quantity", "uom", "override_price", "discount_pct"), f" in items[{i}]")
        qty, uom = qty_in(it.get("quantity", "1"), f"items[{i}].quantity")
        req = engine.PriceRequest(
            item_id=uuid_req(it.get("item_id"), f"items[{i}].item_id"),
            quantity=qty,
            on=day,
            currency=currency,
            party=party,
            channel=text_in(data.get("channel") or "internal", "channel", 20),
            uom=uom or str(it.get("uom") or "UND"),
            fx_rate=fx,
            override_price=money_in(it["override_price"], "override_price", currency)
            if it.get("override_price") not in (None, "")
            else None,
            discount_pct=decimal_in(
                it.get("discount_pct", "0"), "discount_pct", minimum=Decimal(0), maximum=Decimal(100)
            ),
            document_type="sales.pricing_quote",
        )
        res = engine.resolve(uow, company_id, req)
        out.append(
            {
                "item_id": str(req.item_id),
                "quantity": f"{qty:f}",
                "unit_price": {"amount": f"{res.unit_price:f}", "currency": currency},
                "source": res.source,
                "override_status": res.override_status,
                "discount_pct": f"{res.discount_pct:f}",
                "explain": engine.visible_snapshot(ctx, res.snapshot),
            }
        )
    return {"date": day.isoformat(), "currency": currency, "items": out}
