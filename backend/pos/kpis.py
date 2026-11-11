"""KPI provider for ``GET /api/v1/kpis/pos`` (module-registry entry ``pos``) and the ``ventas_por_hora`` panel.

Server-side aggregates, company + period scoped, policy-filtered: a KPI the user may not read is absent; row policy
(own sessions for cashiers) applies to every aggregate. A zero denominator returns ``None`` with ``empty_reason``
(the UI shows '—' + 'Sin base para calcular', never 0, ∞ or NaN). Navigation codes resolve through P04
(``pos.read`` → any ``pos.*.read``; ``pos.kpi.read`` → catalog code ``pos.kpi.read``; ``pos.supervise``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Count, Sum
from django.db.models.functions import Abs

from kernel import clock
from kernel.errors import ValidationFailed
from kernel.tenancy import TenantContext
from pos.models import CashSession, Ticket
from pos.services.common import allowed, visible

BOGOTA = ZoneInfo("America/Bogota")


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


def _delta(cur: Decimal | None, prev: Decimal | None) -> str | None:
    if cur is None or prev is None or prev == 0:
        return None
    return str(((cur - prev) * 100 / abs(prev)).quantize(Decimal("0.1")))


def _kpi(kpi_id: str, unit: str, cur: Decimal | None, prev: Decimal | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": kpi_id,
        "value": None if cur is None else str(cur),
        "previous": None if prev is None else str(prev),
        "delta": _delta(cur, prev),
        "unit": unit,
        "state": "ok" if cur is not None else "no_data",
        "formula_ref": f"module-registry:pos.{kpi_id}",
    }
    if cur is None:
        out["empty_reason"] = "Sin base para calcular"
    return out


def _can(ctx: TenantContext, nav: str) -> bool:
    if nav == "pos.read":
        return any(allowed(ctx, c) for c in ("pos.ticket.read", "pos.session.read", "pos.config.read"))
    if nav == "pos.supervise":
        return allowed(ctx, "pos.session.update") and allowed(ctx, "pos.z_report.read")
    return allowed(ctx, nav)


def _tickets(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    qs = Ticket.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status=Ticket.Status.POSTED)
    return visible(ctx, "pos.ticket.read", qs)


def _sales(qs: Any) -> tuple[Decimal, int]:
    agg = qs.aggregate(s=Sum("subtotal"), n=Count("id"))
    return Decimal(agg["s"] or 0).quantize(Decimal("0.01")), int(agg["n"] or 0)


def pos_kpis(ctx: TenantContext, company_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = period_bounds(period)
    prev_start = (start - dt.timedelta(days=1)).replace(day=1)
    kpis: list[dict[str, Any]] = []
    can_read = _can(ctx, "pos.read") and allowed(ctx, "pos.ticket.read")
    if can_read or _can(ctx, "pos.kpi.read"):
        base = _tickets(ctx, company_id) if allowed(ctx, "pos.ticket.read") else Ticket.objects.none()
        cur_s, cur_n = _sales(base.filter(document_date__gte=start, document_date__lt=end))
        prev_s, prev_n = _sales(base.filter(document_date__gte=prev_start, document_date__lt=start))
        if _can(ctx, "pos.kpi.read"):
            kpis.append(_kpi("ventas_pos", "COP", cur_s, prev_s))
        if can_read:
            kpis.append(_kpi("tiquetes", "count", Decimal(cur_n), Decimal(prev_n)))
        if _can(ctx, "pos.kpi.read"):
            avg = None if cur_n == 0 else (cur_s / cur_n).quantize(Decimal("0.01"))
            prev_avg = None if prev_n == 0 else (prev_s / prev_n).quantize(Decimal("0.01"))
            kpis.append(_kpi("ticket_promedio", "COP", avg, prev_avg))
    if _can(ctx, "pos.read") and allowed(ctx, "pos.session.read"):
        sess = visible(
            ctx, "pos.session.read", CashSession.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        )
        kpis.append(_kpi("turnos_abiertos", "count", Decimal(sess.exclude(status=CashSession.Status.CLOSED).count())))
        if _can(ctx, "pos.supervise"):
            closed = sess.filter(status=CashSession.Status.CLOSED)

            def diffs(a: dt.date, b: dt.date) -> Decimal:
                total = closed.filter(business_date__gte=a, business_date__lt=b).aggregate(s=Sum(Abs("difference")))[
                    "s"
                ]
                return Decimal(total or 0).quantize(Decimal("0.01"))

            kpis.append(_kpi("diferencias_arqueo", "COP", diffs(start, end), diffs(prev_start, start)))
    return {
        "as_of": clock.now().isoformat(),
        "generated_at": clock.now().isoformat(),
        "kpis": kpis,
        "period": start.strftime("%Y-%m"),
    }


def sales_by_hour(ctx: TenantContext, company_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    ctx.require_company(company_id)
    if not (_can(ctx, "pos.kpi.read") and allowed(ctx, "pos.ticket.read")):
        return {"state": "not_available", "series": [], "points": []}
    start, end = period_bounds(period)
    hours: dict[int, Decimal] = {}
    rows = (
        _tickets(ctx, company_id)
        .filter(document_date__gte=start, document_date__lt=end)
        .values_list("posted_at", "subtotal")
    )
    for at, subtotal in rows:
        if at is None:
            continue
        h = at.astimezone(BOGOTA).hour
        hours[h] = hours.get(h, Decimal(0)) + Decimal(subtotal)
    points = [
        {
            "label": f"{h:02d}:00",
            "values": {"ventas": f"{v.quantize(Decimal('0.01')):f}"},
            "drill": f"/m/pos/tiquetes?hora={h:02d}",
        }
        for h, v in sorted(hours.items())
    ]
    return {
        "state": "ok" if points else "no_data",
        "series": [{"id": "ventas", "label": "Ventas sin impuestos (COP)"}],
        "points": points,
        "period": start.strftime("%Y-%m"),
    }
