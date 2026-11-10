"""HR provider of :class:`hr.interfaces.PayrollEmployeeSource` (consumer: payroll, P13b).

Reads contracts in force (``APPROVED``; terminated ``CANCELLED`` contracts while their last day is inside the
period) of payroll contract types (prestación de servicios is never payroll) and the APPROVED timesheet hours
(:mod:`timekeeping.classification` buckets) of the period's months. Must be called inside the caller's
tenancy scope (forced RLS); results are filtered by ``tenant_id`` + ``company_id`` explicitly as well.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Q

from hr.interfaces import PayrollEmployee
from hr.models import PAYROLL_CONTRACT_TYPES, EmploymentContract
from kernel import tenancy
from kernel.errors import TenancyNotBound


def _months(start: dt.date, end: dt.date) -> list[str]:
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(f"{cur.year:04d}-{cur.month:02d}")
        cur = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


class DbPayrollEmployeeSource:
    """Implements ``PayrollEmployeeSource`` over ``hr_contract`` + ``timekeeping_timesheet``."""

    def __init__(self, employee_factory: Any = PayrollEmployee) -> None:
        #: payroll's own PayrollEmployee dataclass when payroll is installed (same fields)
        self.employee_factory = employee_factory

    def employees_for_period(
        self, company_id: uuid.UUID, start: dt.date, end: dt.date, employee_ids: list[uuid.UUID] | None = None
    ) -> list[Any]:
        from timekeeping.models import Timesheet

        ctx = tenancy.current()
        if ctx is None:
            raise TenancyNotBound("the HR directory needs a bound tenant context")
        qs = (
            EmploymentContract.objects.filter(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                contract_type__in=PAYROLL_CONTRACT_TYPES,
                status__in=("APPROVED", "CANCELLED"),
                start_date__lte=end,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
            .filter(Q(termination_date__isnull=True) | Q(termination_date__gte=start))
            .select_related("employee")
            .order_by("employee__code", "start_date")
        )
        if employee_ids is not None:
            qs = qs.filter(employee_id__in=employee_ids)
        months = _months(start, end)
        hours: dict[uuid.UUID, dict[str, Decimal]] = {}
        sheets = Timesheet.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, status="APPROVED", period__in=months
        )
        for sheet in sheets:
            bucket = hours.setdefault(sheet.employee_id, {})
            for key, value in (sheet.hours or {}).items():
                bucket[key] = bucket.get(key, Decimal(0)) + Decimal(str(value))
        out = []
        for c in qs:
            last = min(d for d in (c.end_date, c.termination_date) if d) if (c.end_date or c.termination_date) else None
            out.append(
                self.employee_factory(
                    employee_id=c.employee_id,
                    company_id=c.company_id,
                    full_name=c.employee.full_name,
                    contract_id=c.id,
                    contract_type=c.contract_type,
                    salary=Decimal(c.salary),
                    salary_type=c.salary_type,
                    start_date=c.start_date,
                    end_date=last,
                    arl_risk_class=c.arl_risk_class,
                    cost_center_id=c.cost_center_id,
                    exonerated=c.exonerated,
                    days_worked=None,
                    hours=dict(hours.get(c.employee_id, {})),
                )
            )
        return out
