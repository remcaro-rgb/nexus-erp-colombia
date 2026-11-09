"""Database-backed tax service: builds the engine from the company's tax codes,
withholding concepts, ICA tariffs and party tax profiles.

* :class:`EngineTaxService` implements ``kernel.interfaces.TaxService`` (compute /
  withholdings) for other modules (sales, purchasing, treasury D-029, payroll).
* :func:`compute_document` is the full calculation behind ``POST /tax/compute``.

Party convention: the company's own fiscal profile is the ``PartyTaxProfile``
whose ``party_id`` equals the company id.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from kernel.interfaces.tax import TaxComputation, TaxLineInput, TaxLineResult
from kernel.uow import UnitOfWork
from tax.engine.engine import ParameterSource, TaxEngine
from tax.engine.types import DocumentInput, DocumentLine, PartyProfile, TaxCodeDef, TaxResult, WithholdingConceptDef
from tax.models import IcaTariff, PartyTaxProfile, TaxCode, WithholdingConcept
from tax.parameters.store import DbParameterProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Document types where the company sells (the counterparty is the buyer / payer).
SALES_DOCUMENT_TYPES = frozenset(
    {"FV", "NC", "ND", "DEE", "RC", "sales.invoice", "sales.credit_note", "sales.debit_note", "sales.order",
     "pos.sale", "treasury.cash_receipt"}
)  # fmt: skip


def _active(status: str) -> bool:
    return status == "ACTIVE"


def tax_code_defs(tenant_id: uuid.UUID, company_id: uuid.UUID) -> dict[str, TaxCodeDef]:
    return {
        c.code: TaxCodeDef(
            code=c.code,
            tax_type=c.tax_type,
            rate_parameter_code=c.rate_parameter_code,
            base_rule=c.base_rule,
            base_tax_type=c.base_tax_type,
            base_percent_parameter_code=c.base_percent_parameter_code,
            min_base_uvt_parameter_code=c.min_base_uvt_parameter_code,
            applicability=tuple(c.applicability or ()),
            account_role=c.account_role,
            active=_active(c.status),
        )
        for c in TaxCode.objects.filter(tenant_id=tenant_id, company_id=company_id)
    }


def concept_defs(tenant_id: uuid.UUID, company_id: uuid.UUID) -> dict[str, WithholdingConceptDef]:
    return {
        c.code: WithholdingConceptDef(c.code, c.tax_type, c.base_parameter_code, c.rate_parameter_code,
                                      _active(c.status))
        for c in WithholdingConcept.objects.filter(tenant_id=tenant_id, company_id=company_id)
    }  # fmt: skip


def ica_tariffs(tenant_id: uuid.UUID, company_id: uuid.UUID) -> dict[tuple[str, str], str]:
    return {
        (t.municipality_code, t.activity_code): t.rate_parameter_code
        for t in IcaTariff.objects.filter(tenant_id=tenant_id, company_id=company_id, status="ACTIVE")
    }


def profile_of(tenant_id: uuid.UUID, company_id: uuid.UUID, party_id: uuid.UUID | None) -> PartyProfile:
    """Profile of ``party_id``; unknown party → the most conservative default (no agent flags)."""
    if party_id is None:
        return PartyProfile(person_type="NATURAL", regime="NO_RESPONSABLE_IVA")
    row = PartyTaxProfile.objects.filter(
        tenant_id=tenant_id, company_id=company_id, party_id=party_id, active=True
    ).first()
    if row is None:
        return PartyProfile(person_type="NATURAL", regime="NO_RESPONSABLE_IVA")
    return to_profile(row)


def to_profile(row: PartyTaxProfile) -> PartyProfile:
    return PartyProfile(
        person_type=row.person_type,
        regime=row.regime,
        gran_contribuyente=row.gran_contribuyente,
        autorretenedor=row.autorretenedor,
        withholding_agent=row.withholding_agent,
        vat_withholding_agent=row.vat_withholding_agent,
        ica_municipalities=tuple(row.ica_municipalities or ()),
        responsibilities=tuple(row.responsibilities or ()),
    )


def engine_for(tenant_id: uuid.UUID, company_id: uuid.UUID, parameters: ParameterSource | None = None) -> TaxEngine:
    return TaxEngine(
        parameters or DbParameterProvider(tenant_id=tenant_id),
        tax_code_defs(tenant_id, company_id),
        concept_defs(tenant_id, company_id),
        ica_tariffs(tenant_id, company_id),
    )


def compute_document(
    tenant_id: uuid.UUID,
    company_id: uuid.UUID,
    document_type: str,
    document_date: dt.date,
    party_id: uuid.UUID | None,
    lines: list[DocumentLine],
    *,
    municipality: str | None = None,
    rounding_mode: str = "LINE",
    seller: PartyProfile | None = None,
    buyer: PartyProfile | None = None,
    parameters: ParameterSource | None = None,
) -> TaxResult:
    company_profile = profile_of(tenant_id, company_id, company_id)
    party_profile = profile_of(tenant_id, company_id, party_id)
    if document_type in SALES_DOCUMENT_TYPES:
        default_seller, default_buyer = company_profile, party_profile
    else:
        default_seller, default_buyer = party_profile, company_profile
    doc = DocumentInput(
        company_id=company_id,
        document_date=document_date,
        seller=seller or default_seller,
        buyer=buyer or default_buyer,
        lines=tuple(lines),
        municipality=municipality,
        rounding_mode=rounding_mode,
    )
    return engine_for(tenant_id, company_id, parameters).compute(doc)


def _to_computation(result: TaxResult, withholding: bool) -> TaxComputation:
    lines = tuple(
        TaxLineResult(t.line_ref, t.tax_code, t.base_amount, t.rate, t.amount, t.withholding)
        for t in result.lines
        if t.withholding is withholding
    )
    return TaxComputation(lines, result.parameter_snapshots, result.warnings)


class EngineTaxService:
    """``kernel.interfaces.TaxService`` over the engine (tenant from the active context)."""

    def __init__(self, parameters: ParameterSource | None = None) -> None:
        self.parameters = parameters

    @staticmethod
    def _lines(lines: list[TaxLineInput]) -> list[DocumentLine]:
        return [DocumentLine(x.line_ref, x.base_amount, tuple(x.tax_codes), x.concept) for x in lines]

    def _run(self, company_id: uuid.UUID, document_type: str, document_date: dt.date, party_id: uuid.UUID | None,
             lines: list[TaxLineInput]) -> TaxResult:  # fmt: skip
        from kernel import tenancy

        ctx = tenancy.require_current()
        ctx.require_company(company_id)
        return compute_document(ctx.tenant_id, company_id, document_type, document_date, party_id,
                                self._lines(lines), parameters=self.parameters)  # fmt: skip

    def compute(self, company_id: uuid.UUID, document_type: str, document_date: dt.date, party_id: uuid.UUID | None,
                lines: list[TaxLineInput]) -> TaxComputation:  # fmt: skip
        return _to_computation(self._run(company_id, document_type, document_date, party_id, lines), False)

    def withholdings(self, company_id: uuid.UUID, document_type: str, document_date: dt.date, party_id: uuid.UUID,
                     lines: list[TaxLineInput]) -> TaxComputation:  # fmt: skip
        return _to_computation(self._run(company_id, document_type, document_date, party_id, lines), True)


# ----------------------------------------------------------------------------- company setup


def read_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{name} is not a mapping")
    return data


def install_company_defaults(
    uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any] | None = None
) -> dict[str, int]:
    """Create the default tax codes and withholding concepts for a company (idempotent)."""
    uow.ctx.require_company(company_id)
    data = data or read_fixture("tax_codes_default.yaml")
    who = {"created_by": uow.ctx.principal_id, "updated_by": uow.ctx.principal_id}
    codes = concepts = tariffs = 0
    for row in data.get("tax_codes", []):
        _, created = TaxCode.objects.get_or_create(
            tenant_id=uow.ctx.tenant_id, company_id=company_id, code=row["code"],
            defaults={
                "name": row["name"], "tax_type": row["tax_type"],
                "rate_parameter_code": row.get("rate_parameter_code", ""),
                "base_rule": row.get("base_rule", "NET"), "base_tax_type": row.get("base_tax_type", ""),
                "base_percent_parameter_code": row.get("base_percent_parameter_code", ""),
                "min_base_uvt_parameter_code": row.get("min_base_uvt_parameter_code", ""),
                "applicability": list(row.get("applicability", []) or []),
                "account_role": row.get("account_role", ""), **who,
            },
        )  # fmt: skip
        codes += int(created)
    for row in [*data.get("withholding_concepts", []), *data.get("synthetic_concepts", [])]:
        _, created = WithholdingConcept.objects.get_or_create(
            tenant_id=uow.ctx.tenant_id, company_id=company_id, code=row["code"],
            defaults={"name": row["name"], "tax_type": row["tax_type"],
                      "base_parameter_code": row.get("base_parameter_code", ""),
                      "rate_parameter_code": row.get("rate_parameter_code", ""), **who},
        )  # fmt: skip
        concepts += int(created)
    for row in data.get("synthetic_ica_tariffs", []):
        _, created = IcaTariff.objects.get_or_create(
            tenant_id=uow.ctx.tenant_id, company_id=company_id, code=row["code"],
            defaults={"name": row["name"], "municipality_code": row["municipality_code"],
                      "activity_code": row["activity_code"], "rate_parameter_code": row["rate_parameter_code"], **who},
        )  # fmt: skip
        tariffs += int(created)
    return {"tax_codes": codes, "withholding_concepts": concepts, "ica_tariffs": tariffs}


def to_decimal(raw: Any, field: str) -> Decimal:
    from tax.parameters.values import _dec

    return _dec(raw, field)
