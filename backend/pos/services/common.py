"""Shared helpers of the POS services: policy, numbering, visibility, account roles.

Parsing / policy / audit helpers are the sales ones (``sales.services.common``, P10a): the POS reuses the sale
rules of P10 (pricing D-024, taxes through ``TaxService``) and adds the cash-drawer rules of shared/07 §1.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from django.db.models import QuerySet

from kernel.models import Sequence
from kernel.sequences import allocate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import NumberingRangeMissing
from sales.services.common import allowed, require, scoped

#: Non-fiscal kernel.sequences codes (gap-free per company), created on first use.
SEQUENCES = {
    "session": ("POS-SESSION", "TUR-"),
    "movement": ("POS-CASHMOV", "MCJ-"),
    "return": ("POS-RETURN", "DVP-"),
}
#: Fiscal sequence of the DEE POS (DIAN numbering resolution = external tenant data, never created here).
FISCAL_SEQUENCE = "POS"

#: Account roles of the POS postings (shared/07 §1); role → account determination is tenant data.
ROLE_CASH_POS = "CASH_POS"
ROLE_CASH_DIFFERENCE = "CASH_DIFFERENCE"
ROLE_BANK_TRANSIT = "BANK_TRANSIT"
ROLE_CASH_TRANSFER = "CASH_TRANSFER"  # counterpart of pay-ins / pay-outs (petty cash / safe)
TENDER_ROLES = {
    "CASH": ROLE_CASH_POS,
    "CARD": "CARD_CLEARING",
    "TRANSFER": ROLE_BANK_TRANSIT,
    "QR": ROLE_BANK_TRANSIT,
    "VOUCHER": "VOUCHER_LIABILITY",
}

#: Reading other cashiers' sessions / tickets needs the supervisor read (row policy, T-P12-10).
SUPERVISOR_READ = "pos.z_report.read"


def next_number(uow: UnitOfWork, company_id: uuid.UUID, kind: str, on: dt.date) -> str:
    code, prefix = SEQUENCES[kind]
    ctx = uow.ctx
    Sequence.objects.get_or_create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        sequence_code=code,
        prefix=prefix,
        defaults={"padding": 6},
    )
    return allocate(company_id, code, prefix, on_date=on).formatted


def fiscal_number(uow: UnitOfWork, company_id: uuid.UUID, range_id: uuid.UUID | None, on: dt.date) -> str:
    """Number of the DEE POS from the configured DIAN range (kernel.sequences fiscal row), assigned at commit.

    No client code path and no terminal block ever assigns it (ADR-015). Without an active fiscal ``POS`` range
    valid on ``on`` the checkout fails closed with ``numbering_range_missing``.
    """
    ctx = uow.ctx
    rows = Sequence.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, sequence_code=FISCAL_SEQUENCE, is_fiscal=True, active=True
    )
    if range_id is not None:
        rows = rows.filter(id=range_id)
    valid = [
        s
        for s in rows.order_by("prefix")
        if (s.valid_from is None or s.valid_from <= on) and (s.valid_to is None or on <= s.valid_to)
    ]
    if not valid:
        raise NumberingRangeMissing(f"no active DIAN numbering range for the DEE POS on {on.isoformat()}")
    return allocate(company_id, FISCAL_SEQUENCE, valid[0].prefix, on_date=on).formatted


def is_supervisor(ctx: TenantContext) -> bool:
    return allowed(ctx, SUPERVISOR_READ)


def visible(
    ctx: TenantContext, action: str, queryset: QuerySet[Any], cashier_field: str = "cashier_id"
) -> QuerySet[Any]:
    """Policy row scope + own-sessions rule: a cashier sees only the sessions/tickets they operate."""
    qs = scoped(ctx, action, queryset)
    if is_supervisor(ctx):
        return qs
    return qs.filter(**{cashier_field: ctx.principal_id})


__all__ = ["allowed", "fiscal_number", "is_supervisor", "next_number", "require", "visible"]
