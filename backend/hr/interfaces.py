"""Interfaces HR publishes and consumes (frozen v1 for P13b; CCR P13a-1 moves them to ``kernel.interfaces.hr``).

Provided by HR (consumer: payroll, P13b):

* :class:`PayrollEmployeeSource` / :class:`PayrollEmployee` — exactly the shape P13b froze in
  ``payroll.interfaces`` (CCR P13b-1). HR registers :class:`hr.directory.DbPayrollEmployeeSource` against
  this Protocol **and**, when the payroll app is installed, against ``payroll.interfaces.PayrollEmployeeSource``
  (only if nobody registered one before, so tests keep their fakes).

Consumed by the employee portal (provider: payroll, P13b):

* :class:`EmployeePayslipSource` / :class:`EmployeePayslipView` — POSTED payslips of one employee. HR ships a
  read-only adapter over the payroll tables (:mod:`hr.portal.payslips`); tests use
  :class:`hr.testing.fakes.FakePayslipSource`. A missing provider fails closed (``service_unavailable``).

Every value here is restricted personal data (Ley 1581) used only inside the owning employee's scope or the
payroll calculation.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PayrollEmployee:
    """One employee's payroll-relevant contract data effective in a period."""

    employee_id: uuid.UUID
    company_id: uuid.UUID
    full_name: str
    contract_id: uuid.UUID
    contract_type: str  # INDEFINIDO | FIJO | OBRA_LABOR | APRENDIZAJE (prestación de servicios is not payroll)
    salary: Decimal  # monthly contract salary (COP)
    salary_type: str = "ORDINARIO"  # ORDINARIO | INTEGRAL
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    arl_risk_class: str = "I"
    cost_center_id: uuid.UUID | None = None
    exonerated: bool = False
    days_worked: Decimal | None = None
    hours: dict[str, Decimal] = field(default_factory=dict)  # approved timekeeping hours by bucket


@runtime_checkable
class PayrollEmployeeSource(Protocol):
    def employees_for_period(
        self, company_id: uuid.UUID, start: dt.date, end: dt.date, employee_ids: list[uuid.UUID] | None = None
    ) -> list[PayrollEmployee]:
        """Employees with a payroll contract overlapping ``start..end`` (company-scoped)."""
        ...


@dataclass(frozen=True, slots=True)
class EmployeePayslipView:
    """A POSTED payslip as the employee portal sees it (own data only)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    period: str  # period code, e.g. "2026-10"
    period_end: dt.date
    net_pay: Decimal
    earnings: Decimal = Decimal(0)
    deductions: Decimal = Decimal(0)
    status: str = "POSTED"
    lines: tuple[dict[str, Any], ...] = ()
    legal_notice: str = ""
    parameters: tuple[dict[str, Any], ...] = ()
    version: int = 1


@runtime_checkable
class EmployeePayslipSource(Protocol):
    def payslips_of(
        self, tenant_id: uuid.UUID, company_id: uuid.UUID, employee_id: uuid.UUID
    ) -> list[EmployeePayslipView]:
        """POSTED payslips of one employee, newest period first (called inside the caller's tenancy scope)."""
        ...

    def payslip(self, tenant_id: uuid.UUID, company_id: uuid.UUID, payslip_id: uuid.UUID) -> EmployeePayslipView | None:
        """One POSTED payslip by id (any employee of the company; the portal checks ownership)."""
        ...
