"""AR sub-ledger: open items, applications, aging and statements (GL AR = Σ ``balance_cop``, invariant 4).

:class:`SalesArOpenItems` implements ``kernel.interfaces.ar.ArOpenItems`` for P16 treasury receipts (registered
at app ready): application runs in the caller's UoW under row locks on the open items (ascending id) and never
over-applies (``treasury_overapplication``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from kernel import clock
from kernel.errors import NotFound
from kernel.interfaces.ar import Application, OpenItem
from kernel.interfaces.common import DocumentRef
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import TreasuryOverapplication
from sales.models import ArApplication, ArOpenItem, Party
from sales.services.common import require

BUCKETS = (("current", None, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("over_90", 91, None))


def open_item(
    uow: UnitOfWork,
    party: Party,
    document: DocumentRef,
    day: dt.date,
    due: dt.date,
    currency: str,
    fx: Decimal,
    amount: Decimal,
    amount_cop: Decimal,
) -> ArOpenItem:
    ctx = uow.ctx
    return ArOpenItem.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=party.company_id,
        party=party,
        document_type=document.type,
        document_id=document.id,
        document_number=document.number or "",
        document_date=day,
        due_date=due,
        currency=currency,
        fx_rate=fx,
        amount=amount,
        balance=amount,
        amount_cop=amount_cop,
        balance_cop=amount_cop,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )


def _status(item: ArOpenItem) -> str:
    if item.balance == 0:
        return ArOpenItem.Status.PAID
    return ArOpenItem.Status.PARTIAL if item.balance < item.amount else ArOpenItem.Status.OPEN


def apply_amounts(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    source: DocumentRef,
    applications: list[tuple[uuid.UUID, Decimal]],
    on: dt.date,
) -> list[tuple[ArOpenItem, Decimal, Decimal]]:
    """Reduce open items; returns ``(item, amount, amount_cop at the item's historical rate)`` per application."""
    ctx = uow.ctx
    wanted: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for oid, amount in applications:
        if amount <= 0:
            raise TreasuryOverapplication("application amounts must be positive", open_item_id=str(oid))
        wanted[oid] += amount
    items = {
        i.id: i
        for i in ArOpenItem.objects.select_for_update()
        .filter(tenant_id=ctx.tenant_id, company_id=company_id, id__in=sorted(wanted))
        .order_by("id")
    }
    out: list[tuple[ArOpenItem, Decimal, Decimal]] = []
    for oid in sorted(wanted):
        item = items.get(oid)
        if item is None:
            raise NotFound("open item not found")
        amount = wanted[oid]
        if amount > item.balance:
            raise TreasuryOverapplication(
                f"application {amount} exceeds the open balance {item.balance} of {item.document_number}",
                open_item_id=str(oid),
            )
        cop = item.balance_cop if amount == item.balance else min(item.balance_cop, q_amount(amount * item.fx_rate))
        item.balance -= amount
        item.balance_cop -= cop
        item.status = _status(item)
        item.version += 1
        item.updated_by = ctx.principal_id
        item.save(update_fields=["balance", "balance_cop", "status", "version", "updated_by"])
        ArApplication.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            open_item=item,
            source_type=source.type,
            source_id=source.id,
            source_number=source.number or "",
            amount=amount,
            amount_cop=cop,
            applied_on=on,
        )
        out.append((item, amount, cop))
    return out


def unapply_source(uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef) -> None:
    ctx = uow.ctx
    rows = list(
        ArApplication.objects.select_for_update()
        .filter(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            source_type=source.type,
            source_id=source.id,
            reversed_by__isnull=True,
        )
        .order_by("open_item_id", "id")
    )
    marker = uuid.uuid4()
    for app in rows:
        item = ArOpenItem.objects.select_for_update().get(id=app.open_item_id)
        item.balance += app.amount
        item.balance_cop += app.amount_cop
        item.status = _status(item)
        item.version += 1
        item.save(update_fields=["balance", "balance_cop", "status", "version"])
        app.reversed_by = marker
        app.save(update_fields=["reversed_by"])


def as_open_item(i: ArOpenItem) -> OpenItem:
    return OpenItem(
        i.id,
        i.party_id,
        DocumentRef(i.document_type, i.document_id, i.document_number),
        i.due_date,
        i.currency,
        i.balance,
    )


