"""HR application services. Every write runs inside the caller's unit of work (one transaction + audit + outbox).

Rules:

* codes unique per company; references (position, org unit, employee) must belong to the same company;
* an employee needs the Ley 1581 processing authorisation (``data_consent``) before personal data is stored
  (``consent_required``); the bank account is encrypted at rest (:mod:`hr.crypto`);
* contracts: one contract in force per employee at a time (``contract_overlap``); an INTEGRAL salary must reach
  ``hr.salario_integral.min_smmlv`` × ``smmlv`` on the start date — both legal parameters come from
  ``kernel.parameters`` (UNKNOWN fails closed); amendments and terminations append :class:`ContractHistory`
  and emit ``hr.contract.changed.v1``; a terminated contract is never deleted.
* data-update requests from the portal: SUBMITTED → APPROVED (changes applied) | REJECTED (reason required);
  the reviewer can never be the requesting employee's own portal account.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Q

from hr import crypto
from hr.errors import ConsentRequired, ContractOverlap, InvalidStateTransition, ReasonRequired
from hr.models import (
    ARL_CLASSES,
    CONTRACT_TYPES,
    SALARY_TYPES,
    ContractHistory,
    DataUpdateRequest,
    Employee,
    EmploymentContract,
    OrgUnit,
    Position,
)
from kernel import clock, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.money import q_amount
from kernel.parameters import ParameterProvider
from kernel.uow import UnitOfWork

EMPLOYEE_FIELDS = (
    "code",
    "full_name",
    "document",
    "email",
    "phone",
    "address",
    "birth_date",
    "health_entity",
    "pension_entity",
    "bank_account",
    "position_id",
    "org_unit_id",
    "data_consent",
    "biometric_consent",
)
CONTRACT_FIELDS = (
    "document_date",
    "posting_date",
    "currency",
    "notes",
    "employee_id",
    "contract_type",
    "start_date",
    "end_date",
    "salary",
    "lines",
    "salary_type",
    "arl_risk_class",
    "cost_center_id",
    "position_id",
    "exonerated",
)
AMENDABLE = ("salary", "salary_type", "position_id", "end_date", "arl_risk_class", "cost_center_id", "exonerated")
#: contact fields an employee may ask to change from the portal (HR approves)
PORTAL_UPDATABLE = ("email", "phone", "address")
_CODE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_DOC_NUMBER = re.compile(r"^[0-9A-Za-z-]{3,20}$")


# --------------------------------------------------------------------------- helpers


def audit(uow: UnitOfWork, action: str, resource: str, obj: Any, changes: dict[str, Any] | None = None) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, resource, obj.id, obj.company_id, changes or {}))


def parse_date(value: Any, name: str, *, required: bool = False) -> dt.date | None:
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{name} is required", fields=[name])
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed(f"{name} must be a date (YYYY-MM-DD)", fields=[name]) from None


def parse_amount(value: Any, name: str) -> Decimal:
    raw = value.get("amount") if isinstance(value, Mapping) else value
    if isinstance(value, Mapping) and value.get("currency", "COP") != "COP":
        raise ValidationFailed(f"{name} must be in COP", fields=[name])
    if isinstance(raw, float) or raw is None:
        raise ValidationFailed(f"{name} must be a decimal string", fields=[name])
    try:
        amount = q_amount(Decimal(str(raw)))
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"{name} must be a decimal string", fields=[name]) from None
    if amount <= 0:
        raise ValidationFailed(f"{name} must be positive", fields=[name])
    return amount


def parse_uuid_field(value: Any, name: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed(f"{name} must be a UUID", fields=[name]) from None


def _same_company(model: Any, pk: uuid.UUID | None, company_id: uuid.UUID, name: str) -> Any:
    if pk is None:
        return None
    obj = model.objects.filter(pk=pk, company_id=company_id).first()
    if obj is None:
        raise ValidationFailed(f"{name} not found in this company", fields=[name])
    return obj


def _check_code(model: Any, company_id: uuid.UUID, code: Any, exclude: uuid.UUID | None = None) -> str:
    if not isinstance(code, str) or not _CODE.match(code):
        raise ValidationFailed("code must be 1–40 letters, digits, '.', '_' or '-'", fields=["code"])
    qs = model.objects.filter(company_id=company_id, code__iexact=code)
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    if qs.exists():
        raise ValidationFailed("code already used in this company", fields=["code"])
    return code


def _text(value: Any, name: str, max_len: int, *, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{name} is required", fields=[name])
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise ValidationFailed(f"{name} must be text up to {max_len} characters", fields=[name])
    return value.strip()


def parameter(code: str, at: dt.date, company_id: uuid.UUID) -> Any:
    """A legal parameter through ``kernel.parameters`` (UNKNOWN / missing provider → fail closed)."""
    return services.get(ParameterProvider).get(code, at, company_id)


# --------------------------------------------------------------------------- org units / positions


def create_org_unit(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> OrgUnit:
    unit = OrgUnit(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=_check_code(OrgUnit, company_id, data.get("code")),
        name=_text(data.get("name"), "name", 200, required=True) or "",
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    _apply_org_unit_refs(unit, data)
    unit.save()
    audit(uow, "hr.org_unit.created", "hr.org_unit", unit)
    return unit


def _apply_org_unit_refs(unit: OrgUnit, data: Mapping[str, Any]) -> None:
    if "parent_id" in data:
        parent = _same_company(OrgUnit, parse_uuid_field(data["parent_id"], "parent_id"), unit.company_id, "parent_id")
        node = parent
        while node is not None:  # no cycles
            if node.pk == unit.pk:
                raise ValidationFailed("an org unit cannot be its own ancestor", fields=["parent_id"])
            node = node.parent
        unit.parent = parent
    if "manager_employee_id" in data:
        mgr = _same_company(
            Employee, parse_uuid_field(data["manager_employee_id"], "manager_employee_id"), unit.company_id, "manager"
        )
        unit.manager_employee_id = mgr.pk if mgr else None


def update_org_unit(uow: UnitOfWork, unit: OrgUnit, data: Mapping[str, Any]) -> None:
    if "code" in data:
        unit.code = _check_code(OrgUnit, unit.company_id, data["code"], exclude=unit.pk)
    if "name" in data:
        unit.name = _text(data["name"], "name", 200, required=True) or ""
    _apply_org_unit_refs(unit, data)
    unit.save_versioned(uow.ctx.principal_id, ["code", "name", "parent", "manager_employee_id"])
    audit(uow, "hr.org_unit.updated", "hr.org_unit", unit, {"fields": sorted(data)})


def archive(uow: UnitOfWork, obj: Any) -> None:
    """DELETE of a master = archive (history keeps its references)."""
    if obj.status == "ARCHIVED":
        raise InvalidStateTransition("already archived")
    obj.status = "ARCHIVED"
    obj.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, f"{obj._meta.db_table.replace('_', '.', 1)}.archived", obj._meta.db_table.replace("_", ".", 1), obj)


def create_position(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Position:
    pos = Position(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=_check_code(Position, company_id, data.get("code")),
        name=_text(data.get("name"), "name", 200, required=True) or "",
        org_unit=_same_company(OrgUnit, parse_uuid_field(data.get("org_unit_id"), "org_unit_id"), company_id, "org_unit_id"),
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    pos.save()
    audit(uow, "hr.position.created", "hr.position", pos)
    return pos


def update_position(uow: UnitOfWork, pos: Position, data: Mapping[str, Any]) -> None:
    if "code" in data:
        pos.code = _check_code(Position, pos.company_id, data["code"], exclude=pos.pk)
    if "name" in data:
        pos.name = _text(data["name"], "name", 200, required=True) or ""
    if "org_unit_id" in data:
        pos.org_unit = _same_company(
            OrgUnit, parse_uuid_field(data["org_unit_id"], "org_unit_id"), pos.company_id, "org_unit_id"
        )
    pos.save_versioned(uow.ctx.principal_id, ["code", "name", "org_unit"])
    audit(uow, "hr.position.updated", "hr.position", pos, {"fields": sorted(data)})


# --------------------------------------------------------------------------- employees


def _apply_employee(emp: Employee, data: Mapping[str, Any]) -> list[str]:
    changed: list[str] = []
    if "full_name" in data:
        emp.full_name = _text(data["full_name"], "full_name", 200, required=True) or ""
        changed.append("full_name")
    if "document" in data:
        doc = data["document"]
        if not isinstance(doc, Mapping) or not doc.get("document_type") or not doc.get("number"):
            raise ValidationFailed("document needs document_type and number", fields=["document"])
        number = str(doc["number"])
        if not _DOC_NUMBER.match(number) or len(str(doc["document_type"])) > 10:
            raise ValidationFailed("invalid document", fields=["document"])
        emp.document_type, emp.document_number = str(doc["document_type"]), number
        emp.check_digit = str(doc["check_digit"]) if doc.get("check_digit") not in (None, "") else None
        changed += ["document_type", "document_number", "check_digit"]
    for name, size in (("email", 254), ("phone", 30), ("address", 255), ("health_entity", 120), ("pension_entity", 120)):
        if name in data:
            setattr(emp, name, _text(data[name], name, size))
            changed.append(name)
    if "email" in data and emp.email and "@" not in emp.email:
        raise ValidationFailed("invalid email", fields=["email"])
    if "birth_date" in data:
        emp.birth_date = parse_date(data["birth_date"], "birth_date")
        changed.append("birth_date")
    if "bank_account" in data:
        emp.bank_account_enc = crypto.encrypt(_text(data["bank_account"], "bank_account", 40))
        changed.append("bank_account_enc")
    if "position_id" in data:
        emp.position = _same_company(
            Position, parse_uuid_field(data["position_id"], "position_id"), emp.company_id, "position_id"
        )
        changed.append("position")
    if "org_unit_id" in data:
        emp.org_unit = _same_company(
            OrgUnit, parse_uuid_field(data["org_unit_id"], "org_unit_id"), emp.company_id, "org_unit_id"
        )
        changed.append("org_unit")
    for flag, attr in (("data_consent", "data_consent_at"), ("biometric_consent", "biometric_consent_at")):
        if flag in data:
            setattr(emp, attr, clock.now() if data[flag] is True else None)
            changed.append(attr)
    return changed


def create_employee(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Employee:
    if data.get("data_consent") is not True:
        raise ConsentRequired("the Ley 1581 processing authorisation (data_consent) is required")
    emp = Employee(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=_check_code(Employee, company_id, data.get("code")),
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    for required in ("full_name", "document"):
        if required not in data:
            raise ValidationFailed(f"{required} is required", fields=[required])
    _apply_employee(emp, data)
    if Employee.objects.filter(
        company_id=company_id, document_type=emp.document_type, document_number=emp.document_number
    ).exists():
        raise ValidationFailed("an employee with this document already exists", fields=["document"])
    emp.save()
    audit(uow, "hr.employee.created", "hr.employee", emp)
    return emp


def update_employee(uow: UnitOfWork, emp: Employee, data: Mapping[str, Any]) -> None:
    if "code" in data:
        emp.code = _check_code(Employee, emp.company_id, data["code"], exclude=emp.pk)
    if data.get("data_consent") is False:
        raise ConsentRequired("withdrawing the processing authorisation is a data subject request (DSR), not an edit")
    changed = _apply_employee(emp, data) + (["code"] if "code" in data else [])
    emp.save_versioned(uow.ctx.principal_id, changed)
    audit(uow, "hr.employee.updated", "hr.employee", emp, {"fields": sorted(data)})


def bulk_create_employees(uow: UnitOfWork, company_id: uuid.UUID, items: list[Any]) -> list[Employee]:
    """All-or-nothing (the caller's unit of work rolls back on the first invalid item)."""
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) - set(EMPLOYEE_FIELDS):
            raise ValidationFailed(f"item {i} is invalid", item=i)
        out.append(create_employee(uow, company_id, item))
    return out


