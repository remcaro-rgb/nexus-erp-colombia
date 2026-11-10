"""Internal HR API (``/api/v1/hr/*``, contract ``contracts/openapi/modules/hr.yaml``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.urls import URLPattern, path
from rest_framework.request import Request
from rest_framework.response import Response

from hr import crypto, services
from hr.api.crud import Action, Spec, audit_dict, company_of, money, routes
from hr.models import DataUpdateRequest, Employee, EmploymentContract, OrgUnit, Position
from iam.api.base import SecuredView
from iam.policy.engine import get_engine
from kernel.errors import ValidationFailed
from kernel.uow import UnitOfWork


def _id(v: Any) -> str | None:
    return str(v) if v else None


def employee_dict(e: Employee) -> dict[str, Any]:
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "company_id": e.company_id,
        "code": e.code,
        "status": e.status,
        "full_name": e.full_name,
        "document": {"document_type": e.document_type, "number": e.document_number, "check_digit": e.check_digit},
        "email": e.email,
        "phone": e.phone,
        "address": e.address,
        "birth_date": e.birth_date,
        "health_entity": e.health_entity,
        "pension_entity": e.pension_entity,
        "bank_account": crypto.decrypt(e.bank_account_enc),
        "position_id": _id(e.position_id),
        "org_unit_id": _id(e.org_unit_id),
        "version": e.version,
        "audit": audit_dict(e),
    }


def contract_dict(c: EmploymentContract) -> dict[str, Any]:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "company_id": c.company_id,
        "number": c.number,
        "document_date": c.document_date,
        "posting_date": c.posting_date,
        "status": c.status,
        "currency": c.currency,
        "total": None,  # a contract has no document total (the salary is confidential, never echoed)
        "notes": c.notes,
        "source": None,
        "employee_id": str(c.employee_id),
        "contract_type": c.contract_type,
        "start_date": c.start_date,
        "end_date": c.termination_date or c.end_date,
        "salary": money(c.salary),
        "lines": [],
        "salary_type": c.salary_type,
        "arl_risk_class": c.arl_risk_class,
        "cost_center_id": _id(c.cost_center_id),
        "position_id": _id(c.position_id),
        "exonerated": c.exonerated,
        "version": c.version,
        "audit": audit_dict(c),
    }


def position_dict(p: Position) -> dict[str, Any]:
    return {
        "id": p.id, "tenant_id": p.tenant_id, "company_id": p.company_id, "code": p.code, "name": p.name,
        "status": p.status, "org_unit_id": _id(p.org_unit_id), "version": p.version, "audit": audit_dict(p),
    }  # fmt: skip


def org_unit_dict(u: OrgUnit) -> dict[str, Any]:
    return {
        "id": u.id, "tenant_id": u.tenant_id, "company_id": u.company_id, "code": u.code, "name": u.name,
        "status": u.status, "parent_id": _id(u.parent_id), "manager_employee_id": _id(u.manager_employee_id),
        "version": u.version, "audit": audit_dict(u),
    }  # fmt: skip


def data_update_dict(r: DataUpdateRequest) -> dict[str, Any]:
    return {
        "id": r.id, "tenant_id": r.tenant_id, "company_id": r.company_id, "status": r.review_status,
        "employee_id": str(r.employee_id), "changes": r.changes, "review_status": r.review_status,
        "version": r.version, "audit": audit_dict(r),
    }  # fmt: skip


MASTER_FILTERS = {"code": "code", "name": "name", "status": "status"}

EMPLOYEES = Spec(
    resource="hr.employee",
    url="api/v1/hr/employees",
    model=Employee,
    to_dict=employee_dict,
    create=services.create_employee,
    update=services.update_employee,
    delete=services.archive,
    input_fields=services.EMPLOYEE_FIELDS,
    filters={"code": "code", "status": "status", "position_id": "position_id", "org_unit_id": "org_unit_id"},
    sorts=("code", "status"),
    search=("code", "full_name"),
)
CONTRACTS = Spec(
    resource="hr.contract",
    url="api/v1/hr/contracts",
    model=EmploymentContract,
    to_dict=contract_dict,
    create=services.create_contract,
    update=services.update_contract,
    delete=services.delete_contract,
    input_fields=services.CONTRACT_FIELDS,
    filters={"status": "status", "contract_type": "contract_type", "employee_id": "employee_id"},
    sorts=("number", "start_date", "status"),
    search=("number",),
    actions={
        "amend": Action("hr.contract.update", services.amend_contract),
        "terminate": Action("hr.contract.void", services.terminate_contract),
    },
)
POSITIONS = Spec(
    resource="hr.position",
    url="api/v1/hr/positions",
    model=Position,
    to_dict=position_dict,
    create=services.create_position,
    update=services.update_position,
    delete=services.archive,
    input_fields=("code", "name", "org_unit_id"),
    filters={**MASTER_FILTERS, "org_unit_id": "org_unit_id"},
    sorts=("code", "name"),
    search=("code", "name"),
)
ORG_UNITS = Spec(
    resource="hr.org_unit",
    url="api/v1/hr/org-units",
    model=OrgUnit,
    to_dict=org_unit_dict,
    create=services.create_org_unit,
    update=services.update_org_unit,
    delete=services.archive,
    input_fields=("code", "name", "parent_id", "manager_employee_id"),
    filters={**MASTER_FILTERS, "parent_id": "parent_id"},
    sorts=("code", "name"),
    search=("code", "name"),
)


def _review(approve: bool) -> Any:
    def fn(uow: UnitOfWork, obj: Any, payload: Mapping[str, Any]) -> None:
        services.review_data_update(uow, obj, approve, str(payload.get("reason") or ""))

    return fn


#: HR review of portal data updates (CCR P13a-2: not yet in the frozen contract)
DATA_UPDATES = Spec(
    resource="hr.data_update",
    url="api/v1/hr/data-updates",
    model=DataUpdateRequest,
    to_dict=data_update_dict,
    filters={"review_status": "review_status", "employee_id": "employee_id"},
    sorts=("review_status",),
    actions={
        "approve": Action("hr.employee.update", _review(True)),
        "reject": Action("hr.employee.update", _review(False)),
    },
)


class EmployeesBulkView(SecuredView):
    resource_type = "hr.employee"
    permissions = {"POST": "hr.employee.create"}

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return employee_dict(obj)

    def post(self, request: Request) -> Response:
        items = request.data.get("items") if isinstance(request.data, Mapping) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValidationFailed("items must hold 1–1000 employees", fields=["items"])
        if request.data.get("all_or_nothing", True) is not True:
            raise ValidationFailed("only all_or_nothing imports are supported", fields=["all_or_nothing"])
        for item in items:
            if isinstance(item, Mapping):
                self.check_writable(item, services.EMPLOYEE_FIELDS)
        company = company_of(self)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            get_engine().require(self.ctx, "hr.employee.create", {"company_id": company})
            created = services.bulk_create_employees(uow, company, items)
            return {"items": [{"index": i, "status": 201, "id": str(e.id)} for i, e in enumerate(created)]}, 200

        return self.mutate(request, run, scope="hr.employee.bulk")


def internal_routes() -> list[URLPattern]:
    return [
        path("api/v1/hr/employees:bulk", EmployeesBulkView.as_view()),
        *routes(EMPLOYEES),
        *routes(CONTRACTS),
        *routes(POSITIONS),
        *routes(ORG_UNITS),
        *routes(DATA_UPDATES),
    ]
