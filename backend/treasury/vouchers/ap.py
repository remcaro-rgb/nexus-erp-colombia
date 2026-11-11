"""AP open-item adapter over P11's ``purchasing_ap_open_item`` (``treasury.interfaces.ApOpenItems``, CCR P16a-1).

P11a exposes no application interface for AP open items yet; treasury applies CE payments here under the same
rules as P10's ``ArOpenItems``: inside the caller's unit of work, row locks in ascending id, never past the open
balance (``treasury_overapplication``), COP at the item's historical rate (GL AP = Σ ``balance_cop``). Every
application is recorded in ``treasury_ap_application`` so a reversal re-opens exactly what was applied.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from kernel import clock
from kernel.errors import NotFound
from kernel.interfaces.common import DocumentRef
from kernel.money import q_amount
from kernel.tenancy import require_current
from kernel.uow import UnitOfWork
from purchasing.models import ApOpenItem as ApRow
from treasury.errors import TreasuryOverapplication
from treasury.interfaces import ApApplied, ApOpenItem
from treasury.models import ApApplication, TreasuryVoucher


def _status(item: ApRow) -> str:
    if item.balance == 0:
        return ApRow.Status.PAID
    return ApRow.Status.PARTIAL if item.balance < item.amount else ApRow.Status.OPEN


def _as_item(row: ApRow) -> ApOpenItem:
    return ApOpenItem(
        id=row.id,
        party_id=row.supplier.party_id or row.supplier_id,
        supplier_id=row.supplier_id,
        document=DocumentRef(row.document_type, row.document_id, row.document_number),
        due_date=row.due_date,
        currency=row.currency,
        open_amount=row.balance,
        fx_rate=row.fx_rate,
    )


class PurchasingApOpenItems:
    """``treasury.interfaces.ApOpenItems`` implemented over the P11 AP sub-ledger table."""

    def open_items(self, company_id: uuid.UUID, party_id: uuid.UUID) -> list[ApOpenItem]:
        ctx = require_current()
        rows = (
            ApRow.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, balance__gt=0)
            .select_related("supplier")
            .order_by("due_date", "id")
        )
        return [i for i in (_as_item(r) for r in rows) if i.party_id == party_id]

    def get(self, company_id: uuid.UUID, open_item_id: uuid.UUID) -> ApOpenItem | None:
        ctx = require_current()
        row = (
            ApRow.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=open_item_id)
            .select_related("supplier")
            .first()
        )
        return None if row is None else _as_item(row)

    def apply(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        voucher: DocumentRef,
        applications: list[tuple[uuid.UUID, Decimal]],
    ) -> list[ApApplied]:
        ctx = uow.ctx
        wanted: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        for oid, amount in applications:
            if amount <= 0:
                raise TreasuryOverapplication("application amounts must be positive", open_item_id=str(oid))
            wanted[oid] += amount
        rows = {
            r.id: r
            for r in ApRow.objects.select_for_update()
            .filter(tenant_id=ctx.tenant_id, company_id=company_id, id__in=sorted(wanted))
            .order_by("id")
        }
        out: list[ApApplied] = []
        for oid in sorted(wanted):
            item = rows.get(oid)
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
            item.updated_at = clock.now()
            item.save(update_fields=["balance", "balance_cop", "status", "version", "updated_by", "updated_at"])
            ApApplication.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                voucher_id=voucher.id,
                open_item_id=oid,
                amount=amount,
                amount_cop=cop,
                applied_on=clock.business_date(),
            )
            out.append(ApApplied(oid, amount, cop))
        return out

    def unapply(self, uow: UnitOfWork, company_id: uuid.UUID, voucher: DocumentRef) -> None:
        ctx = uow.ctx
        apps = list(
            ApApplication.objects.select_for_update()
            .filter(tenant_id=ctx.tenant_id, company_id=company_id, voucher_id=voucher.id, reversed=False)
            .order_by("open_item_id", "id")
        )
        items = {
            r.id: r
            for r in ApRow.objects.select_for_update()
            .filter(tenant_id=ctx.tenant_id, company_id=company_id, id__in=sorted({a.open_item_id for a in apps}))
            .order_by("id")
        }
        for app in apps:
            item = items[app.open_item_id]
            item.balance += app.amount
            item.balance_cop += app.amount_cop
            item.status = _status(item)
            item.version += 1
            item.updated_by = ctx.principal_id
            item.updated_at = clock.now()
            item.save(update_fields=["balance", "balance_cop", "status", "version", "updated_by", "updated_at"])
            app.reversed = True
            app.save(update_fields=["reversed"])


def applications_of(voucher: TreasuryVoucher) -> list[ApApplication]:
    return list(voucher.ap_applications.filter(reversed=False).order_by("open_item_id"))