# --------------------------------------------------------------------------- contracts


def _next_number(company_id: uuid.UUID, year: int) -> str:
    n = EmploymentContract.objects.filter(company_id=company_id, number__startswith=f"CT-{year}-").count() + 1
    return f"CT-{year}-{n:05d}"


def _check_integral(company_id: uuid.UUID, salary: Decimal, on: dt.date) -> dict[str, Any]:
    smmlv = parameter("smmlv", on, company_id)
    times = parameter("hr.salario_integral.min_smmlv", on, company_id)
    minimum = q_amount(Decimal(str(smmlv.value)) * Decimal(str(times.value)))
    if salary < minimum:
        raise ValidationFailed(
            "an integral salary must reach the legal minimum (hr.salario_integral.min_smmlv × smmlv)",
            fields=["salary"],
        )
    return {"smmlv": smmlv.snapshot(), "hr.salario_integral.min_smmlv": times.snapshot()}


def _check_overlap(contract: EmploymentContract) -> None:
    end = contract.termination_date or contract.end_date or dt.date.max
    clash = (
        EmploymentContract.objects.filter(employee_id=contract.employee_id, status="APPROVED", start_date__lte=end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=contract.start_date))
        .exclude(pk=contract.pk)
    )
    if clash.exists():
        raise ContractOverlap("the employee already has a contract in force for these dates")


