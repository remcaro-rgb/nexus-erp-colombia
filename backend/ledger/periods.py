"""PeriodService implementation (kernel.interfaces.periods) and period commands."""

from __future__ import annotations

import calendar
import datetime as dt
import uuid

from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.periods import PeriodInfo, PeriodStatus
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger.errors import ledger_error
from ledger.models import Period

_TO_INFO: dict[str, PeriodStatus] = {"OPEN": "OPEN", "SOFT_CLOSED": "CLOSED", "CLOSED": "LOCKED"}
_TRANSITIONS = {
    ("OPEN", "SOFT_CLOSED"),
    ("SOFT_CLOSED", "CLOSED"),
    ("OPEN", "CLOSED"),
    ("SOFT_CLOSED", "OPEN"),
    ("CLOSED", "OPEN"),  # reopen: only through an approved reopen request (API layer)
}
STATUS_EVENT = "ledger.period.status_changed.v1"


def to_info(p: Period) -> PeriodInfo:
    return PeriodInfo(p.id, p.company_id, p.code, p.start, p.end, _TO_INFO[p.status])


class LedgerPeriodService:
    def period_for(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        ctx.require_company(company_id)
        p = (
            Period.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, start__lte=day, end__gte=day)
            .order_by("number")
            .first()
        )
        if p is None:
            raise NotFound("no accounting period for this date")
        return to_info(p)

    def current_period(self, ctx: TenantContext, company_id: uuid.UUID) -> PeriodInfo | None:
        try:
            return self.period_for(ctx, company_id, clock.business_date())
        except NotFound:
            return None

    def assert_open(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        try:
            info = self.period_for(ctx, company_id, day)
        except NotFound:
            raise ledger_error("period_closed", f"no accounting period is defined for {day}") from None
        if info.status != "OPEN":
            raise ledger_error("period_closed", f"period {info.code} is not open")
        return info


def ensure_year(uow: UnitOfWork, company_id: uuid.UUID, year: int) -> list[Period]:
    """Create the 12 monthly periods + adjustment period 13 of ``year`` (idempotent)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    out = []
    for month in range(1, 14):
        m = min(month, 12)
        start = dt.date(year, m, 1) if month <= 12 else dt.date(year, 12, 31)
        end = dt.date(year, m, calendar.monthrange(year, m)[1])
        p, _ = Period.objects.get_or_create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            code=f"{year}-{month:02d}",
            defaults={
                "fiscal_year": year,
                "number": month,
                "start": start,
                "end": end,
                "created_by": ctx.principal_id,
                "updated_by": ctx.principal_id,
            },
        )
        out.append(p)
    return out


def change_status(uow: UnitOfWork, period: Period, to_status: str) -> Period:
    if to_status not in Period.Status.values:
        raise ValidationFailed("unknown status", pointer="/status", reason="enum")
    if (period.status, to_status) not in _TRANSITIONS:
        raise Conflict(f"transition {period.status} -> {to_status} is not allowed", code="invalid_transition")
    old = period.status
    period = Period.objects.select_for_update().get(id=period.id)
    period.status = to_status
    period.save_versioned(uow.ctx.principal_id, ["status"])
    uow.emit(
        STATUS_EVENT,
        {"period_id": str(period.id), "from_status": old, "to_status": to_status},
        company_id=period.company_id,
    )
    return period
