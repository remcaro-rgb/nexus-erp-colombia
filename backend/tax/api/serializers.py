"""JSON representations (contract ``contracts/openapi/modules/tax.yaml``)."""

from __future__ import annotations

from typing import Any

from tax.api.base import audit
from tax.exogena.models import ExogenaFormatPack
from tax.models import IcaTariff, Parameter, PartyTaxProfile, SourceRecord, TaxCalendar, TaxCode, WithholdingConcept


def _s(value: Any) -> str | None:
    return str(value) if value is not None else None


def parameter(p: Parameter) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "tenant_id": str(p.tenant_id),
        "company_id": _s(p.company_id),
        "code": p.code,
        "status": p.status,
        "value": {"type": p.value_type, **p.value},
        "valid_from": p.valid_from.isoformat(),
        "valid_to": p.valid_to.isoformat() if p.valid_to else None,
        "source_id": str(p.source_id),
        "parameter_status": p.parameter_status,
        "legal_reviewed_by": _s(p.legal_reviewed_by),
        "legal_reviewed_at": p.legal_reviewed_at.isoformat() if p.legal_reviewed_at else None,
        "synthetic": p.synthetic,
        # CCR P06-1: approval workflow + scope fields proposed for the contract
        "approval": p.approval,
        "proposed_by": _s(p.proposed_by),
        "scope": p.scope,
        "scope_ref": p.scope_ref,
        "legal_history": p.legal_history,
        "supersedes_id": _s(p.supersedes_id),
        "notes": p.notes,
        "version": p.version,
        "audit": audit(p),
    }


def source(s: SourceRecord) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "tenant_id": _s(s.tenant_id),
        "company_id": None,
        "status": s.status,
        "url": s.url,
        "title": s.title,
        "accessed_at": s.accessed_at.isoformat(),
        "method": s.method,
        "excerpt_hash": s.excerpt_hash,
        "version": s.version,
        "audit": audit(s),
    }


def tax_code(t: TaxCode) -> dict[str, Any]:
    return {
        "id": str(t.id), "tenant_id": str(t.tenant_id), "company_id": str(t.company_id), "code": t.code,
        "name": t.name, "status": t.status, "tax_type": t.tax_type, "rate_parameter_code": t.rate_parameter_code,
        "account_role": t.account_role, "version": t.version, "audit": audit(t),
    }  # fmt: skip


def withholding_concept(w: WithholdingConcept) -> dict[str, Any]:
    return {
        "id": str(w.id), "tenant_id": str(w.tenant_id), "company_id": str(w.company_id), "code": w.code,
        "name": w.name, "status": w.status, "tax_type": w.tax_type, "base_parameter_code": w.base_parameter_code,
        "rate_parameter_code": w.rate_parameter_code, "version": w.version, "audit": audit(w),
    }  # fmt: skip


def ica_tariff(t: IcaTariff) -> dict[str, Any]:
    return {
        "id": str(t.id), "tenant_id": str(t.tenant_id), "company_id": str(t.company_id), "code": t.code,
        "name": t.name, "status": t.status, "municipality_code": t.municipality_code,
        "activity_code": t.activity_code, "rate_parameter_code": t.rate_parameter_code, "version": t.version,
        "audit": audit(t),
    }  # fmt: skip


def party_tax_profile(p: PartyTaxProfile) -> dict[str, Any]:
    return {
        "id": str(p.id), "tenant_id": str(p.tenant_id), "company_id": str(p.company_id), "active": p.active,
        "party": {"id": str(p.party_id), "kind": p.party_kind}, "regime": p.regime,
        "responsibilities": list(p.responsibilities or []), "withholding_agent": p.withholding_agent,
        "version": p.version, "audit": audit(p),
    }  # fmt: skip


def calendar(c: TaxCalendar) -> dict[str, Any]:
    return {
        "id": str(c.id), "tenant_id": str(c.tenant_id), "company_id": str(c.company_id), "code": c.code,
        "name": c.name, "status": c.status, "obligation": c.obligation, "year": c.year,
        "due_dates": c.due_dates, "version": c.version, "audit": audit(c),
    }  # fmt: skip


def exogena_pack(p: ExogenaFormatPack) -> dict[str, Any]:
    return {
        "id": str(p.id), "tenant_id": str(p.tenant_id), "company_id": _s(p.company_id), "code": p.code,
        "status": p.status, "year": p.year, "pack": p.pack, "verified": p.verified, "synthetic": p.synthetic,
        "label": p.label, "version": p.version, "audit": audit(p),
    }  # fmt: skip
