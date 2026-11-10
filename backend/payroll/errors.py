"""Stable error codes of the payroll module (contracts/errors.yaml, module payroll)."""

from __future__ import annotations

from kernel.errors import DomainError


class PayrollParameterUnknown(DomainError):
    """A legal parameter a formula needs is missing/UNKNOWN for the date (fail closed, C1-9)."""

    code, status, title = "payroll_parameter_unknown", 422, "Payroll parameter unknown (fail closed)"


class PayrollRunLocked(DomainError):
    """The run is past CALCULATED: it cannot be recalculated or edited (posted runs are immutable)."""

    code, status, title = "payroll_run_locked", 409, "Payroll run locked"
