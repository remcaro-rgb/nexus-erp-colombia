"""KPI provider for ``GET /api/v1/kpis/aprobaciones`` (module-registry entry ``aprobaciones``).

Server-side aggregates, company + period scoped, policy-filtered. A KPI the
user may not read is absent; a zero denominator returns ``None`` (the UI shows
'—' with 'Sin base para calcular'), never 0, ∞ or NaN.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from kernel import clock
from kernel.errors import ValidationFailed
from kernel.tenancy import TenantContext
from workflow.policy import policy


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


def aprobaciones_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    from workflow.approvals.service import DbWorkflowService
    from workflow.models import AuditEvent

    ctx.require_company(company_id)
    start, end = period_bounds(period)
    prev_start = (start - dt.timedelta(days=1)).replace(day=1)
    t0, t1, tp = (clock.local_midnight_utc(d) for d in (start, end, prev_start))
    kpis: dict[str, Any] = {}
    if policy().authorize(ctx, "workflow.approval.read").allowed:
        svc = DbWorkflowService()
        cur, prev = svc.kpis(ctx, company_id, t0, t1), svc.kpis(ctx, company_id, tp, t0)
        kpis["pendientes"] = {"value": cur["pendientes"], "unit": "count"}
        kpis["vencidas_sla"] = {"value": cur["vencidas_sla"], "unit": "count"}
        kpis["tiempo_aprobacion"] = {
            "value": cur["tiempo_aprobacion"], "previous": prev["tiempo_aprobacion"], "unit": "hours",
            "empty_reason": None if cur["tiempo_aprobacion"] is not None else "Sin base para calcular",
        }  # fmt: skip
    if policy().authorize(ctx, "workflow.audit_event.read").allowed:
        base = AuditEvent.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        kpis["eventos_auditoria"] = {
            "value": base.filter(at__gte=t0, at__lt=t1).count(),
            "previous": base.filter(at__gte=tp, at__lt=t0).count(),
            "unit": "count",
        }
    feed = []
    if "eventos_auditoria" in kpis:
        for ev in AuditEvent.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("-seq")[:10]:
            feed.append({"id": ev.id, "at": ev.at, "action": ev.action, "resource_type": ev.resource_type,
                         "drill": f"/m/aprobaciones/auditoria/{ev.id}"})  # fmt: skip
    return {"module": "aprobaciones", "company_id": company_id, "period": start.strftime("%Y-%m"), "kpis": kpis,
            "panels": {"actividad": feed}}  # fmt: skip
