"""KPI provider for ``GET /api/v1/kpis/activos`` (+ ``/panels/depreciacion_mensual``), module-registry ``activos``.

Server-side aggregates, company + period scoped (the period is a ledger period id or ``YYYY-MM``; default the
current business month), policy-filtered: a KPI the principal may not read is absent. No KPI of this module
has a denominator (``zero_denominator: not_applicable``); an empty company returns ``no_data`` with value 0.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Q, QuerySet, Sum

from assets import engine
from assets.errors import FilterInvalid
from assets.models import (
    Asset,
    AssetBook,
    AssetBookValue,
    BookValueStatus,
    DepreciationLine,
    Framework,
    Lifecycle,
    RegisterEntry,
)
from kernel import clock, services
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext

ZERO = Decimal(0)
REGISTER_PERMISSION = "assets.asset_register.read"
RUN_PERMISSION = "assets.depreciation_run.read"


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
        start = engine.month_start(clock.business_date())
    return start, engine.month_end(start)


def _allowed(ctx: TenantContext, action: str) -> bool:
    return services.get(PolicyService).authorize(ctx, action).allowed


def _scoped(ctx: TenantContext, action: str, qs: QuerySet[Any]) -> QuerySet[Any]:
    scoped: QuerySet[Any] = services.get(PolicyService).scope_queryset(ctx, action, qs)
    return scoped


def _primary_book(ctx: TenantContext, company_id: uuid.UUID) -> AssetBook | None:
    return (
        AssetBook.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status="ACTIVE")
        .order_by("framework", "code")
        .filter(framework=Framework.NIIF)
        .first()
    )


def _historic_cost(assets: QuerySet[Asset], cut: dt.date) -> Decimal:
    live = (
        assets.exclude(lifecycle=Lifecycle.DRAFT)
        .filter(acquisition_date__lte=cut)
        .filter(Q(disposed_on__isnull=True) | Q(disposed_on__gt=cut))
    )
    return live.aggregate(s=Sum("acquisition_cost"))["s"] or ZERO


def _nbv(entries: QuerySet[RegisterEntry], book: AssetBook, cut: dt.date) -> Decimal:
    agg = entries.filter(book=book, date__lte=cut).aggregate(
        c=Sum("cost_delta"), a=Sum("accumulated_delta"), i=Sum("impairment_delta")
    )
    return (agg["c"] or ZERO) - (agg["a"] or ZERO) - (agg["i"] or ZERO)


def _depreciation(lines: QuerySet[DepreciationLine], book: AssetBook, start: dt.date, end: dt.date) -> Decimal:
    agg = lines.filter(
        run__book=book, run__status="POSTED", run__period_start__gte=start, run__period_end__lte=end
    ).aggregate(s=Sum("amount"))
    return agg["s"] or ZERO


def _kpi(
    kid: str, value: Decimal | int, previous: Decimal | int | None, unit: str, has_data: bool, **extra: Any
) -> dict[str, Any]:
    def fmt(v: Decimal | int) -> str:
        return f"{v:.2f}" if unit == "COP" else str(v)

    delta = None if previous is None else fmt(value - previous)
    return {
        "id": kid,
        "value": fmt(value),
        "previous": None if previous is None else fmt(previous),
        "delta": delta,
        "unit": unit,
        "state": "ok" if has_data else "no_data",
        "formula_ref": f"module-registry:activos.{kid}",
        **extra,
    }


def activos_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None, compare: bool = True) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = period_bounds(ctx, company_id, period)
    prev_end = start - dt.timedelta(days=1)
    prev_start = engine.month_start(prev_end)
    kpis: list[dict[str, Any]] = []
    base = {"tenant_id": ctx.tenant_id, "company_id": company_id}
    book = _primary_book(ctx, company_id)
    if _allowed(ctx, REGISTER_PERMISSION):
        assets = _scoped(ctx, REGISTER_PERMISSION, Asset.objects.filter(**base))
        entries = _scoped(ctx, REGISTER_PERMISSION, RegisterEntry.objects.filter(**base))
        lines = _scoped(ctx, REGISTER_PERMISSION, DepreciationLine.objects.filter(**base))
        has = assets.exclude(lifecycle=Lifecycle.DRAFT).exists()
        cost, cost_prev = _historic_cost(assets, end), _historic_cost(assets, prev_end)
        kpis.append(_kpi("costo_historico", cost, cost_prev if compare else None, "COP", has))
        by_book: dict[str, str] = {}
        dep = dep_prev = ZERO
        for b in AssetBook.objects.filter(**base, status="ACTIVE").order_by("framework", "code"):
            value = _depreciation(lines, b, start, end)
            by_book[b.code] = f"{value:.2f}"
            if book is not None and b.id == book.id:
                dep, dep_prev = value, _depreciation(lines, b, prev_start, prev_end)
        kpis.append(_kpi("depreciacion_periodo", dep, dep_prev if compare else None, "COP", has, by_book=by_book))
        if book is not None:
            kpis.append(
                _kpi(
                    "valor_neto",
                    _nbv(entries, book, end),
                    _nbv(entries, book, prev_end) if compare else None,
                    "COP",
                    has,
                )
            )
    if _allowed(ctx, RUN_PERMISSION) and book is not None:
        bvs = _scoped(ctx, RUN_PERMISSION, AssetBookValue.objects.filter(**base, book=book))
        pending = (
            bvs.filter(
                status=BookValueStatus.ACTIVE,
                depreciation_start__lte=start,
                asset__lifecycle__in=[Lifecycle.ACTIVE, Lifecycle.FULLY_DEPRECIATED],
            )
            .exclude(last_period_end__gte=start)
            .count()
        )
        kpis.append(_kpi("sin_depreciar", pending, None, "count", bvs.exists()))
    now = clock.now()
    return {"as_of": end.isoformat(), "generated_at": now.isoformat(), "period": f"{start:%Y-%m}", "kpis": kpis}


def depreciation_panel(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> dict[str, Any]:
    """Bar panel: posted depreciation per month (last 12 months up to the period), one series per book."""
    ctx.require_company(company_id)
    _start, end = period_bounds(ctx, company_id, period)
    if not _allowed(ctx, REGISTER_PERMISSION):
        return {"state": "not_available", "series": [], "points": []}
    base = {"tenant_id": ctx.tenant_id, "company_id": company_id}
    lines = _scoped(ctx, REGISTER_PERMISSION, DepreciationLine.objects.filter(**base, run__status="POSTED"))
    books = list(AssetBook.objects.filter(**base, status="ACTIVE").order_by("framework", "code"))
    first = engine.add_months(engine.month_start(end), -11)
    rows = (
        lines.filter(run__period_start__gte=first, run__period_end__lte=end)
        .values("run__book__code", "run__period_start")
        .annotate(s=Sum("amount"))
    )
    data = {(r["run__book__code"], r["run__period_start"]): r["s"] for r in rows}
    points = []
    for i in range(12):
        month = engine.add_months(first, i)
        points.append(
            {"label": f"{month:%Y-%m}", "values": {b.code: f"{data.get((b.code, month), ZERO):.2f}" for b in books}}
        )
    return {
        "state": "ok" if data else "no_data",
        "series": [{"id": b.code, "label": b.name} for b in books],
        "points": points,
    }
