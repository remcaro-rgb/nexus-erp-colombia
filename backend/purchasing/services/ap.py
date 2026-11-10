"""AP subledger reads: open items, aging buckets and the GRNI report (receipts not yet invoiced)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from kernel import tenancy
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from purchasing.models import ApOpenItem, PurchaseOrderLine, ReceiptLine, VendorInvoiceLine
from purchasing.services.common import scoped

BUCKETS = (("current", None, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("over_90", 91, None))


def _m(value: Decimal) -> str:
    return f"{q_amount(value):f}"


def bucket_of(days_overdue: int) -> str:
    for name, low, high in BUCKETS:
        if (low is None or days_overdue >= low) and (high is None or days_overdue <= high):
            return name
    return "over_90"


def aging(
    ctx: TenantContext, company_id: uuid.UUID, as_of: dt.date, supplier_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Open AP balance (COP) by days past due at ``as_of``, per vendor and in total."""
    ctx.require_company(company_id)
    with tenancy.scope(ctx):
        return _aging(ctx, company_id, as_of, supplier_id)


def _aging(ctx: TenantContext, company_id: uuid.UUID, as_of: dt.date, supplier_id: uuid.UUID | None) -> dict[str, Any]:
    qs = scoped(ctx, "purchasing.ap_aging.read", ApOpenItem.objects.filter(company_id=company_id))
    qs = qs.filter(document_date__lte=as_of, balance_cop__gt=0).select_related("supplier")
    if supplier_id is not None:
        qs = qs.filter(supplier_id=supplier_id)
    totals: dict[str, Decimal] = {name: Decimal(0) for name, *_ in BUCKETS}
    vendors: dict[uuid.UUID, dict[str, Any]] = {}
    for item in qs.order_by("supplier__code", "due_date", "id"):
        bucket = bucket_of(max((as_of - item.due_date).days, 0))
        row = vendors.setdefault(
            item.supplier_id,
            {
                "party": {
                    "id": str(item.supplier.party_id or item.supplier_id),
                    "kind": "vendor",
                    "name": item.supplier.name,
                },
                "supplier_code": item.supplier.code,
                "buckets": {name: Decimal(0) for name, *_ in BUCKETS},
            },
        )
        row["buckets"][bucket] += item.balance_cop
        totals[bucket] += item.balance_cop
    return {
        "as_of": as_of.isoformat(),
        "currency": "COP",
        "buckets": [name for name, *_ in BUCKETS],
        "rows": [
            {
                **row,
                "buckets": {k: _m(v) for k, v in row["buckets"].items()},
                "total": _m(sum(row["buckets"].values(), Decimal(0))),
            }
            for row in vendors.values()
        ],
        "totals": {k: _m(v) for k, v in totals.items()},
        "total": _m(sum(totals.values(), Decimal(0))),
    }


def grni(ctx: TenantContext, company_id: uuid.UUID, as_of: dt.date) -> dict[str, Any]:
    """GRNI = Σ validated receipt value − Σ GRNI cleared by posted invoices, per PO line (invariant 4)."""
    ctx.require_company(company_id)
    with tenancy.scope(ctx):
        return _grni(ctx, company_id, as_of)


def _grni(ctx: TenantContext, company_id: uuid.UUID, as_of: dt.date) -> dict[str, Any]:
    lines = scoped(ctx, "purchasing.grni.read", PurchaseOrderLine.objects.filter(company_id=company_id))
    received: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for row in (
        ReceiptLine.objects.filter(order_line__in=lines, receipt__status="VALIDATED", receipt__document_date__lte=as_of)
        .values("order_line_id")
        .annotate(q=Sum("quantity"), a=Sum("amount_cop"))
    ):
        received[row["order_line_id"]] = (row["q"], row["a"])
    cleared: dict[uuid.UUID, tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal(0), Decimal(0)))
    for row in (
        VendorInvoiceLine.objects.filter(
            order_line__in=lines, invoice__status="POSTED", invoice__document_date__lte=as_of
        )
        .values("order_line_id")
        .annotate(q=Sum("quantity"), a=Sum("grni_amount_cop"))
    ):
        cleared[row["order_line_id"]] = (row["q"], row["a"])
    rows = []
    total = Decimal(0)
    for line in (
        lines.filter(id__in=list(received)).select_related("order__supplier").order_by("order__number", "line_no")
    ):
        rq, ra = received[line.id]
        iq, ia = cleared[line.id]
        balance = ra - ia
        if rq - iq == 0 and balance == 0:
            continue
        total += balance
        rows.append(
            {
                "order": {"type": "purchasing.order", "id": str(line.order_id), "number": line.order.number},
                "order_line": line.line_no,
                "supplier_code": line.order.supplier.code,
                "received_qty": f"{rq:f}",
                "invoiced_qty": f"{iq:f}",
                "pending_qty": f"{rq - iq:f}",
                "grni_cop": _m(balance),
            }
        )
    return {"as_of": as_of.isoformat(), "currency": "COP", "rows": rows, "total": _m(total)}
