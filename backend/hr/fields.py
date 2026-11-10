"""Field classifications (the contract's ``x-classification``) of every HR / time keeping resource.

Unregistered fields are ``secret`` (never readable). Internal readers see a field only when their role's
``max_classification`` reaches it: salary/bank account are ``confidential`` (payroll roles), health and
pension entities, medical notes and geolocation ``sensitive-pii``; a manager role limited to ``internal``
sees team schedules and absences but no salary (field policy, the same for UI, API, export, AI and MCP).
"""

from __future__ import annotations

from iam.policy import fields as registry

_BASE = {"id": "public", "tenant_id": "internal", "company_id": "internal", "version": "internal", "audit": "internal"}
_DOC = {
    **_BASE,
    "number": "internal",
    "document_date": "internal",
    "posting_date": "internal",
    "status": "internal",
    "currency": "public",
    "total": "internal",
    "notes": "internal",
    "source": "internal",
    "lines": "internal",
}

CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "hr.employee": {
        **_BASE,
        "code": "internal",
        "status": "internal",
        "full_name": "restricted-pii",
        "document": "restricted-pii",
        "email": "restricted-pii",
        "phone": "restricted-pii",
        "address": "restricted-pii",
        "birth_date": "restricted-pii",
        "health_entity": "sensitive-pii",
        "pension_entity": "sensitive-pii",
        "bank_account": "confidential",
        "position_id": "internal",
        "org_unit_id": "internal",
        # write-only consent flags (Ley 1581 / R-DP-01); CCR P13a-2 adds them to the contract
        "data_consent": "restricted-pii",
        "biometric_consent": "sensitive-pii",
    },
    "hr.contract": {
        **_DOC,
        "employee_id": "restricted-pii",
        "contract_type": "internal",
        "start_date": "internal",
        "end_date": "internal",
        "salary": "confidential",
        # payroll inputs (CCR P13a-2 adds them to the contract schema)
        "salary_type": "internal",
        "arl_risk_class": "internal",
        "cost_center_id": "internal",
        "position_id": "internal",
        "exonerated": "internal",
    },
    "hr.position": {**_BASE, "code": "internal", "name": "internal", "status": "internal", "org_unit_id": "internal"},
    "hr.org_unit": {
        **_BASE,
        "code": "internal",
        "name": "internal",
        "status": "internal",
        "parent_id": "internal",
        "manager_employee_id": "internal",
    },
    "hr.data_update": {
        **_BASE,
        "status": "internal",
        "employee_id": "restricted-pii",
        "changes": "restricted-pii",
        "review_status": "internal",
    },
    "timekeeping.clock_event": {
        **_BASE,
        "status": "internal",
        "employee_id": "restricted-pii",
        "event_type": "internal",
        "at": "internal",
        "device": "internal",
        "geo": "sensitive-pii",
        "method": "internal",
    },
    "timekeeping.shift": {
        **_BASE,
        "code": "internal",
        "name": "internal",
        "status": "internal",
        "start_time": "internal",
        "end_time": "internal",
    },
    "timekeeping.timesheet": {
        **_DOC,
        "employee_id": "restricted-pii",
        "period": "internal",
        "hours": "internal",
        "warnings": "internal",
        "parameters": "internal",
    },
    "timekeeping.overtime_request": {**_DOC, "employee_id": "restricted-pii", "hours": "internal"},
    "timekeeping.absence": {
        **_DOC,
        "employee_id": "restricted-pii",
        "absence_type": "internal",
        "start_date": "internal",
        "end_date": "internal",
        "medical_note": "sensitive-pii",
        "days": "internal",
    },
}


def register_all() -> None:
    for resource, fields in CLASSIFICATIONS.items():
        registry.register(resource, fields)
