"""Server-side line resolution of a POS cart: scan → item/presentation, price (D-024), taxes (P06).

The client never prices, numbers or computes a CUDE: every checkout recomputes here from current data
(ADR-015 reconnect rule). Presentation lines (F-INV-PRES, D-013) sell whole packages: ``base_qty = quantity ×
factor`` and the package price is the base unit price × factor unless the engine resolves otherwise.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from inventory.barcodes import service as barcode_service
from inventory.models import Item, Presentation
from kernel import services
from kernel.errors import ValidationFailed
from kernel.interfaces.tax import TaxComputation, TaxLineInput, TaxService
from kernel.money import minor_units, q_qty, round_half_up
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from pos.models import PosConfig
from sales.errors import PricingOverrideRequiresApproval
from sales.models import Party
from sales.pricing import engine
from sales.services.common import decimal_in, qty_in, text_in, unknown_keys, uuid_in, uuid_req

LINE_FIELDS = (
    "line_no",
    "item_id",
    "presentation_id",
    "barcode",
    "quantity",
    "discount_pct",
    "unit_price",
    "description",
    "tax_code",
    "account_code",
    "dimensions",
    "amount",
)
DOC_TYPE = "pos.ticket"


@dataclass
class PricedLine:
    line_no: int
    item: Item
    presentation: Presentation | None
    barcode: str
    description: str
    quantity: Decimal
    factor: Decimal
    base_qty: Decimal
    unit_price: Decimal
    discount_pct: Decimal
    amount: Decimal
    tax_code: str
    price_source: str
    client_unit_price: Decimal | None
    tax_amount: Decimal = Decimal(0)


@dataclass
class PricedCart:
    lines: list[PricedLine]
    taxes: TaxComputation
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    currency: str = "COP"
    diff: list[dict[str, Any]] = field(default_factory=list)


def resolve_scan(ctx: TenantContext, company_id: uuid.UUID, raw: Any) -> tuple[Item, Presentation | None, str]:
    """Scanner (keyboard wedge) value → (item, presentation, value). Presentation barcodes win (T-INV-BARCODE-05)."""
    value = barcode_service.normalize(raw)
    variants = barcode_service._variants(value)
    from inventory.barcodes.models import Barcode

    owner: tuple[str, uuid.UUID] | None = None
    bc = Barcode.objects.filter(company_id=company_id, value__in=variants, status="ACTIVE").first()
    if bc is not None:
        owner = (bc.owner_type, bc.owner_id)
    else:
        p = Presentation.objects.filter(company_id=company_id, barcode__in=variants).first()
        if p is not None:
            owner = ("presentation", p.id)
        else:
            item = Item.objects.filter(company_id=company_id, gtin__in=variants).first()
            if item is not None:
                owner = ("item", item.id)
    if owner is None or owner[0] not in ("item", "presentation"):
        raise barcode_service.BarcodeUnknown(f"barcode {value} is not a product of this company")
    if owner[0] == "presentation":
        pres = Presentation.objects.select_related("item").filter(company_id=company_id, id=owner[1]).first()
        if pres is None:  # pragma: no cover - FK integrity
            raise barcode_service.BarcodeUnknown(f"barcode {value} is not a product of this company")
        return pres.item, pres, value
    item = Item.objects.filter(company_id=company_id, id=owner[1]).first()
    if item is None:  # pragma: no cover - FK integrity
        raise barcode_service.BarcodeUnknown(f"barcode {value} is not a product of this company")
    return item, None, value


def _item_and_presentation(
    ctx: TenantContext, company_id: uuid.UUID, row: Mapping[str, Any], i: int
) -> tuple[Item, Presentation | None, str]:
    if row.get("barcode"):
        scanned, scanned_pres, value = resolve_scan(ctx, company_id, row["barcode"])
        if row.get("item_id") and uuid_in(row["item_id"], f"lines[{i}].item_id") != scanned.id:
            raise ValidationFailed(f"lines[{i}]: barcode and item_id disagree", field="lines")
        return scanned, scanned_pres, value
    item_id = uuid_req(row.get("item_id"), f"lines[{i}].item_id")
    item = Item.objects.filter(company_id=company_id, id=item_id).first()
    if item is None:
        raise ValidationFailed(f"lines[{i}].item_id is not an item of the company", field="lines")
    pres: Presentation | None = None
    pres_id = uuid_in(row.get("presentation_id"), f"lines[{i}].presentation_id", required=False)
    if pres_id is not None:
        pres = Presentation.objects.filter(company_id=company_id, id=pres_id, item_id=item.id).first()
        if pres is None:
            raise ValidationFailed(f"lines[{i}].presentation_id is not a presentation of the item", field="lines")
    return item, pres, ""


def _taxes(company_id: uuid.UUID, day: dt.date, party_id: uuid.UUID | None, lines: list[PricedLine]) -> TaxComputation:
    inputs = [TaxLineInput(str(x.line_no), x.amount, (x.tax_code,)) for x in lines if x.tax_code]
    if not inputs:
        return TaxComputation(())
    comp = services.get(TaxService).compute(company_id, DOC_TYPE, day, party_id, inputs)
    places = minor_units("COP")
    out = tuple(
        type(t)(t.line_ref, t.tax_code, t.base_amount, t.rate, round_half_up(t.amount, places), t.withholding)
        for t in comp.lines
    )
    return TaxComputation(out, comp.parameter_snapshots, comp.warnings)


def price_party(cfg: PosConfig, party: Party | None) -> Party | None:
    """The customer's own pricing, else a transient party carrying the POS price list (D-024 list level)."""
    if party is not None:
        return party
    if cfg.price_list_id is None:
        return None
    return Party(tenant_id=cfg.tenant_id, company_id=cfg.company_id, price_list_id=cfg.price_list_id)


