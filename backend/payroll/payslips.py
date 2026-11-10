"""Payslips (read-only) and nómina electrónica data (F-PAY-FORMULA explain, R-FE-09).

Field policy: amounts are confidential and the employee is restricted personal data. Fields the
principal may not read (``PolicyService.field_mask(ctx, "payroll.payslip")``) are returned as
``null`` with their names listed in ``masked`` — the same rule for UI, API, export, AI and MCP.
The legal notice ``NOT LEGALLY VERIFIED`` and the parameter statuses are never masked.

Electronic payroll: one document per period with a NominaIndividual entry per employee built from
the POSTED payslips and the concepts' DIAN element mapping. Transmission (CUNE, signature, DIAN
validation) is EXTERNAL: P15's FiscalGateway; here the status stays ``SIMULATED_NOT_TRANSMITTED``
and the payload is labelled FIXTURE_NOT_CERTIFIED.
"""

from __future__ import annotations

import uuid
from typing import Any

from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from payroll.concepts import service as concepts
from payroll.concepts.parameters import CATALOGUE_VERSION, NOT_LEGALLY_VERIFIED
from payroll.models import ElectronicPayrollDocument, PayrollRun, Payslip
from payroll.runs import get_period
from workflow.audit.service import record_change

RESOURCE = "payroll.payslip"
CONFIDENTIAL = ("earnings", "deductions", "net_pay", "employer_cost", "lines")
RESTRICTED = ("employee_id", "employee_name")
FIXTURE_LABEL = "SIMULADO — FIXTURE_NOT_CERTIFIED (sin transmisión a la DIAN)"
MAPPING_VERSION = "SYN-NE-2026.1"


def _money(v: Any) -> dict[str, str]:
    return {"amount": str(q_amount(v)), "currency": "COP"}


def readable(ctx: TenantContext) -> frozenset[str]:
    return concepts.policy().field_mask(ctx, RESOURCE).readable


def payslip_json(ctx: TenantContext, slip: Payslip, *, fields: frozenset[str] | None = None) -> dict[str, Any]:
    fields = readable(ctx) if fields is None else fields
    lines = [
        {
            "concept": ln.concept_code, "concept_type": ln.concept_type, "sequence": ln.sequence,
            "amount": _money(ln.amount), "quantity": None if ln.quantity is None else str(ln.quantity),
            "formula_id": str(ln.formula_id) if ln.formula_id else None, "formula_version": ln.formula_version,
            "formula_hash": ln.formula_hash, "inputs": ln.inputs, "trace": ln.trace,
            "parameters": ln.parameters, "electronic_payroll_code": ln.electronic_payroll_code,
        }  # fmt: skip
        for ln in slip.lines.all()
    ]
    full: dict[str, Any] = {
        "employee_id": str(slip.employee_id),
        "employee_name": slip.employee_name,
        "earnings": _money(slip.earnings),
        "deductions": _money(slip.deductions),
        "net_pay": _money(slip.net_pay),
        "employer_cost": _money(slip.employer_cost),
        "lines": lines,
    }
    masked = sorted(k for k in (*CONFIDENTIAL, *RESTRICTED) if k not in fields)
    body: dict[str, Any] = {
        "id": str(slip.id),
        "tenant_id": str(slip.tenant_id),
        "company_id": str(slip.company_id),
        "run_id": str(slip.run_id),
        "status": "POSTED" if slip.posted else "CALCULATED",
        "legally_verified": slip.legally_verified,
        "legal_notice": slip.legal_notice,
        "parameters": [
            {k: p.get(k) for k in ("code", "input", "status", "source_id", "valid_from", "valid_to",
                                   "legally_verified", "label", "value")}  # fmt: skip
            for p in (slip.parameter_snapshot or {}).values()
        ],
        "masked": masked,
    }
    for key, value in full.items():
        body[key] = None if key in masked else value
    return body


def payslips_for(ctx: TenantContext, company_id: uuid.UUID) -> Any:
    concepts.policy().require(ctx, "payroll.payslip.read", {"company_id": company_id})
    qs = Payslip.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).prefetch_related("lines")
    return concepts.policy().scope_queryset(ctx, "payroll.payslip.read", qs)


# --------------------------------------------------------------------------- electronic payroll


def electronic_json(doc: ElectronicPayrollDocument) -> dict[str, Any]:
    return {
        "id": str(doc.id), "tenant_id": str(doc.tenant_id), "company_id": str(doc.company_id),
        "period_id": str(doc.period_id), "document_type": doc.document_type, "status": doc.status,
        "adjustment_kind": doc.adjustment_kind or None, "replaces": str(doc.replaces_id) if doc.replaces_id else None,
        "transmission_status": doc.transmission_status, "cune": doc.cune, "label": FIXTURE_LABEL,
        "documents": len((doc.payload or {}).get("documents", [])), "version": doc.version,
    }  # fmt: skip


