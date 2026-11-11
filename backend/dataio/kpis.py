"""KPI provider for ``GET /api/v1/kpis/datos`` (+ ``/panels/lotes_por_tipo``), module-registry ``datos``.

Server-side aggregates, company + period scoped (ledger period id or ``YYYY-MM``; default the current business
month in America/Bogota), policy-filtered: a KPI the principal may not read is absent. No KPI of this module has
a denominator (``zero_denominator: not_applicable``); an empty company returns ``no_data`` with value 0.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Count, Sum

from dataio.errors import FilterInvalid
from dataio.models import ExportJob, ImportJob, ImportStatus
from dataio.service import allowed
from kernel import clock, services
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext

BOGOTA = ZoneInfo("America/Bogota")
READ = "dataio.import.read"
EXPORT_READ = "dataio.export.read"


def _month_end(start: dt.date) -> dt.date:
    nxt = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def period_bounds(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> tuple[dt.date, dt.date]:
    if period and len(period) == 36:
        from ledger.models import Period

        try:
            row = Period.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=uuid.UUID(period)).first()
        except ValueError:
            row = None
        if row is None:
            raise FilterInvalid("unknown period")
        return row.start, row.end
    if period:
        try:
            start = dt.date.fromisoformat(f"{period}-01")
        except ValueError:
            raise FilterInvalid("period must be YYYY-MM or a period id") from None
    else:
        start = clock.business_date().replace(day=1)
    return start, _month_end(start)


def previous(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """Same-length period immediately before (an open month compares day 1..same day of the previous month)."""
    today = clock.business_date()
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    if start <= today <= end:
        day = min(today.day, prev_end.day)
        return prev_start, prev_start.replace(day=day)
    return prev_start, prev_end


def _window(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    lo = dt.datetime.combine(start, dt.time.min, tzinfo=BOGOTA)
    hi = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=BOGOTA)
    return lo, hi


def _scoped(ctx: TenantContext, action: str, qs: Any) -> Any:
    return services.get(PolicyService).scope_queryset(ctx, action, qs)


def _imports(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    return _scoped(ctx, READ, ImportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id))


def _counts(ctx: TenantContext, company_id: uuid.UUID, start: dt.date, end: dt.date) -> dict[str, int]:
    lo, hi = _window(start, end)
    jobs = _imports(ctx, company_id)
    committed = jobs.filter(import_status=ImportStatus.COMMITTED, committed_at__gte=lo, committed_at__lt=hi)
    rejected = jobs.filter(import_status=ImportStatus.FAILED, failed_at__gte=lo, failed_at__lt=hi)
    return {
        "lotes_importados": committed.count(),
        "filas_importadas": int(committed.aggregate(s=Sum("applied_rows"))["s"] or 0),
        "lotes_rechazados": rejected.count(),
    }


def _exports(ctx: TenantContext, company_id: uuid.UUID, start: dt.date, end: dt.date) -> int:
    lo, hi = _window(start, end)
    qs = _scoped(ctx, EXPORT_READ, ExportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id))
    return int(qs.filter(export_status="COMPLETED", completed_at__gte=lo, completed_at__lt=hi).count())


def _kpi(kid: str, value: int, prev: int | None, has: bool) -> dict[str, Any]:
    return {
        "id": kid,
        "value": str(value),
        "previous": None if prev is None else str(prev),
        "delta": None if prev is None else str(value - prev),
        "unit": "count",
        "state": "ok" if has else "no_data",
        "formula_ref": f"module-registry:datos.{kid}",
    }


def datos_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None, compare: bool = True) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = period_bounds(ctx, company_id, period)
    p_start, p_end = previous(start, end)
    kpis: list[dict[str, Any]] = []
    if allowed(ctx, READ):
        cur = _counts(ctx, company_id, start, end)
        prev = _counts(ctx, company_id, p_start, p_end) if compare else None
        has = _imports(ctx, company_id).filter(import_status__in=[ImportStatus.COMMITTED, ImportStatus.FAILED]).exists()
        for kid in ("lotes_importados", "filas_importadas", "lotes_rechazados"):
            kpis.append(_kpi(kid, cur[kid], None if prev is None else prev[kid], has))
    if allowed(ctx, EXPORT_READ):
        value = _exports(ctx, company_id, start, end)
        prev_value = _exports(ctx, company_id, p_start, p_end) if compare else None
        has = _scoped(
            ctx, EXPORT_READ, ExportJob.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        ).exists()
        kpis.append(_kpi("exportaciones", value, prev_value, has))
    return {"as_of": end.isoformat(), "generated_at": clock.now().isoformat(), "period": f"{start:%Y-%m}", "kpis": kpis}


def lotes_por_tipo(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> dict[str, Any]:
    """Bar panel: import batches of the period per template (committed vs rejected)."""
    ctx.require_company(company_id)
    if not allowed(ctx, READ):
        return {"state": "not_available", "series": [], "points": []}
    start, end = period_bounds(ctx, company_id, period)
    lo, hi = _window(start, end)
    jobs = _imports(ctx, company_id)
    data: dict[str, dict[str, int]] = {}
    for status, field in ((ImportStatus.COMMITTED, "committed_at"), (ImportStatus.FAILED, "failed_at")):
        rows = (
            jobs.filter(import_status=status, **{f"{field}__gte": lo, f"{field}__lt": hi})
            .values("entity")
            .annotate(n=Count("id"))
        )
        for r in rows:
            data.setdefault(r["entity"], {"confirmado": 0, "rechazado": 0})[
                "confirmado" if status == ImportStatus.COMMITTED else "rechazado"
            ] = r["n"]
    points = [{"label": k, "values": {s: str(v) for s, v in data[k].items()}} for k in sorted(data)]
    return {
        "state": "ok" if points else "no_data",
        "series": [{"id": "confirmado", "label": "Confirmados"}, {"id": "rechazado", "label": "Rechazados"}],
        "points": points,
    }
