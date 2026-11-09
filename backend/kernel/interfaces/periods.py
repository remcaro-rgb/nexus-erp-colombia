"""Accounting periods (owner: P05 ledger). Exposed to ``/api/v1/ui/context``."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from kernel.tenancy import TenantContext

PeriodStatus = Literal["OPEN", "CLOSED", "LOCKED"]


@dataclass(frozen=True, slots=True)
class PeriodInfo:
    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    start: dt.date
    end: dt.date
    status: PeriodStatus
    timezone: str = "America/Bogota"

    def contains(self, day: dt.date) -> bool:
        return self.start <= day <= self.end

    def to_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "company_id": str(self.company_id),
            "code": self.code,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "status": self.status,
            "timezone": self.timezone,
        }


@runtime_checkable
class PeriodService(Protocol):
    def current_period(self, ctx: TenantContext, company_id: uuid.UUID) -> PeriodInfo | None:
        """Period containing today's business date in the company timezone."""
        ...

    def period_for(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        """Period containing ``day``. Raises ``NotFound`` when none is defined."""
        ...

    def assert_open(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        """Raise ``Conflict(code='period_closed')`` unless the period is OPEN (invariant 8)."""
        ...
