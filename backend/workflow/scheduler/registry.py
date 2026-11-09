"""Job-type registry: modules register handlers; unknown types fail closed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kernel.tenancy import TenantContext

Handler = Callable[[TenantContext, Any, dict[str, Any], str], dict[str, Any]]


@dataclass(frozen=True)
class JobType:
    code: str
    permission: str  # re-evaluated for the owner at every run
    label: str
    handler: Handler | None = None


JOB_TYPES: dict[str, JobType] = {}


def register_job_type(code: str, permission: str, label: str, handler: Handler | None = None, *, replace: bool = False) -> None:
    if code in JOB_TYPES and not replace and JOB_TYPES[code].handler is not None:
        raise ValueError(f"job type {code} already registered")
    JOB_TYPES[code] = JobType(code, permission, label, handler)


# Types named by the prompt; handlers are registered by their owning modules (fail closed until then).
for _code, _perm, _label in (
    ("report_delivery", "reporting.report.read", "Envío de informes"),
    ("recurring_entries", "ledger.journal_entry.create", "Asientos recurrentes"),
    ("payroll_reminders", "payroll.payroll_run.read", "Recordatorios de nómina"),
    ("ds_weekly_accumulation", "purchasing.support_document.create", "Acumulación semanal documento soporte"),
    ("dian_deadline_checks", "tax.calendar.read", "Vencimientos DIAN"),
    ("fiscal_retries", "einvoice.document.update", "Reintentos fiscales"),
    ("ai_task", "ai.task.run", "Tarea de IA"),
):
    register_job_type(_code, _perm, _label)
