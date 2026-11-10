"""Payslips for the employee portal.

:class:`PayrollTablesPayslipSource` implements :class:`hr.interfaces.EmployeePayslipSource` read-only over the
payroll tables of P13b (``payroll.Payslip`` / ``PayrollRun`` / ``PayrollPeriod``), resolved through the app
registry so HR has no import-time dependency on payroll. Only POSTED payslips are visible. It runs inside the
caller's tenancy scope (forced RLS) and filters by tenant + company explicitly; the portal view additionally
checks that the payslip belongs to the token's employee (IDOR defence in depth).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.apps import apps

from hr.interfaces import EmployeePayslipSource, EmployeePayslipView
from kernel import services


def source() -> EmployeePayslipSource:
    return services.get(EmployeePayslipSource)


def _view(slip: Any) -> EmployeePayslipView:
    period = slip.run.period
    return EmployeePayslipView(
        id=slip.id,
        tenant_id=slip.tenant_id,
        company_id=slip.company_id,
        employee_id=slip.employee_id,
        period=str(period.code),
        period_end=period.end_date,
        net_pay=Decimal(slip.net_pay),
        earnings=Decimal(slip.earnings),
        deductions=Decimal(slip.deductions),
        status="POSTED",
        lines=tuple(
            {"concept": ln.concept_code, "concept_type": ln.concept_type, "amount": str(ln.amount)}
            for ln in slip.lines.all().order_by("sequence")
        ),
        legal_notice=str(slip.legal_notice or ""),
        parameters=tuple((slip.parameter_snapshot or {}).values()),
    )


class PayrollTablesPayslipSource:
    def _qs(self, tenant_id: uuid.UUID, company_id: uuid.UUID) -> Any:
        model = apps.get_model("payroll", "Payslip")
        return model.objects.filter(tenant_id=tenant_id, company_id=company_id, posted=True).select_related(
            "run__period"
        )

    def payslips_of(
        self, tenant_id: uuid.UUID, company_id: uuid.UUID, employee_id: uuid.UUID
    ) -> list[EmployeePayslipView]:
        rows = self._qs(tenant_id, company_id).filter(employee_id=employee_id).order_by("-run__period__end_date", "-id")
        return [_view(s) for s in rows]

    def payslip(self, tenant_id: uuid.UUID, company_id: uuid.UUID, payslip_id: uuid.UUID) -> EmployeePayslipView | None:
        slip = self._qs(tenant_id, company_id).filter(pk=payslip_id).first()
        return _view(slip) if slip is not None else None
