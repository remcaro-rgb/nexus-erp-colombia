"""Employee portal dashboard KPIs (module-registry ``portals › empleados › dashboard``), own data only.

* ``ep_dias_vacaciones`` = accrued − taken − approved pending vacation days (:mod:`hr.vacations`); the annual
  entitlement is a legal parameter — UNKNOWN → ``state: not_available`` (fail closed, never a guess);
* ``ep_ultimo_pago`` = net pay of my last POSTED payslip (``previous`` = the one before); no payslip source →
  ``not_available``; no payslip → ``no_data``;
* ``ep_solicitudes_pendientes`` = count of my vacation + data-update requests in SUBMITTED.

Each KPI is computed only if the portal principal holds its permission. Nothing here aggregates over other
employees (no salary aggregate is reachable from the portal).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from hr.models import DataUpdateRequest, Employee
from iam.policy.engine import get_engine
from kernel import clock
from kernel.errors import ParameterNotVerified, ParameterUnknown, ServiceUnavailable
from kernel.money import q_amount
from timekeeping.models import VACATION, Absence


def _kpi(kid: str, value: Any, unit: str, state: str = "ok", previous: Any = None) -> dict[str, Any]:
    delta = None
    if isinstance(value, Decimal) and isinstance(previous, Decimal):
        delta = str(q_amount(value - previous))
    return {
        "id": kid,
        "value": None if value is None else str(value),
        "previous": None if previous is None else str(previous),
        "delta": delta,
        "unit": unit,
        "state": state,
        "formula_ref": f"module-registry.json#portals/empleados/{kid}",
    }


def employee_kpis(view: Any, emp: Employee) -> dict[str, Any]:
    from hr.vacations import balance

    ctx = view.ctx
    engine = get_engine()
    today = clock.business_date()
    out: list[dict[str, Any]] = []
    if engine.authorize(ctx, "portal.employee_vacation.read").allowed:
        try:
            bal = balance(emp, today)
            out.append(_kpi("ep_dias_vacaciones", bal.available, "days"))
        except (ParameterUnknown, ParameterNotVerified, ServiceUnavailable):
            out.append(_kpi("ep_dias_vacaciones", None, "days", "not_available"))
    if engine.authorize(ctx, "portal.employee_payslip.read").allowed:
        try:
            slips = view.own_payslips(emp) if hasattr(view, "own_payslips") else _own_payslips(view, emp)
            if slips:
                prev = q_amount(slips[1].net_pay) if len(slips) > 1 else None
                out.append(_kpi("ep_ultimo_pago", q_amount(slips[0].net_pay), "COP", previous=prev))
            else:
                out.append(_kpi("ep_ultimo_pago", None, "COP", "no_data"))
        except ServiceUnavailable:
            out.append(_kpi("ep_ultimo_pago", None, "COP", "not_available"))
    if engine.authorize(ctx, "portal.employee_vacation.read").allowed:
        pending = Absence.objects.filter(employee=emp, absence_type=VACATION, status="SUBMITTED").count()
        pending += DataUpdateRequest.objects.filter(employee=emp, review_status="SUBMITTED").count()
        out.append(_kpi("ep_solicitudes_pendientes", pending, "count"))
    now = clock.now()
    return {"portal": "employee", "as_of": today.isoformat(), "kpis": out, "generated_at": now.isoformat()}


def _own_payslips(view: Any, emp: Employee) -> list[Any]:
    from hr.portal.payslips import source

    rows = source().payslips_of(emp.tenant_id, emp.company_id, emp.id)
    return [p for p in rows if p.employee_id == emp.id and p.company_id == emp.company_id]
