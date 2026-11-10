"""Payroll concepts: CRUD with the dependency-graph check on save (D-025) and graph validation.

Every write runs inside the caller's unit of work, checks ``payroll.concept.*`` through the
PolicyService (deny by default), is audited, and emits ``payroll.concept.changed.v1``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from kernel import clock, services
from kernel.errors import Conflict, DomainError, NotFound, ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from payroll.concepts import formulas
from payroll.concepts.graph import (
    ConceptSpec,
    check_concept_code,
    check_input_schema,
    compile_graph,
    structural_order,
)
from payroll.concepts.parameters import PARAMETER_INPUTS, ParameterSet
from payroll.models import CONCEPT_TYPES, PayrollConcept, PayslipLine
from workflow.audit.service import record_change
from workflow.formula.errors import FormulaError

FIELDS = ("code", "name", "concept_type", "formula_id", "depends_on", "account_role", "salary_base",
          "electronic_payroll_code")  # fmt: skip


def policy() -> PolicyService:
    return services.get(PolicyService)


def to_json(c: PayrollConcept) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "company_id": str(c.company_id),
        "code": c.code,
        "name": c.name,
        "status": c.status,
        "concept_type": c.concept_type,
        "formula_id": str(c.formula_id) if c.formula_id else None,
        "depends_on": list(c.depends_on or []),
        "account_role": c.account_role,
        "salary_base": c.salary_base,
        "electronic_payroll_code": c.electronic_payroll_code,
        "version": c.version,
        "audit": {
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "created_by": str(c.created_by) if c.created_by else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "updated_by": str(c.updated_by) if c.updated_by else None,
        },
    }


def _clean(data: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    out: dict[str, Any] = {}
    if "code" in data or not partial:
        code = data.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 40:
            raise ValidationFailed("code is required (1..40 characters)", field="code")
        out["code"] = check_concept_code(code)
    if "name" in data or not partial:
        name = data.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise ValidationFailed("name is required (1..200 characters)", field="name")
        out["name"] = name.strip()
    if "concept_type" in data or not partial:
        if data.get("concept_type") not in CONCEPT_TYPES:
            raise ValidationFailed(f"concept_type must be one of {', '.join(CONCEPT_TYPES)}", field="concept_type")
        out["concept_type"] = data["concept_type"]
    if "formula_id" in data:
        raw = data["formula_id"]
        try:
            out["formula_id"] = uuid.UUID(str(raw)) if raw else None
        except ValueError as exc:
            raise ValidationFailed("formula_id must be a UUID", field="formula_id") from exc
    if "depends_on" in data:
        deps = data["depends_on"]
        if not isinstance(deps, list) or not all(isinstance(d, str) and 1 <= len(d) <= 40 for d in deps):
            raise ValidationFailed("depends_on must be a list of concept codes", field="depends_on")
        out["depends_on"] = sorted(set(deps))
    for key, limit in (("account_role", 255), ("electronic_payroll_code", 255)):
        if key in data:
            val = data[key]
            if val is not None and (not isinstance(val, str) or len(val) > limit):
                raise ValidationFailed(f"{key} must be text up to {limit} characters", field=key)
            out[key] = val if key == "electronic_payroll_code" else (val or "")
    if "salary_base" in data:
        if not isinstance(data["salary_base"], bool):
            raise ValidationFailed("salary_base must be true/false", field="salary_base")
        out["salary_base"] = data["salary_base"]
    return out


def _concepts(tenant_id: uuid.UUID, company_id: uuid.UUID) -> list[PayrollConcept]:
    return list(
        PayrollConcept.objects.filter(tenant_id=tenant_id, company_id=company_id, status=PayrollConcept.ACTIVE)
    )


def _check_graph(ctx: TenantContext, company_id: uuid.UUID, concept: PayrollConcept) -> list[str]:
    """Structural DAG check of the company's concepts with ``concept`` replaced/added (on save)."""
    nodes = {c.code: list(c.depends_on or []) for c in _concepts(ctx.tenant_id, company_id) if c.pk != concept.pk}
    nodes[concept.code] = list(concept.depends_on or [])
    return structural_order(nodes)


def _check_formula(ctx: TenantContext, company_id: uuid.UUID, concept: PayrollConcept) -> None:
    if concept.formula_id is None:
        return
    row = formulas.referenced(ctx.tenant_id, company_id, concept.formula_id)
    check_input_schema(row.inputs)  # unknown inputs / unknown param_* fail closed on save
    if not concept.depends_on and row.depends_on:
        concept.depends_on = sorted(set(row.depends_on))


def create(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PayrollConcept:
    ctx = uow.ctx
    ctx.require_company(company_id)
    policy().require(ctx, "payroll.concept.create", {"company_id": company_id})
    values = _clean(data, partial=False)
    if PayrollConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=values["code"]).exists():
        raise Conflict(f"concept {values['code']} already exists", code="duplicate")
    concept = PayrollConcept(
        tenant_id=ctx.tenant_id, company_id=company_id, created_by=ctx.principal_id, updated_by=ctx.principal_id,
        **values,
    )  # fmt: skip
    _check_formula(ctx, company_id, concept)
    _check_graph(ctx, company_id, concept)
    concept.save()
    record_change("payroll.concept.create", "payroll.concept", concept.id, company_id=company_id,
                  after={k: str(v) for k, v in values.items()}, uow=uow)  # fmt: skip
    uow.emit("payroll.concept.changed.v1", {"concept_id": str(concept.id), "code": concept.code},
             company_id=company_id)  # fmt: skip
    return concept


