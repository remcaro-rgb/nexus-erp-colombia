"""Employee certificates (certificado laboral; certificado de ingresos y retenciones — non-certified dev format).

Certificates are derived documents, not stored rows: their ids are deterministic (UUIDv5 of tenant, company,
employee, type and year), so only the token's own employee can ever produce a matching id (a foreign id is a
404 ``portal_document_not_owned``). Available:

* ``LABORAL`` for the current year when the employee has a contract;
* ``INGRESOS_RETENCIONES`` for every year with POSTED payslips (totals of the employee's own payslips only).

Every rendered PDF carries the watermark :data:`WATERMARK` and the status of each legal parameter involved
(the payslips' parameter snapshots and ``uvt`` of the year), e.g. ``smmlv=VERIFIED-PRIMARY (FIXTURE — NOT
LEGALLY VERIFIED)``; the official formato 220 is EXTERNAL and never claimed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from hr import services
from hr.interfaces import EmployeePayslipView
from hr.models import Employee
from kernel.errors import ParameterNotVerified, ParameterUnknown, ServiceUnavailable
from kernel.money import q_amount

NAMESPACE = uuid.UUID("6d1f3c9e-2b9a-4e43-9a0e-5f0c9e1a13a0")
WATERMARK = "SINTÉTICO — NO LEGALMENTE VERIFICADO (formato de desarrollo, no certificado)"
TYPES = ("LABORAL", "INGRESOS_RETENCIONES")


@dataclass(frozen=True, slots=True)
class Certificate:
    id: uuid.UUID
    tenant_id: uuid.UUID
    employee_id: uuid.UUID
    certificate_type: str
    year: int


def certificate_id(emp: Employee, kind: str, year: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{emp.tenant_id}:{emp.company_id}:{emp.id}:{kind}:{year}")


def available(emp: Employee, payslips: list[EmployeePayslipView], today: dt.date) -> list[Certificate]:
    out = []
    if services.first_contract(emp) is not None:
        out.append(Certificate(certificate_id(emp, "LABORAL", today.year), emp.tenant_id, emp.id, "LABORAL", today.year))
    for year in sorted({p.period_end.year for p in payslips}, reverse=True):
        out.append(
            Certificate(certificate_id(emp, "INGRESOS_RETENCIONES", year), emp.tenant_id, emp.id, "INGRESOS_RETENCIONES", year)
        )
    return out


def _label(snapshot: dict[str, Any]) -> str:
    fixture = " (FIXTURE — NOT LEGALLY VERIFIED)" if snapshot.get("synthetic") or not snapshot.get("legal_review_stamp") else ""
    return f"{snapshot.get('code')}={snapshot.get('status')}{fixture}"


def _parameter_labels(code: str, on: dt.date, company_id: uuid.UUID) -> str:
    try:
        return _label(services.parameter(code, on, company_id).snapshot())
    except (ParameterUnknown, ParameterNotVerified, ServiceUnavailable):
        return f"{code}=UNKNOWN (sin valor vigente; no se usa en cálculos)"


def render_data(
    cert: Certificate, emp: Employee, company_name: str, payslips: list[EmployeePayslipView], today: dt.date
) -> tuple[str, dict[str, Any]]:
    """(template code, data) for the DocumentRenderer; only the employee's own data."""
    labels: list[str] = []
    data: dict[str, Any] = {
        "certificate_type": cert.certificate_type,
        "year": cert.year,
        "company": company_name,
        "employee": emp.full_name,
        "document": f"{emp.document_type} {emp.document_number}",
        "issued_on": today.isoformat(),
        "legal_notice": WATERMARK,
    }
    if cert.certificate_type == "LABORAL":
        contract = services.contract_in_force(emp, today) or services.first_contract(emp)
        if contract is not None:
            data.update(
                position=emp.position.name if emp.position_id and emp.position else "",
                contract_type=contract.contract_type,
                start_date=contract.start_date.isoformat(),
                end_date=(contract.termination_date or contract.end_date or "vigente").__str__(),
                salary=str(q_amount(contract.salary)),
            )
        labels.append(_parameter_labels("smmlv", today, emp.company_id))
        template = "hr.certificado_laboral"
    else:
        own = [p for p in payslips if p.period_end.year == cert.year and p.employee_id == emp.id]
        data.update(
            payslips=len(own),
            total_earnings=str(q_amount(sum((p.earnings for p in own), Decimal(0)))),
            total_deductions=str(q_amount(sum((p.deductions for p in own), Decimal(0)))),
            total_net=str(q_amount(sum((p.net_pay for p in own), Decimal(0)))),
        )
        seen: dict[str, str] = {}
        for p in own:
            for snap in p.parameters:
                if isinstance(snap, dict) and snap.get("code"):
                    seen[str(snap["code"])] = _label(snap)
        labels.extend(seen[k] for k in sorted(seen))
        labels.append(_parameter_labels("uvt", dt.date(cert.year, 12, 31), emp.company_id))
        template = "hr.certificado_ingresos_retenciones"
    data["parameter_status"] = " · ".join(labels)
    return template, data


def watermark(data: dict[str, Any]) -> str:
    return f"{WATERMARK} · Parámetros: {data['parameter_status']}"