def _apply_contract(c: EmploymentContract, data: Mapping[str, Any]) -> None:
    if "contract_type" in data:
        if data["contract_type"] not in CONTRACT_TYPES:
            raise ValidationFailed(f"contract_type must be one of {', '.join(CONTRACT_TYPES)}", fields=["contract_type"])
        c.contract_type = data["contract_type"]
    if "salary_type" in data:
        if data["salary_type"] not in SALARY_TYPES:
            raise ValidationFailed("salary_type must be ORDINARIO or INTEGRAL", fields=["salary_type"])
        c.salary_type = data["salary_type"]
    if "arl_risk_class" in data:
        if data["arl_risk_class"] not in ARL_CLASSES:
            raise ValidationFailed("arl_risk_class must be I–V", fields=["arl_risk_class"])
        c.arl_risk_class = data["arl_risk_class"]
    if "salary" in data:
        c.salary = parse_amount(data["salary"], "salary")
    for name in ("document_date", "posting_date", "start_date", "end_date"):
        if name in data:
            setattr(c, name, parse_date(data[name], name, required=name in ("document_date", "start_date")))
    if "notes" in data:
        c.notes = _text(data["notes"], "notes", 2000) or ""
    if "currency" in data and data["currency"] != "COP":
        raise ValidationFailed("contracts are in COP", fields=["currency"])
    if "cost_center_id" in data:
        c.cost_center_id = parse_uuid_field(data["cost_center_id"], "cost_center_id")
    if "position_id" in data:
        c.position = _same_company(
            Position, parse_uuid_field(data["position_id"], "position_id"), c.company_id, "position_id"
        )
    if "exonerated" in data:
        c.exonerated = data["exonerated"] is True
    if c.end_date and c.end_date < c.start_date:
        raise ValidationFailed("end_date is before start_date", fields=["end_date"])
    if c.contract_type == "FIJO" and c.end_date is None:
        raise ValidationFailed("a fixed-term contract needs end_date", fields=["end_date"])


