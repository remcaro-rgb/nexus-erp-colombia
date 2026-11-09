"""Engine input/output types (Decimal only, frozen)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

WITHHOLDING_TYPES = frozenset({"RETEFUENTE", "RETEIVA", "RETEICA", "AUTORRETENCION"})


@dataclass(frozen=True, slots=True)
class PartyProfile:
    """Fiscal profile of seller or buyer (from ``tax.PartyTaxProfile``)."""

    person_type: str = "JURIDICA"  # NATURAL | JURIDICA
    regime: str = "RESPONSABLE_IVA"  # RESPONSABLE_IVA | NO_RESPONSABLE_IVA | SIMPLE | NO_CONTRIBUYENTE
    gran_contribuyente: bool = False
    autorretenedor: bool = False
    withholding_agent: bool = False
    vat_withholding_agent: bool = False
    ica_municipalities: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()

    def attr(self, name: str) -> Any:
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class TaxCodeDef:
    code: str
    tax_type: str
    rate_parameter_code: str = ""
    base_rule: str = "NET"  # NET | TAX_AMOUNT | PERCENT_OF_BASE
    base_tax_type: str = ""
    base_percent_parameter_code: str = ""
    min_base_uvt_parameter_code: str = ""
    applicability: tuple[dict[str, Any], ...] = ()
    account_role: str = ""
    active: bool = True

    @property
    def withholding(self) -> bool:
        return self.tax_type in WITHHOLDING_TYPES


@dataclass(frozen=True, slots=True)
class WithholdingConceptDef:
    code: str
    tax_type: str
    base_parameter_code: str = ""
    rate_parameter_code: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class DocumentLine:
    line_ref: str
    base_amount: Decimal
    tax_codes: tuple[str, ...] = ()
    concept: str | None = None  # withholding concept code
    activity_code: str | None = None  # CIIU (ICA)

    def __post_init__(self) -> None:
        if not isinstance(self.base_amount, Decimal):
            raise TypeError("base_amount must be Decimal")


@dataclass(frozen=True, slots=True)
class DocumentInput:
    company_id: Any
    document_date: dt.date
    seller: PartyProfile
    buyer: PartyProfile
    lines: tuple[DocumentLine, ...]
    municipality: str | None = None
    rounding_mode: str = "LINE"  # LINE (default, shared/07 §8) | DOCUMENT
    currency: str = "COP"


@dataclass(frozen=True, slots=True)
class TraceStep:
    step: str
    line_ref: str | None
    tax_code: str | None
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "line_ref": self.line_ref,
            "tax_code": self.tax_code,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class LineTax:
    line_ref: str
    tax_code: str
    tax_type: str
    base_amount: Decimal
    rate: Decimal
    unrounded: Decimal
    amount: Decimal
    withholding: bool
    parameters: tuple[str, ...]
    account_role: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "line_ref": self.line_ref, "tax_code": self.tax_code, "tax_type": self.tax_type,
            "base_amount": str(self.base_amount), "rate": str(self.rate), "unrounded": str(self.unrounded),
            "amount": str(self.amount), "withholding": self.withholding, "parameters": list(self.parameters),
            "account_role": self.account_role,
        }  # fmt: skip


@dataclass(frozen=True, slots=True)
class TaxResult:
    lines: tuple[LineTax, ...]
    totals: dict[str, Decimal]
    tax_total: Decimal
    withholding_total: Decimal
    rounding_adjustment: Decimal
    parameter_snapshots: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    trace: tuple[TraceStep, ...]
    rounding_mode: str

    def to_json(self) -> dict[str, Any]:
        return {
            "lines": [x.to_json() for x in self.lines],
            "totals": {k: str(v) for k, v in sorted(self.totals.items())},
            "tax_total": str(self.tax_total),
            "withholding_total": str(self.withholding_total),
            "rounding_adjustment": str(self.rounding_adjustment),
            "rounding_mode": self.rounding_mode,
            "parameter_snapshots": list(self.parameter_snapshots),
            "warnings": list(self.warnings),
            "trace": [t.to_json() for t in self.trace],
        }
