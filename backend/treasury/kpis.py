"""KPI provider for ``GET /api/v1/kpis/tesoreria`` (+ ``/panels/flujo_caja``), module-registry ``tesoreria``.

Server-side aggregates, company + period scoped (a ledger period id or ``YYYY-MM``; default the current business
month), policy-filtered: a KPI the principal may not read is absent (never a number computed past the policy).
``saldos_intercompania`` belongs to P16b (consolidation) and is reported ``not_available`` here. No KPI of this
module has a denominator (``zero_denominator: not_applicable``); an empty company answers ``no_data``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from treasury.models import BankAccount, CashBox, Fund, StatementLine, TreasuryVoucher

ZERO = Decimal(0)


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def month_end(day: dt.date) -> dt.date:
    nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def period_bounds(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> tuple[dt.date, dt.date]:
    if period and len(period) == 36:
        from django.apps import apps

        model = apps.get_model("ledger", "Period")
        try:
            row = model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=uuid.UUID(period)).first()
        except ValueError:
            row = None
        if row is None:
            raise ValidationFailed("unknown period", field="period_id", code="filter_invalid")
        return row.start, row.end
    if period:
        try:
            start = dt.date.fromisoformat(f"{period}-01")
        except ValueError:
            raise ValidationFailed(
                "period must be YYYY-MM or a period id", field="period_id", code="filter_invalid"
            ) from None
    else:
        start = month_start(clock.business_date())
    return start, month_end(start)


def _allowed(ctx: TenantContext, action: str) -> bool:
    return services.get(PolicyService).authorize(ctx, action).allowed


def _fmt(value: Decimal | int, unit: str) -> str:
    return f"{value:.2f}" if unit == "COP" else str(value)


def _kpi(kid: str, value: Decimal | int, previous: Decimal | int | None, unit: str, has_data: bool) -> dict[str, Any]:
    return {
        "id": kid,
        "value": _fmt(value, unit),
        "previous": None if previous is None else _fmt(previous, unit),
        "delta": None if previous is None else _fmt(value - previous, unit),
        "unit": unit,
        "state": "ok" if has_data else "no_data",
        "formula_ref": f"module-registry:tesoreria.{kid}",
    }


def _fund_accounts(ctx: TenantContext, company_id: uuid.UUID) -> list[uuid.UUID]:
    base = {"tenant_id": ctx.tenant_id, "company_id": company_id, "status": "ACTIVE"}
    return [
        *BankAccount.objects.filter(**base).values_list("account_id", flat=True),
        *CashBox.objects.filter(**base).values_list("account_id", flat=True),
    ]


def _balance(ctx: TenantContext, company_id: uuid.UUID, accounts: list[uuid.UUID], cut: dt.date) -> Decimal:
    from django.apps import apps

    model = apps.get_model("ledger", "JournalLine")
    row = model.objects.filter(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        account_id__in=accounts,
        entry__book__primary=True,
        posting_date__lte=cut,
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (row["d"] or ZERO) - (row["c"] or ZERO)


def _flow(ctx: TenantContext, company_id: uuid.UUID, vtype: str, start: dt.date, end: dt.date) -> Decimal:
    rows = TreasuryVoucher.objects.filter(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        voucher_type=vtype,
        posted=True,
        reversal_of__isnull=True,
        posting_date__gte=start,
        posting_date__lte=end,
    ).exclude(status=TreasuryVoucher.State.REVERSED)
    total = ZERO
    for amount, fx in rows.values_list("total", "fx_rate"):
        total += amount * fx
    return total


def tesoreria_kpis(
    ctx: TenantContext, company_id: uuid.UUID, period: str | None, compare: bool = True
) -> dict[str, Any]:
    ctx.require_company(company_id)
    start, end = period_bounds(ctx, company_id, period)
    prev_end = start - dt.timedelta(days=1)
    prev_start = month_start(prev_end)
    kpis: list[dict[str, Any]] = []
    if _allowed(ctx, "treasury.kpi.read"):
        accounts = _fund_accounts(ctx, company_id)
        has_funds = bool(accounts)
        kpis.append(
            _kpi(
                "saldo_disponible",
                _balance(ctx, company_id, accounts, end) if has_funds else ZERO,
                (_balance(ctx, company_id, accounts, prev_end) if has_funds else ZERO) if compare else None,
                "COP",
                has_funds,
            )
        )
        for kid, vtype in (("recaudos_periodo", "RC"), ("pagos_periodo", "CE")):
            value = _flow(ctx, company_id, vtype, start, end)
            prev = _flow(ctx, company_id, vtype, prev_start, prev_end) if compare else None
            has = TreasuryVoucher.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, voucher_type=vtype, posted=True
            ).exists()
            kpis.append(_kpi(kid, value, prev, "COP", has))
    if _allowed(ctx, "treasury.reconcile"):
        lines = StatementLine.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)

        def open_at(cut: dt.date) -> int:
            return lines.filter(date__lte=cut, state=StatementLine.State.UNMATCHED).count()

        kpis.append(
            _kpi(
                "partidas_sin_conciliar", open_at(end), open_at(prev_end) if compare else None, "count", lines.exists()
            )
        )
    if _allowed(ctx, "consolidation.read"):
        kpis.append(
            {
                "id": "saldos_intercompania",
                "value": None,
                "previous": None,
                "delta": None,
                "unit": "COP",
                "state": "not_available",
                "formula_ref": "module-registry:tesoreria.saldos_intercompania",
            }
        )
    has_any = Fund.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).exists()
    return {
        "as_of": end.isoformat(),
        "generated_at": clock.now().isoformat(),
        "period": f"{start:%Y-%m}",
        "kpis": kpis,
        "empty": not has_any,
    }


def cash_flow_panel(ctx: TenantContext, company_id: uuid.UUID, period: str | None) -> dict[str, Any]:
    """Line panel ``flujo_caja``: posted receipts vs. disbursements per month (12 months up to the period)."""
    ctx.require_company(company_id)
    _start, end = period_bounds(ctx, company_id, period)
    if not _allowed(ctx, "treasury.kpi.read"):
        return {"state": "not_available", "series": [], "points": []}
    first = month_start(end)
    months = []
    for _ in range(12):
        months.append(first)
        first = month_start(first - dt.timedelta(days=1))
    points = []
    any_data = False
    for m in reversed(months):
        rc = _flow(ctx, company_id, "RC", m, month_end(m))
        ce = _flow(ctx, company_id, "CE", m, month_end(m))
        any_data = any_data or bool(rc or ce)
        points.append({"label": f"{m:%Y-%m}", "values": {"recaudos": f"{rc:.2f}", "pagos": f"{ce:.2f}"}})
    return {
        "state": "ok" if any_data else "no_data",
        "series": [{"id": "recaudos", "label": "Recaudos"}, {"id": "pagos", "label": "Pagos"}],
        "points": points,
    }
