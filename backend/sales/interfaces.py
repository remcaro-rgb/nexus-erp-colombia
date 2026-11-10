"""Frozen in-process contract of the sales core for other tasks (P10b customer portal, P15, P18, POS).

``CONTRACT_VERSION`` follows semver: P10a only adds to this module; a breaking change needs a CCR.

Customer-portal projections (``/api/portal/v1/customer/*``, implemented by P10b over these functions):

* the caller (P10b) resolves the external account → ``party_id`` from the portal token; every function filters
  by that party and answers ``NotFound`` for any other party's document (no existence oracle);
* each function requires its own ``portal.customer_*.read`` permission through ``PolicyService`` (deny by
  default) — it is a dedicated, party-scoped grant, not a bypass of the internal ``sales.*`` permissions;
* the output is an allowlist of the contract schemas (``CustomerQuote``, ``CustomerOrder``,
  ``CustomerInvoice``, ``CustomerCreditNote``, ``CustomerReceipt``): NEVER costs, margins, price snapshots,
  formula ids/text/trace, salesperson or approval data (T-SAL-PRICING-04);
* ``portal_price`` answers the final unit price only (``engine.explain``), evaluated in a rolled-back
  savepoint so nothing persists.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.db import transaction

from kernel import clock
from kernel.errors import NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.kits import KitComponent, KitExplosion
from sales.models import ArOpenItem, Invoice, Party, Quote, Receipt, SalesNote, SalesOrder
from sales.party.service import PartyInfo, PartyService
from sales.pricing import engine
from sales.services.common import require

CONTRACT_VERSION = "1.0.0"

#: Allowlisted keys of every portal projection (contract ``Customer*`` schemas, ``additionalProperties: false``).
PORTAL_FIELDS: tuple[str, ...] = (
    "id",
    "tenant_id",
    "status",
    "number",
    "total",
    "valid_until",
    "quote_status",
    "version",
    "audit",
)
#: Keys that must never reach a portal response (checked by T-SAL-PRICING-04).
PORTAL_FORBIDDEN: frozenset[str] = frozenset(
    {
        "cost",
        "cost_value",
        "cost_total",
        "margin",
        "price_snapshot",
        "snapshot",
        "formula",
        "formula_id",
        "formula_code",
        "formula_hash",
        "expression",
        "trace",
        "inputs",
        "salesperson_id",
        "approval_request_id",
        "credit_limit",
        "price_source",
        "lines",
    }
)

PERM_QUOTE = "portal.customer_quote.read"
PERM_ORDER = "portal.customer_order.read"
PERM_INVOICE = "portal.customer_invoice.read"
PERM_CREDIT_NOTE = "portal.customer_credit_note.read"
PERM_RECEIPT = "portal.customer_receipt.read"


def _money(amount: Decimal | None, currency: str) -> dict[str, str] | None:
    return None if amount is None else {"amount": f"{amount:f}", "currency": currency}


def _audit(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat() if getattr(obj, "created_at", None) else None,
        "updated_at": obj.updated_at.isoformat() if getattr(obj, "updated_at", None) else None,
    }


def _project(obj: Any, *, total: Decimal | None, currency: str, doc_status: str, valid_until: Any = None) -> dict[str, Any]:
    out = {
        "id": str(obj.id),
        "tenant_id": str(obj.tenant_id),
        "status": doc_status,
        "number": obj.number or "",
        "total": _money(total, currency),
        "valid_until": valid_until.isoformat() if isinstance(valid_until, dt.date) else None,
        "quote_status": str(obj.status),
        "version": int(getattr(obj, "version", 1) or 1),
        "audit": _audit(obj),
    }
    return {k: out[k] for k in PORTAL_FIELDS}


def _party(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> Party:
    ctx.require_company(company_id)
    party = Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=party_id).first()
    if party is None or "customer" not in (party.roles or []):
        raise NotFound("not found")
    return party


def _rows(ctx: TenantContext, perm: str, company_id: uuid.UUID, party_id: uuid.UUID, qs: Any) -> Any:
    require(ctx, perm, {"company_id": company_id, "party_id": party_id})
    party = _party(ctx, company_id, party_id)
    return qs.filter(tenant_id=ctx.tenant_id, company_id=company_id, party=party).order_by("-document_date", "-id")


def _one(rows: Any, doc_id: Any) -> Any:
    try:
        key = uuid.UUID(str(doc_id))
    except (TypeError, ValueError) as exc:
        raise NotFound("not found") from exc
    obj = rows.filter(id=key).first()
    if obj is None:
        raise NotFound("not found")  # another party's document: same answer as a missing one
    return obj


# --------------------------------------------------------------------------- quotes

_QUOTE_VISIBLE = (Quote.Status.SENT, Quote.Status.ACCEPTED, Quote.Status.ORDERED, Quote.Status.CANCELLED)


def portal_quote_json(q: Quote) -> dict[str, Any]:
    status = {"SENT": "SUBMITTED", "ACCEPTED": "APPROVED", "ORDERED": "APPROVED", "CANCELLED": "CANCELLED"}
    return _project(q, total=q.total, currency=q.currency, doc_status=status.get(q.status, "DRAFT"), valid_until=q.valid_until)


def portal_quotes(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    """Quotes sent to the customer (drafts are internal and never listed)."""
    rows = _rows(ctx, PERM_QUOTE, company_id, party_id, Quote.objects.filter(status__in=_QUOTE_VISIBLE))
    return [portal_quote_json(q) for q in rows]


def portal_quote(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID, quote_id: Any) -> dict[str, Any]:
    rows = _rows(ctx, PERM_QUOTE, company_id, party_id, Quote.objects.filter(status__in=_QUOTE_VISIBLE))
    return portal_quote_json(_one(rows, quote_id))


# --------------------------------------------------------------------------- orders / invoices / notes / receipts


def portal_order_json(o: SalesOrder) -> dict[str, Any]:
    status = "CANCELLED" if o.status == SalesOrder.Status.CANCELLED else "APPROVED"
    return _project(o, total=o.total, currency=o.currency, doc_status=status)


def portal_orders(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    qs = SalesOrder.objects.exclude(status__in=(SalesOrder.Status.DRAFT, SalesOrder.Status.PENDING_APPROVAL))
    return [portal_order_json(o) for o in _rows(ctx, PERM_ORDER, company_id, party_id, qs)]


def portal_order(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID, order_id: Any) -> dict[str, Any]:
    qs = SalesOrder.objects.exclude(status__in=(SalesOrder.Status.DRAFT, SalesOrder.Status.PENDING_APPROVAL))
    return portal_order_json(_one(_rows(ctx, PERM_ORDER, company_id, party_id, qs), order_id))


def portal_invoice_json(i: Invoice) -> dict[str, Any]:
    return _project(i, total=i.total, currency=i.currency, doc_status="POSTED", valid_until=i.due_date)


def portal_invoices(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    qs = Invoice.objects.filter(status=Invoice.Status.POSTED)
    return [portal_invoice_json(i) for i in _rows(ctx, PERM_INVOICE, company_id, party_id, qs)]


def portal_invoice(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID, invoice_id: Any) -> dict[str, Any]:
    qs = Invoice.objects.filter(status=Invoice.Status.POSTED)
    return portal_invoice_json(_one(_rows(ctx, PERM_INVOICE, company_id, party_id, qs), invoice_id))


def portal_credit_notes(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    qs = SalesNote.objects.filter(kind=SalesNote.Kind.CREDIT, status=SalesNote.Status.POSTED)
    rows = _rows(ctx, PERM_CREDIT_NOTE, company_id, party_id, qs)
    return [_project(n, total=n.total, currency=n.currency, doc_status="POSTED") for n in rows]


def portal_receipts(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    qs = Receipt.objects.filter(status=Receipt.Status.POSTED)
    rows = _rows(ctx, PERM_RECEIPT, company_id, party_id, qs)
    return [_project(r, total=r.amount, currency=r.currency, doc_status="POSTED") for r in rows]


def portal_open_items(ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> list[dict[str, Any]]:
    """Open invoices of the customer (for payment intents): number, due date and open balance only."""
    require(ctx, PERM_INVOICE, {"company_id": company_id, "party_id": party_id})
    party = _party(ctx, company_id, party_id)
    rows = ArOpenItem.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, party=party).exclude(
        status=ArOpenItem.Status.PAID
    )
    return [
        {
            "document": {"type": i.document_type, "id": str(i.document_id), "number": i.document_number},
            "due_date": i.due_date.isoformat() if i.due_date else None,
            "balance": _money(i.balance, i.currency),
        }
        for i in rows.order_by("due_date", "id")
    ]


# --------------------------------------------------------------------------- portal price


def portal_price(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    party_id: uuid.UUID,
    items: Sequence[Mapping[str, Any]],
    on: dt.date | None = None,
    currency: str = "COP",
) -> list[dict[str, Any]]:
    """Final unit prices for the customer (D-024 resolution, no override): ``{item_id, quantity, unit_price}``.

    Evaluated in a savepoint that is rolled back (formula snapshots are not kept for a portal preview).
    """
    ctx = uow.ctx
    require(ctx, PERM_QUOTE, {"company_id": company_id, "party_id": party_id})
    party = _party(ctx, company_id, party_id)
    if not isinstance(items, Sequence) or not items or len(items) > 200:
        raise ValidationFailed("items must be a list of 1–200 {item_id, quantity}", field="items")
    day = on or clock.business_date()
    out: list[dict[str, Any]] = []
    with transaction.atomic():
        for n, it in enumerate(items):
            try:
                item_id = uuid.UUID(str(it.get("item_id")))
                qty = Decimal(str(it.get("quantity", "1")))
            except (ValueError, ArithmeticError) as exc:
                raise ValidationFailed(f"items[{n}] needs item_id and quantity", field="items") from exc
            if qty <= 0:
                raise ValidationFailed(f"items[{n}].quantity must be positive", field="items")
            res = engine.resolve(
                uow,
                company_id,
                engine.PriceRequest(item_id=item_id, quantity=qty, on=day, currency=currency, party=party, channel="portal"),
            )
            out.append({"item_id": str(item_id), "quantity": f"{qty:f}", "unit_price": engine.explain(res)})
        transaction.set_rollback(True)
    return out


__all__ = [
    "CONTRACT_VERSION",
    "PORTAL_FIELDS",
    "PORTAL_FORBIDDEN",
    "KitComponent",
    "KitExplosion",
    "PartyInfo",
    "PartyService",
    "portal_credit_notes",
    "portal_invoice",
    "portal_invoices",
    "portal_open_items",
    "portal_order",
    "portal_orders",
    "portal_price",
    "portal_quote",
    "portal_quotes",
    "portal_receipts",
]