class SalesArOpenItems:
    """``kernel.interfaces.ar.ArOpenItems`` (owner P10; consumer P16 treasury)."""

    def open_items(self, company_id: uuid.UUID, party_id: uuid.UUID) -> list[OpenItem]:
        rows = ArOpenItem.objects.filter(company_id=company_id, party_id=party_id, balance__gt=0).order_by(
            "due_date", "id"
        )
        return [as_open_item(i) for i in rows]

    def apply(
        self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef, applications: list[Application]
    ) -> None:
        require(uow.ctx, "sales.receipt.post", {"company_id": company_id})
        apply_amounts(
            uow, company_id, receipt, [(a.open_item_id, a.amount) for a in applications], clock.business_date()
        )

    def unapply(self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef) -> None:
        require(uow.ctx, "sales.receipt.post", {"company_id": company_id})
        unapply_source(uow, company_id, receipt)


def aging(ctx: TenantContext, items: Any, as_of: dt.date) -> dict[str, Any]:
    """Aging buckets by days past due (COP), per customer and totals."""
    per_party: dict[uuid.UUID, dict[str, Decimal]] = {}
    names: dict[uuid.UUID, str] = {}
    totals = {b[0]: Decimal(0) for b in BUCKETS}
    for item in items.filter(balance__gt=0, document_date__lte=as_of).select_related("party"):
        days = (as_of - item.due_date).days
        bucket = next(
            name
            for name, lo, hi in BUCKETS
            if (lo is None and days <= hi) or (lo is not None and days >= lo and (hi is None or days <= hi))
        )
        row = per_party.setdefault(item.party_id, {b[0]: Decimal(0) for b in BUCKETS})
        row[bucket] += item.balance_cop
        totals[bucket] += item.balance_cop
        names[item.party_id] = item.party.legal_name
    parties = [
        {
            "party": {"id": str(pid), "kind": "customer"},
            "name": names[pid],
            "buckets": {k: f"{v:f}" for k, v in row.items()},
            "total": f"{sum(row.values(), Decimal(0)):f}",
        }
        for pid, row in sorted(per_party.items(), key=lambda kv: names[kv[0]])
    ]
    return {
        "as_of": as_of.isoformat(),
        "currency": "COP",
        "buckets": [b[0] for b in BUCKETS],
        "parties": parties,
        "totals": {k: f"{v:f}" for k, v in totals.items()},
        "total": f"{sum(totals.values(), Decimal(0)):f}",
    }


def statement(ctx: TenantContext, party: Party, items: Any, date_from: dt.date, date_to: dt.date) -> dict[str, Any]:
    """Customer statement: opening balance, documents and applications in the range, closing balance (COP)."""
    opening = Decimal(0)
    moves: list[dict[str, Any]] = []
    for item in items.filter(party=party).order_by("document_date", "id"):
        apps = list(item.applications.filter(reversed_by__isnull=True).order_by("applied_on", "id"))
        if item.document_date < date_from:
            opening += item.amount_cop - sum((a.amount_cop for a in apps if a.applied_on < date_from), Decimal(0))
        elif item.document_date <= date_to:
            moves.append(
                {
                    "date": item.document_date.isoformat(),
                    "document": {
                        "type": item.document_type,
                        "id": str(item.document_id),
                        "number": item.document_number,
                    },
                    "debit": f"{item.amount_cop:f}",
                    "credit": "0",
                }
            )
        for a in apps:
            if date_from <= a.applied_on <= date_to and not (item.document_date > date_to):
                moves.append(
                    {
                        "date": a.applied_on.isoformat(),
                        "document": {"type": a.source_type, "id": str(a.source_id), "number": a.source_number},
                        "debit": "0",
                        "credit": f"{a.amount_cop:f}",
                    }
                )
            elif a.applied_on < date_from and item.document_date >= date_from:
                opening -= a.amount_cop
    moves.sort(key=lambda m: m["date"])
    balance = opening
    for m in moves:
        balance += Decimal(m["debit"]) - Decimal(m["credit"])
        m["balance"] = f"{balance:f}"
    return {
        "party": {"id": str(party.id), "kind": "customer"},
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "currency": "COP",
        "opening_balance": f"{opening:f}",
        "lines": moves,
        "closing_balance": f"{balance:f}",
    }
