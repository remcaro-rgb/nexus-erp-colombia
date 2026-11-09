"""SchedulerService (owner P07): cron jobs in the owner's company timezone (C2-4).

Contract: jobs run as a service principal with an explicit TenantContext;
each run executes in its own UoW; runs are idempotent per (job, scheduled_for).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kernel.tenancy import TenantContext


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    id: uuid.UUID
    code: str
    cron: str
    timezone: str
    task_name: str
    args: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@runtime_checkable
class SchedulerService(Protocol):
    def schedule(
        self,
        ctx: TenantContext,
        company_id: uuid.UUID,
        code: str,
        cron: str,
        task_name: str,
        args: dict[str, Any] | None = None,
    ) -> ScheduledJob: ...

    def cancel(self, ctx: TenantContext, job_id: uuid.UUID) -> None: ...

    def next_run(self, job: ScheduledJob, after: dt.datetime) -> dt.datetime:
        """Next aware UTC instant, evaluated in ``job.timezone``."""
        ...