def get(ctx: TenantContext, company_id: uuid.UUID, concept_id: uuid.UUID, *, lock: bool = False) -> PayrollConcept:
    qs = PayrollConcept.objects.select_for_update() if lock else PayrollConcept.objects
    row = qs.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=concept_id).first()
    if row is None:
        raise NotFound("concept not found")
    return row


def update(
    uow: UnitOfWork, company_id: uuid.UUID, concept_id: uuid.UUID, patch: Mapping[str, Any], if_match: str | None
) -> PayrollConcept:
    ctx = uow.ctx
    ctx.require_company(company_id)
    policy().require(ctx, "payroll.concept.update", {"company_id": company_id})
    concept = get(ctx, company_id, concept_id, lock=True)
    concept.check_if_match(if_match)
    values = _clean(patch, partial=True)
    if "code" in values and values["code"] != concept.code:
        if PayslipLine.objects.filter(concept_id=concept.id).exists():
            raise Conflict("the code of a concept used in payslips cannot change", code="concept_in_use")
        if any(concept.code in (c.depends_on or []) for c in _concepts(ctx.tenant_id, company_id)):
            raise Conflict("other concepts depend on this code", code="concept_in_use")
    before = {k: str(getattr(concept, k)) for k in values}
    for k, v in values.items():
        setattr(concept, k, v)
    _check_formula(ctx, company_id, concept)
    _check_graph(ctx, company_id, concept)
    concept.save_versioned(ctx.principal_id, [*values, "depends_on"])
    record_change("payroll.concept.update", "payroll.concept", concept.id, company_id=company_id, before=before,
                  after={k: str(v) for k, v in values.items()}, uow=uow)  # fmt: skip
    uow.emit("payroll.concept.changed.v1", {"concept_id": str(concept.id), "code": concept.code},
             company_id=company_id)  # fmt: skip
    return concept


def delete(uow: UnitOfWork, company_id: uuid.UUID, concept_id: uuid.UUID, if_match: str | None) -> None:
    ctx = uow.ctx
    ctx.require_company(company_id)
    policy().require(ctx, "payroll.concept.delete", {"company_id": company_id})
    concept = get(ctx, company_id, concept_id, lock=True)
    concept.check_if_match(if_match)
    if PayslipLine.objects.filter(concept_id=concept.id).exists() or concept.novelties.exists():
        raise Conflict("concept in use: archive is not offered, corrections go through new concepts",
                       code="concept_in_use")  # fmt: skip
    if any(concept.code in (c.depends_on or []) for c in _concepts(ctx.tenant_id, company_id)):
        raise Conflict("other concepts depend on this concept", code="concept_in_use")
    record_change("payroll.concept.delete", "payroll.concept", concept.id, company_id=company_id,
                  before={"code": concept.code}, uow=uow)  # fmt: skip
    uow.emit("payroll.concept.changed.v1", {"concept_id": str(concept.id), "code": concept.code},
             company_id=company_id)  # fmt: skip
    concept.delete()


def specs(ctx: TenantContext, company_id: uuid.UUID, on: dt.date) -> list[ConceptSpec]:
    """Concept specs with the ACTIVE formula version effective ``on`` (``formula_not_approved``)."""
    out: list[ConceptSpec] = []
    for c in sorted(_concepts(ctx.tenant_id, company_id), key=lambda x: x.code):
        ref = formulas.active_version(ctx.tenant_id, company_id, c.formula_id, on) if c.formula_id else None
        out.append(ConceptSpec(str(c.id), c.code, c.concept_type, tuple(c.depends_on or ()), ref, c.rounding_places))
    return out


def validate_graph(ctx: TenantContext, company_id: uuid.UUID, on: dt.date | None = None) -> dict[str, Any]:
    """``:validate-graph`` — the full graph with ACTIVE formulas: order, parameters, first error."""
    ctx.require_company(company_id)
    policy().require(ctx, "payroll.concept.read", {"company_id": company_id})
    on = on or clock.business_date()
    try:
        compiled = compile_graph(specs(ctx, company_id, on))
    except FormulaError as exc:
        return {"status": "invalid", "data": {"on": on.isoformat(), "order": [],
                "errors": [{"code": exc.code, "detail": exc.detail, **exc.extra}]}}  # fmt: skip
    params = ParameterSet(company_id, on)
    status, errors = "valid", []
    try:
        params.inputs(compiled.parameter_inputs)
    except DomainError as exc:  # reported to the caller (fail closed), never swallowed
        status, errors = "invalid", [{"code": exc.code, "detail": exc.detail, **exc.extra}]
    return {
        "status": status,
        "data": {
            "on": on.isoformat(),
            "order": compiled.order,
            "errors": errors,
            "parameters": [
                {"input": n, "code": PARAMETER_INPUTS[n], **params.snapshot().get(PARAMETER_INPUTS[n], {})}
                for n in compiled.parameter_inputs
            ],
        },
    }
