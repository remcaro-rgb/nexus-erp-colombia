"""Priced document lines shared by quotes, orders and invoices (D-024 resolver, snapshots, override approvals)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.workflow import WorkflowService
from kernel.money import minor_units, round_half_up
from kernel.uow import UnitOfWork
from sales.errors import PricingOverrideRequiresApproval
from sales.models import ItemSalesPrice, OverrideStatus, SalesDocument
from sales.pricing import engine
from sales.services.common import decimal_in, dims_in, money_in, qty_in, text_in, unknown_keys, uuid_in

LINE_FIELDS = (
    "line_no",
    "item_id",
    "description",
    "quantity",
    "uom",
    "unit_price",
    "discount_pct",
    "override_reason",
    "tax_code",
    "warehouse_id",
    "dimensions",
)
OVERRIDE_POLICY = "sales.price_override"


def line_amount(qty: Decimal, unit_price: Decimal, discount_pct: Decimal, currency: str) -> Decimal:
    """Net line amount rounded ROUND_HALF_UP to the currency minor unit (D-001)."""
    return round_half_up(qty * unit_price * (Decimal(100) - discount_pct) / Decimal(100), minor_units(currency))


def default_tax_code(company_id: uuid.UUID, tenant_id: uuid.UUID, item_id: uuid.UUID | None) -> str:
    if item_id is None:
        return ""
    row = (
        ItemSalesPrice.objects.filter(tenant_id=tenant_id, company_id=company_id, item_id=item_id)
        .exclude(tax_code="")
        .first()
    )
    if row is not None:
        return row.tax_code
    from django.apps import apps

    if apps.is_installed("inventory"):
        item = apps.get_model("inventory", "Item").objects.filter(company_id=company_id, id=item_id).first()
        if item is not None and item.sales_tax_code:
            return str(item.sales_tax_code)
    return ""


def doc_type(doc: SalesDocument) -> str:
    return {"Quote": "sales.quote", "SalesOrder": "sales.order", "Invoice": "sales.invoice"}[type(doc).__name__]


def price_line(
    uow: UnitOfWork,
    doc: SalesDocument,
    *,
    item_id: uuid.UUID,
    qty: Decimal,
    uom: str,
    override: Decimal | None,
    discount: Decimal,
    reason: str,
) -> engine.PriceResult:
    return engine.resolve(
        uow,
        doc.company_id,
        engine.PriceRequest(
            item_id=item_id,
            quantity=qty,
            on=doc.document_date,
            currency=doc.currency,
            party=doc.party,
            channel=doc.channel,
            uom=uom,
            fx_rate=doc.fx_rate,
            override_price=override,
            override_reason=reason,
            discount_pct=discount,
            document_type=doc_type(doc),
            document_id=doc.id,
        ),
    )


def write_lines(
    uow: UnitOfWork,
    doc: SalesDocument,
    line_model: Any,
    raw: Any,
    *,
    extra: Mapping[int, Mapping[str, Any]] | None = None,
) -> None:
    """Replace the lines of a DRAFT document; every item line is priced by the resolver (D-024)."""
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a document needs at least one line", field="lines")
    line_model.objects.filter(document=doc).delete()
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown_keys(line, LINE_FIELDS, f" in lines[{i}]")
        line_no = line.get("line_no", i + 1)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        qty, uom = qty_in(line.get("quantity"), f"lines[{i}].quantity")
        item_id = uuid_in(line.get("item_id"), f"lines[{i}].item_id", required=False)
        override = (
            money_in(line["unit_price"], f"lines[{i}].unit_price", doc.currency)
            if line.get("unit_price") not in (None, "")
            else None
        )
        discount = decimal_in(
            line.get("discount_pct", "0"), f"lines[{i}].discount_pct", minimum=Decimal(0), maximum=Decimal(100)
        )
        reason = text_in(line.get("override_reason"), "override_reason", 500)
        uom = (uom or str(line.get("uom") or "UND"))[:20]
        if item_id is not None:
            priced = price_line(
                uow, doc, item_id=item_id, qty=qty, uom=uom, override=override, discount=discount, reason=reason
            )
            unit_price, source, snapshot, status = (
                priced.unit_price,
                priced.source,
                priced.snapshot,
                priced.override_status,
            )
        else:
            if override is None:
                raise ValidationFailed(f"lines[{i}] without item_id needs unit_price", field="lines")
            unit_price, source, status = override, engine.OVERRIDE, OverrideStatus.NONE
            snapshot = {"source": engine.OVERRIDE, "unit_price": f"{override:f}", "free_text": True}
        tax_code = text_in(line.get("tax_code"), "tax_code", 40) or default_tax_code(
            doc.company_id, doc.tenant_id, item_id
        )
        line_model.objects.create(
            tenant_id=doc.tenant_id,
            company_id=doc.company_id,
            document=doc,
            line_no=line_no,
            item_id=item_id,
            description=text_in(line.get("description"), "description", 500),
            quantity=qty,
            uom=uom,
            unit_price=unit_price,
            discount_pct=discount,
            amount=line_amount(qty, unit_price, discount, doc.currency),
            tax_code=tax_code,
            warehouse_id=uuid_in(line.get("warehouse_id"), f"lines[{i}].warehouse_id", required=False),
            dimensions=dims_in(line.get("dimensions")),
            price_source=source,
            price_snapshot=snapshot,
            override_status=status,
            **dict((extra or {}).get(line_no, {})),
        )
    refresh_totals(doc)
    route_overrides(uow, doc)


def copy_lines(src: SalesDocument, dst: SalesDocument, line_model: Any, qty_of: Any = None, extra: Any = None) -> int:
    """Copy lines with their price snapshots (agreed prices are carried, never re-resolved)."""
    n = 0
    for line in src.lines.order_by("line_no"):  # type: ignore[attr-defined]
        qty = qty_of(line) if qty_of else line.quantity
        if qty <= 0:
            continue
        n += 1
        line_model.objects.create(
            tenant_id=dst.tenant_id,
            company_id=dst.company_id,
            document=dst,
            line_no=line.line_no,
            item_id=line.item_id,
            description=line.description,
            quantity=qty,
            uom=line.uom,
            unit_price=line.unit_price,
            discount_pct=line.discount_pct,
            amount=line_amount(qty, line.unit_price, line.discount_pct, dst.currency),
            tax_code=line.tax_code,
            warehouse_id=line.warehouse_id,
            dimensions=line.dimensions,
            price_source=line.price_source,
            price_snapshot={
                **line.price_snapshot,
                "carried_from": {"type": doc_type(src), "id": str(src.id), "number": src.number},
            },
            override_status=line.override_status,
            **(extra(line) if extra else {}),
        )
    refresh_totals(dst)
    return n


def refresh_totals(doc: SalesDocument) -> None:
    doc.subtotal = sum((x.amount for x in doc.lines.all()), Decimal(0))  # type: ignore[attr-defined]
    doc.total = doc.subtotal + doc.tax_total
    doc.save(update_fields=["subtotal", "total"])


def route_overrides(uow: UnitOfWork, doc: SalesDocument) -> None:
    """Overrides beyond the discount limit start a ``sales.price_override`` approval (D-024, T-SAL-PRICING-03)."""
    pending = doc.lines.filter(override_status=OverrideStatus.PENDING_APPROVAL).exists()  # type: ignore[attr-defined]
    if not pending:
        if doc.approval_request_id is not None:
            doc.approval_request_id = None
            doc.save(update_fields=["approval_request_id"])
        return
    req = services.get(WorkflowService).request_approval(
        uow,
        doc.company_id,
        DocumentRef(doc_type(doc), doc.id, doc.number),
        OVERRIDE_POLICY,
        "precio manual sobre el límite de descuento",
    )
    doc.approval_request_id = req.id
    doc.save(update_fields=["approval_request_id"])


def require_overrides_approved(uow: UnitOfWork, doc: SalesDocument) -> None:
    """Refuse to confirm/send/post while an override approval is pending or was rejected."""
    lines = doc.lines.filter(override_status=OverrideStatus.PENDING_APPROVAL)  # type: ignore[attr-defined]
    if not lines.exists():
        return
    state = services.get(WorkflowService).state(doc.approval_request_id) if doc.approval_request_id else None
    if state is not None and state.state == "APPROVED":
        lines.update(override_status=OverrideStatus.APPROVED)
        return
    if state is not None and state.state == "REJECTED":
        lines.update(override_status=OverrideStatus.REJECTED)
        raise PricingOverrideRequiresApproval(
            "the price override was rejected; reprice the lines", approval_request_id=str(doc.approval_request_id)
        )
    raise PricingOverrideRequiresApproval(
        "a price override above the discount limit is waiting for approval",
        approval_request_id=str(doc.approval_request_id) if doc.approval_request_id else None,
    )


def reprice(uow: UnitOfWork, doc: SalesDocument, *, apply: bool) -> list[dict[str, Any]]:
    """Re-resolve the prices of a DRAFT document (T-SAL-PRICING-02): returns the diff; ``apply`` stores it.

    Lines with a manual override keep it (the resolved reference price and the discount are re-evaluated).
    """
    diff: list[dict[str, Any]] = []
    for line in doc.lines.order_by("line_no"):  # type: ignore[attr-defined]
        if line.item_id is None:
            continue
        override = line.unit_price if line.price_source == engine.OVERRIDE else None
        reason = ((line.price_snapshot or {}).get("override") or {}).get("reason", "")
        res = price_line(
            uow,
            doc,
            item_id=line.item_id,
            qty=line.quantity,
            uom=line.uom,
            override=override,
            discount=line.discount_pct,
            reason=reason,
        )
        new_amount = line_amount(line.quantity, res.unit_price, line.discount_pct, doc.currency)
        changed = res.unit_price != line.unit_price or res.source != line.price_source
        diff.append(
            {
                "line_no": line.line_no,
                "item_id": str(line.item_id),
                "old": {
                    "unit_price": f"{line.unit_price:f}",
                    "source": line.price_source,
                    "amount": f"{line.amount:f}",
                    "snapshot_hash": (line.price_snapshot or {}).get("hash"),
                },
                "new": {
                    "unit_price": f"{res.unit_price:f}",
                    "source": res.source,
                    "amount": f"{new_amount:f}",
                    "snapshot_hash": res.snapshot.get("hash"),
                },
                "delta_amount": f"{new_amount - line.amount:f}",
                "changed": changed,
            }
        )
        if apply and changed:
            line.unit_price, line.price_source, line.price_snapshot = res.unit_price, res.source, res.snapshot
            line.override_status, line.amount = res.override_status, new_amount
            line.save(update_fields=["unit_price", "price_source", "price_snapshot", "override_status", "amount"])
    if apply:
        refresh_totals(doc)
        route_overrides(uow, doc)
    return diff
