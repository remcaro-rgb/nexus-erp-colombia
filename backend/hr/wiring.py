"""Service registration at app ready (kernel.services).

* :class:`hr.interfaces.PayrollEmployeeSource` → :class:`hr.directory.DbPayrollEmployeeSource`; also registered
  against ``payroll.interfaces.PayrollEmployeeSource`` when P13b's payroll app is installed (CCR P13a-1 / P13b-1).
* :class:`hr.interfaces.EmployeePayslipSource` → :class:`hr.portal.payslips.PayrollTablesPayslipSource` when the
  payroll app is installed (read-only adapter over P13b's tables); otherwise the portal fails closed.

Never replaces an implementation registered earlier (tests register their fakes first).
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from django.apps import apps

from hr import fields
from kernel import services


def _payroll_interfaces() -> Any | None:
    if not apps.is_installed("payroll") or importlib.util.find_spec("payroll.interfaces") is None:
        return None
    return importlib.import_module("payroll.interfaces")


def register_services() -> None:
    from hr.directory import DbPayrollEmployeeSource
    from hr.interfaces import EmployeePayslipSource, PayrollEmployeeSource

    fields.register_all()
    if services.optional(PayrollEmployeeSource) is None:
        provider: PayrollEmployeeSource = DbPayrollEmployeeSource()
        services.register(PayrollEmployeeSource, provider)
    payroll_ifaces = _payroll_interfaces()
    if payroll_ifaces is not None:
        protocol = getattr(payroll_ifaces, "PayrollEmployeeSource", None)
        factory = getattr(payroll_ifaces, "PayrollEmployee", None)
        if protocol is not None and factory is not None and services.optional(protocol) is None:
            services.register(protocol, DbPayrollEmployeeSource(factory))
        from hr.portal.payslips import PayrollTablesPayslipSource

        if services.optional(EmployeePayslipSource) is None:
            payslip_source: EmployeePayslipSource = PayrollTablesPayslipSource()
            services.register(EmployeePayslipSource, payslip_source)