def create_electronic(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> ElectronicPayrollDocument:
    ctx = uow.ctx
    ctx.require_company(company_id)
    concepts.policy().require(ctx, "payroll.electronic_payroll.create", {"company_id": company_id})
    unknown = sorted(set(data) - {"period_id"})
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    try:
        period = get_period(ctx, company_id, uuid.UUID(str(data.get("period_id"))))
    except ValueError as exc:
        raise ValidationFailed("period_id must be a UUID", field="period_id") from exc
    doc = ElectronicPayrollDocument.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, period=period, created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )  # fmt: skip
    record_change("payroll.electronic_payroll.create", "payroll.electronic_payroll", doc.id, company_id=company_id,
                  after={"period": period.code}, uow=uow)  # fmt: skip
    return doc


def _individual(slip: Payslip) -> dict[str, Any]:
    devengados: list[dict[str, Any]] = []
    deducciones: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for ln in slip.lines.all():
        if ln.concept_type not in ("DEVENGO", "DEDUCCION"):
            continue
        if not ln.electronic_payroll_code:
            unmapped.append(ln.concept_code)
        item = {"element": ln.electronic_payroll_code, "concept": ln.concept_code, "amount": str(q_amount(ln.amount))}
        (devengados if ln.concept_type == "DEVENGO" else deducciones).append(item)
    return {
        "type": ElectronicPayrollDocument.INDIVIDUAL,
        "employee_id": str(slip.employee_id),
        "run_id": str(slip.run_id),
        "devengados": devengados,
        "deducciones": deducciones,
        "devengados_total": str(q_amount(slip.earnings)),
        "deducciones_total": str(q_amount(slip.deductions)),
        "comprobante_total": str(q_amount(slip.net_pay)),
        "unmapped_concepts": unmapped,
        "legal_notice": slip.legal_notice or "",
    }


def generate(uow: UnitOfWork, company_id: uuid.UUID, doc_id: uuid.UUID) -> ElectronicPayrollDocument:
    ctx = uow.ctx
    ctx.require_company(company_id)
    concepts.policy().require(ctx, "payroll.electronic_payroll.update", {"company_id": company_id})
    doc = (
        ElectronicPayrollDocument.objects.select_for_update()
        .filter(tenant_id=ctx.tenant_id, company_id=company_id, id=doc_id)
        .first()
    )
    if doc is None:
        raise NotFound("electronic payroll document not found")
    if doc.status != ElectronicPayrollDocument.DRAFT:
        raise Conflict("already generated; corrections are NominaIndividualDeAjuste documents (:adjust)")
    slips = list(
        Payslip.objects.filter(
            run__period=doc.period, run__status__in=[PayrollRun.POSTED, PayrollRun.PAID], posted=True
        )
        .prefetch_related("lines")
        .order_by("employee_id")
    )
    if not slips:
        raise ValidationFailed("the period has no posted payslips")
    doc.payload = {
        "label": FIXTURE_LABEL, "mapping_version": MAPPING_VERSION, "parameter_catalogue": CATALOGUE_VERSION,
        "period": {"start": doc.period.start_date.isoformat(), "end": doc.period.end_date.isoformat()},
        "deadline_rule": "first 10 business days of the following month (R-FE-09; external DIAN calendar)",
        "legal_notice": NOT_LEGALLY_VERIFIED if any(not s.legally_verified for s in slips) else "",
        "documents": [_individual(s) for s in slips],
    }  # fmt: skip
    doc.status, doc.generated_at = ElectronicPayrollDocument.GENERATED, clock.now()
    doc.save_versioned(ctx.principal_id, ["payload", "status", "generated_at"])
    record_change("payroll.electronic_payroll.generate", "payroll.electronic_payroll", doc.id, company_id=company_id,
                  after={"documents": len(slips)}, uow=uow)  # fmt: skip
    return doc


def adjust(uow: UnitOfWork, company_id: uuid.UUID, doc_id: uuid.UUID, kind: str = "Reemplazar") -> Any:
    """NominaIndividualDeAjuste: a new document replacing (or deleting) a generated one."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    concepts.policy().require(ctx, "payroll.electronic_payroll.update", {"company_id": company_id})
    if kind not in ("Reemplazar", "Eliminar"):
        raise ValidationFailed("kind must be Reemplazar or Eliminar", field="kind")
    doc = (
        ElectronicPayrollDocument.objects.select_for_update()
        .filter(tenant_id=ctx.tenant_id, company_id=company_id, id=doc_id)
        .first()
    )
    if doc is None:
        raise NotFound("electronic payroll document not found")
    if doc.status != ElectronicPayrollDocument.GENERATED:
        raise Conflict("only generated documents can be adjusted")
    payload = {**(doc.payload or {}), "adjustment": kind, "replaces": str(doc.id)}
    if kind == "Eliminar":
        payload["documents"] = []
    new = ElectronicPayrollDocument.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, period=doc.period, document_type=doc.ADJUSTMENT,
        adjustment_kind=kind, replaces=doc, status=doc.GENERATED, payload=payload, generated_at=clock.now(),
        created_by=ctx.principal_id, updated_by=ctx.principal_id,
    )  # fmt: skip
    doc.status = doc.SUPERSEDED
    doc.save_versioned(ctx.principal_id, ["status"])
    record_change("payroll.electronic_payroll.adjust", "payroll.electronic_payroll", new.id, company_id=company_id,
                  after={"replaces": str(doc.id), "kind": kind}, uow=uow)  # fmt: skip
    return new
