"""The HR interface payroll consumes (consumer side; provider P13a).

P13a publishes the HR interface in ``kernel.interfaces`` (CCR P13b-1 asks for exactly this
shape). Until it is registered, payroll resolves :class:`PayrollEmployeeSource` from
``kernel.services`` and tests register :class:`payroll.testing.fakes.FakeEmployeeSource`
(fake allowed until G2). A missing provider fails closed (``service_unavailable``).

Every value here is restricted personal data (Ley 1581): it is used only inside the
calculation and is never returned by an API without the payroll field policy.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable


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
    exonerated: bool = False  # ET 114-1 exoneration applies to the employer
    days_worked: Decimal | None = None  # None → derived from the period and the contract dates
    hours: dict[str, Decimal] = field(default_factory=dict)  # timekeeping hours by type (approved)


@runtime_checkable
class PayrollEmployeeSource(Protocol):
    def employees_for_period(
        self, company_id: uuid.UUID, start: dt.date, end: dt.date, employee_ids: list[uuid.UUID] | None = None
    ) -> list[PayrollEmployee]:
        """Employees with a payroll contract overlapping ``start..end`` (company-scoped)."""
        ...
