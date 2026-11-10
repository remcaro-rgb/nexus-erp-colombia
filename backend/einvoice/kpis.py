"""KPI provider for ``GET /api/v1/kpis/facturacion`` (module-registry entry ``facturacion``).

Server-side aggregates, company + period scoped, policy-filtered: a KPI the user may not read is absent.
A zero denominator returns ``None`` with ``empty_reason`` (the UI shows '—' + 'Sin base para calcular').
"""

from __future__ import annotations

import datetime as dt
import uuid
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


def facturacion_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = period_bounds(period)
    prev_start = (start - dt.timedelta(days=1)).replace(day=1)
    kpis: dict[str, Any] = {}
    panels: dict[str, Any] = {}
    docs = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).exclude(family="FE_RECEIVED")
    if _allowed(ctx, "einvoice.document.read"):
        cur = docs.filter(issue_date__gte=start, issue_date__lt=end)
        prev = docs.filter(issue_date__gte=prev_start, issue_date__lt=start)
        kpis["docs_emitidos"] = {"value": cur.count(), "previous": prev.count(), "unit": "count"}

        def pct(qs: Any) -> str | None:
            sent = qs.filter(status__in=SENT).count()
            return None if sent == 0 else str(round(qs.filter(status__in=VALIDATED).count() * 100 / sent, 1))

        value = pct(cur)
        kpis["docs_validados_pct"] = {
            "value": value,
            "previous": pct(prev),
            "unit": "percent",
            "label_note": "Simulado",
            "empty_reason": None if value is not None else "Sin base para calcular",
        }
        cutoff = clock.local_midnight_utc(end)
        prev_cutoff = clock.local_midnight_utc(start)
        kpis["docs_rechazados"] = {
            "value": docs.filter(status__in=PENDING, created_at__lt=cutoff).count(),
            "previous": docs.filter(status__in=PENDING, created_at__lt=prev_cutoff).count(),
            "unit": "count",
        }
        by_state: dict[str, int] = {}
        for status in cur.values_list("status", flat=True):
            by_state[status] = by_state.get(status, 0) + 1
        panels["estado_documentos"] = [
            {"x": k, "value": v, "drill": f"/m/facturacion/documentos?estado={k}"} for k, v in sorted(by_state.items())
        ]
    if _allowed(ctx, "einvoice.numbering_range.read"):
        today = clock.business_date()
        remaining = [
            service.range_usage(r)["remaining"]
            for r in NumberingRange.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, status=NumberingRange.Status.ACTIVE, valid_to__gte=today
            )
        ]
        kpis["numeracion_restante"] = {
            "value": min(remaining) if remaining else None,
            "unit": "count",
            "empty_reason": None if remaining else "Sin resoluciones vigentes",
        }
    return {
        "module": "facturacion",
        "company_id": str(company_id),
        "period": start.strftime("%Y-%m"),
        "kpis": kpis,
        "panels": panels,
        "simulated": True,
    }