def price_cart(
    uow: UnitOfWork,
    cfg: PosConfig,
    raw_lines: Any,
    day: dt.date,
    party: Party | None,
) -> PricedCart:
    ctx, company = uow.ctx, cfg.company_id
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationFailed("lines must be a non-empty list", field="lines")
    if len(raw_lines) > 500:
        raise ValidationFailed("a ticket has at most 500 lines", field="lines")
    places = minor_units("COP")
    out: list[PricedLine] = []
    pricing_party = price_party(cfg, party)
    for i, row in enumerate(raw_lines):
        if not isinstance(row, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown_keys(row, LINE_FIELDS, f" in lines[{i}]")
        item, pres, scanned = _item_and_presentation(ctx, company, row, i)
        if str(item.status) != "ACTIVE":
            raise ValidationFailed(f"lines[{i}]: the item is not active", field="lines")
        if pres is not None and (not pres.sellable or str(pres.status) != "ACTIVE"):
            raise ValidationFailed(f"lines[{i}]: the presentation is not sellable", field="lines")
        qty, _uom = qty_in(row.get("quantity", "1"), f"lines[{i}].quantity")
        factor = Decimal(pres.factor) if pres is not None else Decimal(1)
        if pres is not None and qty != round_half_up(qty, int(pres.precision)):
            raise ValidationFailed(
                f"lines[{i}]: {pres.code} is sold in whole packages", field="lines", code="uom_precision_violation"
            )
        base_qty = q_qty(qty * factor)
        discount = decimal_in(
            row.get("discount_pct", "0"), f"lines[{i}].discount_pct", minimum=Decimal(0), maximum=Decimal(100)
        )
        res = engine.resolve(
            uow,
            company,
            engine.PriceRequest(
                item_id=item.id,
                quantity=base_qty,
                on=day,
                party=pricing_party,
                channel="pos",
                uom=str(item.base_uom.code),
                discount_pct=discount,
                document_type=DOC_TYPE,
            ),
        )
        if res.override_status == "PENDING_APPROVAL":
            raise PricingOverrideRequiresApproval(
                f"lines[{i}]: the discount exceeds the cashier's limit (D-024); a supervisor must apply it",
                field="lines",
            )
        unit_price = round_half_up(res.unit_price * factor, places)
        amount = round_half_up(unit_price * qty * (Decimal(100) - discount) / Decimal(100), places)
        client_price_raw = row.get("unit_price")
        client_price: Decimal | None = None
        if client_price_raw not in (None, ""):
            if isinstance(client_price_raw, Mapping):
                client_price_raw = client_price_raw.get("amount")
            client_price = decimal_in(client_price_raw, f"lines[{i}].unit_price", minimum=Decimal(0))
        tax_code = str(row.get("tax_code") or getattr(item, "sales_tax_code", "") or "")
        label = pres.name if pres is not None else ""
        out.append(
            PricedLine(
                line_no=len(out) + 1,
                item=item,
                presentation=pres,
                barcode=scanned,
                description=text_in(row.get("description"), "description", 255)
                or (f"{item.name} — {label}" if label else str(item.name))[:255],
                quantity=qty,
                factor=factor,
                base_qty=base_qty,
                unit_price=unit_price,
                discount_pct=discount,
                amount=amount,
                tax_code=tax_code,
                price_source=res.source,
                client_unit_price=client_price,
            )
        )
    taxes = _taxes(company, day, party.id if party is not None else None, out)
    by_ref: dict[str, Decimal] = {}
    for t in taxes.lines:
        if not t.withholding:
            by_ref[t.line_ref] = by_ref.get(t.line_ref, Decimal(0)) + t.amount
    for x in out:
        x.tax_amount = by_ref.get(str(x.line_no), Decimal(0))
    subtotal = sum((x.amount for x in out), Decimal(0))
    tax_total = sum(by_ref.values(), Decimal(0))
    cart = PricedCart(out, taxes, subtotal, tax_total, subtotal + tax_total)
    cart.diff = [
        {
            "line_no": x.line_no,
            "item_id": str(x.item.id),
            "draft_unit_price": f"{x.client_unit_price:f}",
            "server_unit_price": f"{x.unit_price:f}",
        }
        for x in out
        if x.client_unit_price is not None and x.client_unit_price != x.unit_price
    ]
    return cart


def cart_json(cart: PricedCart) -> dict[str, Any]:
    return {
        "currency": cart.currency,
        "subtotal": f"{cart.subtotal:f}",
        "tax_total": f"{cart.tax_total:f}",
        "total": f"{cart.total:f}",
        "lines": [
            {
                "line_no": x.line_no,
                "item_id": str(x.item.id),
                "presentation_id": str(x.presentation.id) if x.presentation else None,
                "description": x.description,
                "quantity": f"{x.quantity:f}",
                "factor": f"{x.factor:f}",
                "base_qty": f"{x.base_qty:f}",
                "unit_price": f"{x.unit_price:f}",
                "discount_pct": f"{x.discount_pct:f}",
                "amount": f"{x.amount:f}",
                "tax_code": x.tax_code,
                "tax_amount": f"{x.tax_amount:f}",
            }
            for x in cart.lines
        ],
        "diff": cart.diff,
        "warnings": list(cart.taxes.warnings),
    }