def _history(uow: UnitOfWork, c: EmploymentContract, change: str, on: dt.date, changes: dict[str, Any], reason: str = "") -> None:
    ContractHistory.objects.create(
        tenant_id=c.tenant_id,
        company_id=c.company_id,
        contract=c,
        change=change,
        effective_date=on,
        changes=changes,
        reason=reason,
        by=uow.ctx.principal_id,
    )
    uow.emit(
        "hr.contract.changed.v1",
        {"contract_id": str(c.id), "employee_id": str(c.employee_id), "change": change},
        company_id=c.company_id,
    )


def create_contract(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> EmploymentContract:
    for required in ("document_date", "employee_id", "contract_type", "start_date", "salary"):
        if data.get(required) in (None, ""):
            raise ValidationFailed(f"{required} is required", fields=[required])
    employee = _same_company(Employee, parse_uuid_field(data["employee_id"], "employee_id"), company_id, "employee_id")
    if employee.status != "ACTIVE":
        raise ValidationFailed("the employee is archived", fields=["employee_id"])
    c = EmploymentContract(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, employee=employee,
        created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    _apply_contract(c, data)
    snapshots = _check_integral(company_id, c.salary, c.start_date) if c.salary_type == "INTEGRAL" else {}
    if c.is_payroll:
        _check_overlap(c)
    c.number = _next_number(company_id, c.document_date.year)
    c.save()
    _history(uow, c, "created", c.start_date, {"salary": str(c.salary), "parameters": snapshots})
    audit(uow, "hr.contract.created", "hr.contract", c)
    return c


def update_contract(uow: UnitOfWork, c: EmploymentContract, data: Mapping[str, Any]) -> None:
    """PATCH corrects data entry errors before any payroll used the contract; salary changes go through :amend."""
    if c.status != "APPROVED":
        raise InvalidStateTransition("only a contract in force can be corrected")
    if "salary" in data or "employee_id" in data:
        raise ValidationFailed("salary changes use :amend; the employee of a contract never changes", fields=["salary"])
    _apply_contract(c, data)
    if c.is_payroll:
        _check_overlap(c)
    c.save_versioned(uow.ctx.principal_id, [f.name for f in c._meta.concrete_fields if f.name not in ("id", "version")])
    audit(uow, "hr.contract.updated", "hr.contract", c, {"fields": sorted(data)})


def delete_contract(uow: UnitOfWork, c: EmploymentContract) -> None:
    raise InvalidStateTransition("contracts are never deleted; use :terminate")


def amend_contract(uow: UnitOfWork, c: EmploymentContract, payload: Mapping[str, Any]) -> None:
    if c.status != "APPROVED":
        raise InvalidStateTransition("only a contract in force can be amended")
    params = payload.get("parameters") or {}
    if not isinstance(params, Mapping) or not params or set(params) - set(AMENDABLE):
        raise ValidationFailed(f"parameters must name changes among {', '.join(AMENDABLE)}", fields=["parameters"])
    on = parse_date(payload.get("effective_date"), "effective_date") or clock.business_date()
    before = {k: str(getattr(c, k if k != "position_id" else "position_id")) for k in params}
    _apply_contract(c, params)
    snapshots = _check_integral(c.company_id, c.salary, on) if c.salary_type == "INTEGRAL" else {}
    c.save_versioned(uow.ctx.principal_id, [f.name for f in c._meta.concrete_fields if f.name not in ("id", "version")])
    after = {k: str(getattr(c, k)) for k in params}
    _history(uow, c, "amended", on, {"before": before, "after": after, "parameters": snapshots}, payload.get("reason") or "")
    audit(uow, "hr.contract.amended", "hr.contract", c, {"fields": sorted(params)})


def terminate_contract(uow: UnitOfWork, c: EmploymentContract, payload: Mapping[str, Any]) -> None:
    if c.status != "APPROVED":
        raise InvalidStateTransition("only a contract in force can be terminated")
    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise ReasonRequired("a termination needs a reason")
    on = parse_date(payload.get("effective_date"), "effective_date") or clock.business_date()
    if on < c.start_date:
        raise ValidationFailed("termination before the start date", fields=["effective_date"])
    c.status, c.termination_date, c.termination_reason = "CANCELLED", on, reason[:255]
    c.save_versioned(uow.ctx.principal_id, ["status", "termination_date", "termination_reason"])
    _history(uow, c, "terminated", on, {"termination_date": on.isoformat()}, reason)
    audit(uow, "hr.contract.terminated", "hr.contract", c)


def contract_in_force(employee: Employee, on: dt.date) -> EmploymentContract | None:
    return (
        EmploymentContract.objects.filter(employee=employee, status__in=("APPROVED", "CANCELLED"), start_date__lte=on)
        .filter(Q(termination_date__isnull=True) | Q(termination_date__gte=on))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=on))
        .order_by("-start_date")
        .first()
    )


