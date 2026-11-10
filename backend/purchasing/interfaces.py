"""Frozen purchasing contract for the other P11 parts (P11b requisitions + supplier portal) and other modules.

P11b codes against these functions only (never against the views or private helpers). Each runs inside the
caller's unit of work and authorizes through ``PolicyService`` like the public API does. Version 1.0.0.

* Requisitions (F-INV-REQ, ``backend/purchasing/requisitions/**``): ``rfq_from_requisition`` (``:to-rfq``),
  ``draft_order`` (``:to-po`` and the D-011 automatic drafts) and ``valid_vendor_price`` (D-011: approved award
  ≤ 90 days old).
* Supplier portal (F-PORTAL-SUP, ``backend/purchasing/portal/**``): ``portal_submit_quote`` (channel
  ``supplier_portal``; the supplier id comes from the portal token's party scope, D-033) and
  ``portal_acknowledge_order``. Portal reads must filter by that supplier id and never expose other vendors'
  quotes, internal prices or notes (T-PORTAL-SUP-04).
* Events: ``purchasing.rfq.awarded.v1``, ``purchasing.receipt.validated.v1``, ``purchasing.vendor_invoice.posted.v1``
  (schemas in ``contracts/events/purchasing/``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kernel import clock
from kernel.errors import NotFound
from kernel.interfaces.common import DocumentRef
from kernel.money import UNIT_COST, round_half_up
from kernel.uow import UnitOfWork
from purchasing.models import Award, AwardLine, PurchaseOrder, Rfq, VendorQuote
from purchasing.services import orders as _orders
from purchasing.services import rfq as _rfq
from purchasing.services.common import require

CONTRACT_VERSION = "1.0.0"
VENDOR_PRICE_MAX_AGE_DAYS = 90  # D-011


@dataclass(frozen=True, slots=True)
class VendorPrice:
    supplier_id: uuid.UUID
    item_id: uuid.UUID
    unit_price: Decimal  # document currency per base UoM, net of discounts
    currency: str
    fx_rate: Decimal
    uom: str
    awarded_on: dt.date
    award_id: uuid.UUID


def rfq_from_requisition(
    uow: UnitOfWork, company_id: uuid.UUID, requisition: DocumentRef, data: Mapping[str, Any]
) -> tuple[Rfq, list[str]]:
    """DRAFT RFQ whose lines keep the requisition line ids (``lines[].source_line_id``) as lineage."""
    return _rfq.create_rfq(uow, company_id, data, source=requisition)


def draft_order(uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, data: Mapping[str, Any]) -> PurchaseOrder:
    """DRAFT purchase order from a requisition; it always enters the normal approval (D-011)."""
    return _orders.create_order(
        uow, company_id, data, source={"type": source.type, "id": str(source.id), "number": source.number}
    )


def valid_vendor_price(
    uow: UnitOfWork, company_id: uuid.UUID, item_id: uuid.UUID, on: dt.date | None = None
) -> VendorPrice | None:
    """Most recent APPROVED award price of ``item_id`` decided at most 90 days before ``on`` (D-011), else None."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.rfq.read", {"company_id": company_id})
    day = on or clock.business_date()
    since = clock.local_midnight_utc(day - dt.timedelta(days=VENDOR_PRICE_MAX_AGE_DAYS))
    line = (
        AwardLine.objects.filter(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            rfq_line__item_id=item_id,
            award__status=Award.Status.APPROVED,
            award__decided_at__gte=since,
        )
        .select_related("award", "quote_line", "quote", "rfq_line")
        .order_by("-award__decided_at", "-id")
        .first()
    )
    if line is None or line.award.decided_at is None:
        return None
    ql = line.quote_line
    net = ql.unit_price * (Decimal(100) - ql.discount_pct) / Decimal(100)
    return VendorPrice(
        supplier_id=line.quote.supplier_id,
        item_id=item_id,
        unit_price=round_half_up(net / ql.factor_to_base, UNIT_COST),
        currency=line.quote.currency,
        fx_rate=line.quote.fx_rate,
        uom=line.rfq_line.uom,
        awarded_on=line.award.decided_at.astimezone(clock.tz()).date(),
        award_id=line.award_id,
    )


def portal_submit_quote(
    uow: UnitOfWork, rfq_id: uuid.UUID, supplier_id: uuid.UUID, data: Mapping[str, Any]
) -> tuple[VendorQuote, list[str]]:
    """Quote submitted by the supplier portal for the supplier of the portal principal (never from the body)."""
    rfq = Rfq.objects.select_for_update().filter(tenant_id=uow.ctx.tenant_id, id=rfq_id).first()
    if rfq is None or str(supplier_id) not in rfq.invited_vendors:
        raise NotFound("not found")
    body = {k: v for k, v in data.items() if k != "party"}
    return _rfq.create_quote(
        uow,
        rfq,
        body,
        channel=VendorQuote.Channel.SUPPLIER_PORTAL,
        supplier_id=supplier_id,
        permission="portal.supplier_quote.create",
    )


def portal_acknowledge_order(uow: UnitOfWork, order_id: uuid.UUID, supplier_id: uuid.UUID) -> PurchaseOrder:
    """SENT → ACKNOWLEDGED for an order of the portal principal's supplier (others answer not_found)."""
    require(uow.ctx, "portal.supplier_purchase_order.update", {"supplier_id": supplier_id})
    order = (
        PurchaseOrder.objects.select_for_update()
        .filter(tenant_id=uow.ctx.tenant_id, id=order_id, supplier_id=supplier_id)
        .first()
    )
    if order is None:
        raise NotFound("not found")
    return _orders.acknowledge_order(uow, order)
