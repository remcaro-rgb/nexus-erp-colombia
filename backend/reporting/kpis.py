"""KPI provider for ``GET /api/v1/kpis/reportes`` (+ ``/panels/ejecuciones_diarias``), module-registry ``reportes``.

Server-side, company + period scoped (``YYYY-MM``, default the current business month), policy-filtered: a KPI the
principal may not read is absent. ``reportes_disponibles`` counts the saved reports the principal can run plus the
semantic entities visible to it; run counts include the principal's own runs and runs of reports visible to it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from django.db.models import Q

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from reporting import engine
from reporting.models import ReportRun, SavedReport


def _month(period: str | None) -> tuple[dt.date, dt.date]:
    if period:
        try:
            start = dt.date.fromisoformat(f"{period}-01")
        except ValueError:
            raise ValidationFailed("period must be YYYY-MM", field="period", code="filter_invalid") from None
    else:
        start = clock.business_date().replace(day=1)
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return start, end


def _bounds(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return clock.local_midnight_utc(start), clock.local_midnight_utc(end + dt.timedelta(days=1))


def _allowed(ctx: TenantContext, action: str) -> bool:
    return services.get(PolicyService).authorize(ctx, action).allowed


def _kpi(kid: str, value: int, previous: int | None) -> dict[str, Any]:
    return {
        "id": kid,
        "value": str(value),
        "previous": None if previous is None else str(previous),
        "delta": None if previous is None else str(value - previous),
        "unit": "count",
        "state": "ok",
        "formula_ref": f"module-registry:reportes.{kid}",
    }


def _runs(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    me = ctx.on_behalf_of or ctx.principal_id
    return ReportRun.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).filter(
        Q(principal_id=me) | Q(report__owner_id=me) | Q(report__published_at__isnull=False)
    )


def reportes_kpis(
    ctx: TenantContext, company_id: uuid.UUID, period: str | None, compare: bool = True
) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = _month(period)
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    kpis: list[dict[str, Any]] = []
    has_runs = False
    if _allowed(ctx, "reporting.report.read"):
        me = ctx.on_behalf_of or ctx.principal_id
        saved = SavedReport.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status="ACTIVE").filter(
            Q(owner_id=me) | Q(published_at__isnull=False)
        )
        kpis.append(_kpi("reportes_disponibles", saved.count() + len(engine.visible_entities(ctx)), None))
        runs = _runs(ctx, company_id)
        has_runs = runs.exists()

        def count(lo: dt.date, hi: dt.date, failed: bool) -> int:
            a, b = _bounds(lo, hi)
            qs = runs.filter(started_at__gte=a, started_at__lt=b)
            return int(qs.filter(state=ReportRun.State.FAILED).count() if failed else qs.count())

        for kid, failed in (("ejecuciones", False), ("ejecuciones_fallidas", True)):
            kpis.append(_kpi(kid, count(start, end, failed), count(prev_start, prev_end, failed) if compare else None))
    if _allowed(ctx, "reporting.report.update"):
        active = SavedReport.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, status="ACTIVE", scheduled_job_id__isnull=False
        )
        kpis.append(_kpi("programados_activos", active.count(), None))
    return {
        "as_of": end.isoformat(),
        "generated_at": clock.now().isoformat(),
        "period": f"{start:%Y-%m}",
        "kpis": kpis,
        "empty": not has_runs,
    }


def daily_runs_panel(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> dict[str, Any]:
    """Bar panel ``ejecuciones_diarias``: runs per day of the month."""
    ctx.require_company(company_id)
    if not _allowed(ctx, "reporting.report.read"):
        return {"state": "not_available", "series": [], "points": []}
    start, end = _month(period)
    runs = _runs(ctx, company_id)
    points = []
    day = start
    any_data = False
    while day <= end:
        a, b = _bounds(day, day)
        n = runs.filter(started_at__gte=a, started_at__lt=b).count()
        any_data = any_data or n > 0
        points.append({"label": day.isoformat(), "values": {"ejecuciones": str(n)}})
        day += dt.timedelta(days=1)
    return {"state": "ok" if any_data else "no_data", "series": [{"id": "ejecuciones", "label": "Ejecuciones"}],
            "points": points}  # fmt: skip
