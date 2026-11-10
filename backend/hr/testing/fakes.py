"""Typed fakes behind the interfaces HR consumes (fake allowed until G2; real wiring at the gate).

* :class:`FakePayslipSource` implements :class:`hr.interfaces.EmployeePayslipSource` over an in-memory list of
  synthetic POSTED payslips (P13b provides the real tables).
* :func:`legal_parameters` — labelled synthetic parameter values (NOT LEGALLY VERIFIED) for the HR / time keeping
  codes; any code not listed is UNKNOWN (fail closed).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from hr.interfaces import EmployeePayslipView
from kernel.parameters import ParameterStatus, ParameterValue


@dataclass
class FakePayslipSource:
    slips: list[EmployeePayslipView] = field(default_factory=list)

    def add(self, **kwargs: object) -> EmployeePayslipView:
        slip = EmployeePayslipView(**kwargs)  # type: ignore[arg-type]  # test helper: fields validated by the dataclass
        self.slips.append(slip)
        return slip

    def payslips_of(
        self, tenant_id: uuid.UUID, company_id: uuid.UUID, employee_id: uuid.UUID
    ) -> list[EmployeePayslipView]:
        rows = [
            s for s in self.slips if (s.tenant_id, s.company_id, s.employee_id) == (tenant_id, company_id, employee_id)
        ]
        return sorted(rows, key=lambda s: s.period_end, reverse=True)

    def payslip(self, tenant_id: uuid.UUID, company_id: uuid.UUID, payslip_id: uuid.UUID) -> EmployeePayslipView | None:
        return next(
            (s for s in self.slips if (s.tenant_id, s.company_id, s.id) == (tenant_id, company_id, payslip_id)), None
        )


def _syn(
    code: str,
    value: object,
    status: ParameterStatus,
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
) -> ParameterValue:
    return ParameterValue(code, value, status, "SYNTHETIC-FIXTURE", valid_from, valid_to, synthetic=True)


def legal_parameters() -> dict[str, ParameterValue]:
    """Synthetic fixture values (labelled; never the official figures)."""
    vs = ParameterStatus.VERIFIED_SECONDARY
    return {
        "hr.vacaciones.dias_habiles_anio": _syn("hr.vacaciones.dias_habiles_anio", Decimal(15), vs),
        "smmlv": _syn("smmlv", Decimal("1000000"), ParameterStatus.VERIFIED_PRIMARY),
        "uvt": _syn("uvt", Decimal("50000"), ParameterStatus.VERIFIED_PRIMARY),
        "hr.salario_integral.min_smmlv": _syn("hr.salario_integral.min_smmlv", Decimal(13), vs),
        "hr.jornada.nocturna_inicio": _syn("hr.jornada.nocturna_inicio", "19:00", vs),
        "hr.jornada.nocturna_fin": _syn("hr.jornada.nocturna_fin", "06:00", vs),
        "hr.jornada.horas_diarias_ordinarias": _syn("hr.jornada.horas_diarias_ordinarias", Decimal(8), vs),
        "hr.jornada.horas_extra_max_dia": _syn("hr.jornada.horas_extra_max_dia", Decimal(2), vs),
    }
