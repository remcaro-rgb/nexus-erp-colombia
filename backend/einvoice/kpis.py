"""KPI provider for ``GET /api/v1/kpis/facturacion`` (module-registry entry ``facturacion``).

Server-side aggregates, company + period scoped, policy-filtered: a KPI the user may not read is absent.
A zero denominator returns ``None`` with ``empty_reason`` (the UI shows '—' + 'Sin base para calcular').
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from einvoice import service
from einvoice.models import FiscalDocument, NumberingRange
from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext

S = FiscalDocument.State
VALIDATED = (S.ACCEPTED, S.ACCEPTED_WITH_NOTICES)
SENT = (S.ACCEPTED, S.ACCEPTED_WITH_NOTICES, S.REJECTED, S.SUBMITTED, S.TRANSMITTED_LATE, S.ERROR_RETRYABLE)
PENDING = (S.REJECTED, S.ERROR_RETRYABLE, S.CONTINGENCY_ISSUED, S.SUBMITTED, S.TRANSMITTED_LATE, S.BUILT, S.SIGNED)


def period_bounds(period: str | None) -> tuple[dt.date, dt.date]:
    today = clock.business_date()
    if not period:
        start = today.replace(day=1)
    else:
        try:
            year, month = (int(p) for p in period.split("-"))
            start = dt.date(year, month, 1)
        except ValueError:
            raise ValidationFailed("period must be YYYY-MM", code="filter_invalid") from None
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, end


def _allowed(ctx: TenantContext, permission: str) -> bool:
    return bool(services.get(PolicyService).authorize(ctx, permission).allowed)


def _delta(cur: Decimal | None, prev: Decimal | None) -> str | None:
    if cur is None or prev is None or prev == 0:
        return None  # zero denominator → '—' (never 0, ∞ or NaN)
    return str(((cur - prev) * 100 / abs(prev)).quantize(Decimal("0.1")))


def _kpi(kpi_id: str, unit: str, cur: Decimal | int | None, prev: Decimal | int | None = None) -> dict[str, Any]:
    c = None if cur is None else Decimal(cur)
    p = None if prev is None else Decimal(prev)
    return {
        "id": kpi_id,
        "value": None if c is None else str(c),
        "previous": None if p is None else str(p),
        "delta": _delta(c, p),
        "unit": unit,
        "state": "ok" if c is not None else "no_data",
        "formula_ref": f"module-registry:facturacion.{kpi_id}",
    }


def _documents(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    qs = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).exclude(family="FE_RECEIVED")
    return services.get(PolicyService).scope_queryset(ctx, "einvoice.document.read", qs)


def facturacion_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    """``{as_of, kpis:[{id, value, previous, delta, unit, state, formula_ref}], generated_at}`` (registry api.kpis)."""
    ctx.require_company(company_id)
    start, end = period_bounds(period)
    prev_start = (start - dt.timedelta(days=1)).replace(day=1)
    cutoff = min(clock.now(), clock.local_midnight_utc(end))
    kpis: list[dict[str, Any]] = []
    if _allowed(ctx, "einvoice.document.read"):
        docs = _documents(ctx, company_id)
        cur = docs.filter(issue_date__gte=start, issue_date__lt=end)
        prev = docs.filter(issue_date__gte=prev_start, issue_date__lt=start)
        kpis.append(_kpi("docs_emitidos", "count", cur.count(), prev.count()))

        def pct(qs: Any) -> Decimal | None:
            sent = qs.filter(status__in=SENT).count()
            return (
                None
                if sent == 0
                else (Decimal(qs.filter(status__in=VALIDATED).count() * 100) / sent).quantize(Decimal("0.1"))
            )

        validated = _kpi("docs_validados_pct", "percent", pct(cur), pct(prev))
        validated["label_note"] = "Simulado"
        if validated["value"] is None:
            validated["empty_reason"] = "Sin base para calcular"
        kpis.append(validated)
        prev_cutoff = clock.local_midnight_utc(start)
        kpis.append(
            _kpi(
                "docs_rechazados",
                "count",
                docs.filter(status__in=PENDING, created_at__lt=cutoff).count(),
                docs.filter(status__in=PENDING, created_at__lt=prev_cutoff).count(),
            )
        )
    if _allowed(ctx, "einvoice.numbering_range.read"):
        today = clock.business_date()
        ranges = NumberingRange.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, status=NumberingRange.Status.ACTIVE, valid_to__gte=today
        )
        remaining = [service.range_usage(r)["remaining"] for r in ranges]
        kpis.append(_kpi("numeracion_restante", "count", min(remaining) if remaining else None))
    return {
        "as_of": cutoff.isoformat(),
        "generated_at": clock.now().isoformat(),
        "kpis": kpis,
        "simulated": True,
        "period": start.strftime("%Y-%m"),
    }


def panel(ctx: TenantContext, company_id: uuid.UUID, panel_id: str, period: str | None = None) -> dict[str, Any]:
    """``GET /api/v1/kpis/facturacion/panels/{panel_id}`` (registry api.panel)."""
    from einvoice.lifecycle import FISCAL_STATUS

    ctx.require_company(company_id)
    if panel_id == "estado_documentos":
        if not _allowed(ctx, "einvoice.document.read"):
            return {"state": "not_available", "series": [], "points": []}
        start, end = period_bounds(period)
        counts: dict[str, int] = {}
        for status in (
            _documents(ctx, company_id)
            .filter(issue_date__gte=start, issue_date__lt=end)
            .values_list("status", flat=True)
        ):
            counts[status] = counts.get(status, 0) + 1
        points = [
            {
                "label": k,
                "values": {"documentos": str(v)},
                "fiscal_status": FISCAL_STATUS[k],
                "drill": f"/m/facturacion/documentos?estado={k}",
            }
            for k, v in sorted(counts.items())
        ]
        return {
            "state": "ok" if points else "no_data",
            "series": [{"id": "documentos", "label": "Documentos"}],
            "points": points,
            "simulated": True,
        }
    if panel_id == "contingencia":
        if not _allowed(ctx, "einvoice.contingency.read"):
            return {"state": "not_available", "series": [], "points": []}
        over = service.contingency_overview(ctx, company_id)
        keys = (
            "active",
            "kind",
            "since",
            "restored_at",
            "deadline_at",
            "remaining_seconds",
            "overdue",
            "pending_documents",
        )
        return {"state": "ok", "series": [], "points": [], "contingency": {k: over[k] for k in keys}}
    raise ValidationFailed(f"unknown panel {panel_id!r}", code="filter_invalid")