def first_contract(employee: Employee) -> EmploymentContract | None:
    return (
        EmploymentContract.objects.filter(employee=employee, status__in=("APPROVED", "CANCELLED"))
        .order_by("start_date")
        .first()
    )


# --------------------------------------------------------------------------- data updates (portal → HR)


def submit_data_update(uow: UnitOfWork, employee: Employee, changes: Any) -> DataUpdateRequest:
    if not isinstance(changes, Mapping) or not changes:
        raise ValidationFailed("changes must name at least one field", fields=["changes"])
    forbidden = sorted(set(changes) - set(PORTAL_UPDATABLE))
    if forbidden:
        from iam.errors import FieldForbidden

        raise FieldForbidden("these fields cannot be changed from the portal", fields=forbidden)
    probe = Employee(company_id=employee.company_id)
    _apply_employee(probe, changes)  # validates the values without saving
    req = DataUpdateRequest.objects.create(
        tenant_id=employee.tenant_id,
        company_id=employee.company_id,
        employee=employee,
        changes={k: changes[k] for k in sorted(changes)},
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    audit(uow, "hr.data_update.submitted", "hr.data_update", req)
    return req


def review_data_update(uow: UnitOfWork, req: DataUpdateRequest, approve: bool, reason: str = "") -> None:
    if req.review_status != "SUBMITTED":
        raise InvalidStateTransition("the request was already reviewed")
    if req.created_by == uow.ctx.principal_id:
        from iam.errors import SodViolation

        raise SodViolation("a requester cannot review their own request")
    if not approve and not reason.strip():
        raise ReasonRequired("a rejection needs a reason")
    if approve:
        emp = Employee.objects.select_for_update().get(pk=req.employee_id)
        update_employee(uow, emp, dict(req.changes))
    req.review_status = "APPROVED" if approve else "REJECTED"
    req.reviewed_by, req.reviewed_at, req.review_reason = uow.ctx.principal_id, clock.now(), reason[:255]
    req.save_versioned(uow.ctx.principal_id, ["review_status", "reviewed_by", "reviewed_at", "review_reason"])
    audit(uow, f"hr.data_update.{req.review_status.lower()}", "hr.data_update", req)


def employee_of(tenant_id: uuid.UUID, company_id: uuid.UUID, employee_id: uuid.UUID | None) -> Employee:
    emp = (
        Employee.objects.filter(tenant_id=tenant_id, company_id=company_id, pk=employee_id).first()
        if employee_id
        else None
    )
    if emp is None:
        raise NotFound("employee not found", code="portal_document_not_owned")
    return emp
