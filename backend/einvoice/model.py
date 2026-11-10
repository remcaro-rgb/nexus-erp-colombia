"""Canonical internal model of a fiscal document (``FiscalDocumentModel``).

Builders are pure functions from this model to XML; the issuing module (sales, POS, purchasing,
payroll) maps its own document to this model. Money and quantities are ``Decimal`` only (C2-2):
``from_dict`` refuses floats. Amounts are validated (lines → totals) before anything is built.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from einvoice import annex
from kernel.errors import ValidationFailed

CENT = Decimal("0.01")
FIXTURE_OUTCOMES = ("accept", "accept_with_notices", "reject", "transient")


def dec(value: Any, name: str) -> Decimal:
    if isinstance(value, float) or isinstance(value, bool):
        raise ValidationFailed(f"{name} must be a decimal string, not {type(value).__name__}", field=name)
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"{name} is not a decimal", field=name) from None
    if not out.is_finite():
        raise ValidationFailed(f"{name} is not finite", field=name)
    return out


def _req(data: dict[str, Any], key: str, prefix: str = "") -> Any:
    if key not in data or data[key] in (None, ""):
        raise ValidationFailed(f"missing {prefix}{key}", field=f"{prefix}{key}")
    return data[key]


@dataclass(frozen=True, slots=True)
class Party:
    id_number: str  # NIT / document number without check digit, no dots
    id_type: str = "31"
    check_digit: str = ""
    name: str = ""
    email: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str) -> Party:
        id_number = str(_req(data, "id_number", prefix)).replace(".", "").replace("-", "").strip()
        if not id_number.isalnum():
            raise ValidationFailed(f"{prefix}id_number must be alphanumeric", field=f"{prefix}id_number")
        id_type = str(data.get("id_type", "31"))
        if id_type not in annex.ID_TYPES:
            raise ValidationFailed(f"{prefix}id_type {id_type!r} not in the code list", field=f"{prefix}id_type")
        return cls(id_number, id_type, str(data.get("check_digit", "")), str(data.get("name", "")),
                   str(data.get("email", "")))  # fmt: skip


@dataclass(frozen=True, slots=True)
class TaxLine:
    scheme: str  # "01" IVA, "04" INC, "03" ICA
    rate: Decimal
    taxable: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_extension: Decimal
    taxes: tuple[TaxLine, ...] = ()
    unit_code: str = "94"


@dataclass(frozen=True, slots=True)
class BillingReference:
    number: str
    unique_code: str
    issue_date: dt.date
    scheme_name: str = "CUFE-SHA384"


@dataclass(frozen=True, slots=True)
class PayrollTotals:
    devengados: Decimal
    deducciones: Decimal
    total: Decimal
    period_start: dt.date
    period_end: dt.date


@dataclass(frozen=True, slots=True)
class FiscalDocumentModel:
    family: str
    number: str  # prefix + consecutive, e.g. "SETP990000001"
    issue_date: dt.date
    issue_time: str  # "HH:MM:SS-05:00"
    supplier: Party
    customer: Party
    currency: str = "COP"
    lines: tuple[Line, ...] = ()
    line_extension: Decimal = Decimal(0)
    tax_exclusive: Decimal = Decimal(0)
    tax_inclusive: Decimal = Decimal(0)
    payable: Decimal = Decimal(0)
    billing_reference: BillingReference | None = None
    concept_code: str = ""  # credit/debit note concept
    payroll: PayrollTotals | None = None
    notes: tuple[str, ...] = ()
    fixture_outcome: str = "accept"  # simulator switch (DEV only)
    synthetic: bool = True
    extras: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived
    @property
    def root(self) -> str:
        return annex.family(self.family)[0]

    @property
    def hash_kind(self) -> str:
        return annex.family(self.family)[1]

    @property
    def type_code(self) -> str:
        return annex.family(self.family)[2].value

    def tax_total(self, scheme: str) -> Decimal:
        return sum((t.amount for line in self.lines for t in line.taxes if t.scheme == scheme), Decimal(0))

    # ------------------------------------------------------------------ io
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FiscalDocumentModel:
        if not isinstance(data, dict):
            raise ValidationFailed("document model must be an object")
        family = str(_req(data, "family"))
        annex.family(family)
        try:
            issue_date = dt.date.fromisoformat(str(_req(data, "issue_date")))
        except ValueError:
            raise ValidationFailed("issue_date must be YYYY-MM-DD", field="issue_date") from None
        issue_time = str(_req(data, "issue_time"))
        if len(issue_time) != 14 or issue_time[8] not in "+-":
            raise ValidationFailed("issue_time must be HH:MM:SS±HH:MM (with GMT offset)", field="issue_time")
        lines = []
        for i, raw in enumerate(data.get("lines") or []):
            p = f"lines[{i}]."
            taxes = []
            for j, t in enumerate(raw.get("taxes") or []):
                scheme = str(_req(t, "scheme", f"{p}taxes[{j}]."))
                if scheme not in annex.TAX_SCHEMES:
                    raise ValidationFailed(f"{p}taxes[{j}].scheme {scheme!r} not supported", field=f"{p}taxes")
                taxes.append(TaxLine(scheme, dec(t.get("rate", "0"), f"{p}rate"), dec(_req(t, "taxable", p), f"{p}taxable"),
                                     dec(_req(t, "amount", p), f"{p}amount")))  # fmt: skip
            lines.append(
                Line(str(_req(raw, "description", p)), dec(_req(raw, "quantity", p), f"{p}quantity"),
                     dec(_req(raw, "unit_price", p), f"{p}unit_price"),
                     dec(_req(raw, "line_extension", p), f"{p}line_extension"), tuple(taxes),
                     str(raw.get("unit_code", "94")))  # fmt: skip
            )
        ref = data.get("billing_reference")
        billing = None
        if ref:
            billing = BillingReference(str(_req(ref, "number", "billing_reference.")),
                                       str(_req(ref, "unique_code", "billing_reference.")),
                                       dt.date.fromisoformat(str(_req(ref, "issue_date", "billing_reference."))),
                                       str(ref.get("scheme_name", "CUFE-SHA384")))  # fmt: skip
        pay = data.get("payroll")
        payroll = None
        if pay:
            payroll = PayrollTotals(dec(_req(pay, "devengados", "payroll."), "payroll.devengados"),
                                    dec(_req(pay, "deducciones", "payroll."), "payroll.deducciones"),
                                    dec(_req(pay, "total", "payroll."), "payroll.total"),
                                    dt.date.fromisoformat(str(_req(pay, "period_start", "payroll."))),
                                    dt.date.fromisoformat(str(_req(pay, "period_end", "payroll."))))  # fmt: skip
        outcome = str(data.get("fixture_outcome", "accept"))
        if outcome not in FIXTURE_OUTCOMES:
            raise ValidationFailed(f"fixture_outcome must be one of {', '.join(FIXTURE_OUTCOMES)}", field="fixture_outcome")
        model = cls(
            family=family,
            number=str(_req(data, "number")),
            issue_date=issue_date,
            issue_time=issue_time,
            supplier=Party.from_dict(_req(data, "supplier"), "supplier."),
            customer=Party.from_dict(_req(data, "customer"), "customer."),
            currency=str(data.get("currency", "COP")),
            lines=tuple(lines),
            line_extension=dec(data.get("line_extension", "0"), "line_extension"),
            tax_exclusive=dec(data.get("tax_exclusive", data.get("line_extension", "0")), "tax_exclusive"),
            tax_inclusive=dec(data.get("tax_inclusive", "0"), "tax_inclusive"),
            payable=dec(data.get("payable", "0"), "payable"),
            billing_reference=billing,
            concept_code=str(data.get("concept_code", "")),
            payroll=payroll,
            notes=tuple(str(n) for n in data.get("notes") or ()),
            fixture_outcome=outcome,
            synthetic=bool(data.get("synthetic", True)),
            extras={str(k): str(v) for k, v in (data.get("extras") or {}).items()},
        )
        model.validate()
        return model

    def validate(self) -> None:
        """Arithmetic consistency (lines → totals) and family-specific requirements."""
        if self.family in ("NOMINA", "NOMINA_AJUSTE"):
            if self.payroll is None:
                raise ValidationFailed("payroll totals are required for nómina", field="payroll")
            if self.payroll.devengados - self.payroll.deducciones != self.payroll.total:
                raise ValidationFailed("payroll total must equal devengados − deducciones", field="payroll.total")
            if self.family == "NOMINA_AJUSTE" and self.billing_reference is None:
                raise ValidationFailed("an adjustment needs the referenced payroll document", field="billing_reference")
            return
        if not self.lines:
            raise ValidationFailed("at least one line is required", field="lines")
        for i, line in enumerate(self.lines):
            if (line.quantity * line.unit_price).quantize(CENT) != line.line_extension.quantize(CENT):
                raise ValidationFailed(f"lines[{i}].line_extension must equal quantity × unit_price", field="lines")
        total_lines = sum((line.line_extension for line in self.lines), Decimal(0))
        if total_lines != self.line_extension:
            raise ValidationFailed("line_extension must equal the sum of the lines", field="line_extension")
        taxes = sum((t.amount for line in self.lines for t in line.taxes), Decimal(0))
        if self.tax_exclusive + taxes != self.tax_inclusive:
            raise ValidationFailed("tax_inclusive must equal tax_exclusive + taxes", field="tax_inclusive")
        if self.payable != self.tax_inclusive:
            raise ValidationFailed("payable must equal tax_inclusive (no prepaid/charges in this profile)", field="payable")
        if self.family in ("NC", "ND", "DS_ADJUSTMENT") and self.billing_reference is None:
            raise ValidationFailed("a note needs the referenced document (billing_reference)", field="billing_reference")
        if self.family == "NC" and self.concept_code not in annex.CREDIT_NOTE_CONCEPTS:
            raise ValidationFailed("concept_code must be a credit-note concept", field="concept_code")
        if self.family == "ND" and self.concept_code not in annex.DEBIT_NOTE_CONCEPTS:
            raise ValidationFailed("concept_code must be a debit-note concept", field="concept_code")

    def hash_inputs(self, environment: str, secret: str) -> dict[str, object]:
        """Inputs of the fiscal hash (``secret`` = technical key or software PIN; never persisted)."""
        kind = self.hash_kind
        if kind in ("CUFE", "CUDE"):
            values: dict[str, object] = {
                "NumFac": self.number, "FecFac": self.issue_date.isoformat(), "HorFac": self.issue_time,
                "ValFac": self.line_extension, "ValImp1": self.tax_total("01"), "ValImp2": self.tax_total("04"),
                "ValImp3": self.tax_total("03"), "ValTot": self.payable, "NitOFE": self.supplier.id_number,
                "NumAdq": self.customer.id_number, "TipoAmbiente": environment,
            }  # fmt: skip
            values["ClTec" if kind == "CUFE" else "SoftwarePIN"] = secret
            return values
        if kind == "CUDS":
            return {
                "NumDS": self.number, "FecDS": self.issue_date.isoformat(), "HorDS": self.issue_time,
                "ValDS": self.line_extension, "ValImp": self.tax_total("01"), "ValTol": self.payable,
                "NumSNO": self.customer.id_number, "NITABS": self.supplier.id_number, "SoftwarePIN": secret,
                "TipoAmbiente": environment,
            }  # fmt: skip
        assert self.payroll is not None  # validated above (nómina families)
        return {
            "NumNE": self.number, "FecNE": self.issue_date.isoformat(), "HorNE": self.issue_time,
            "ValDev": self.payroll.devengados, "ValDed": self.payroll.deducciones, "ValTolNE": self.payroll.total,
            "NitNE": self.supplier.id_number, "DocEmp": self.customer.id_number, "TipoXML": self.type_code,
            "SoftwarePIN": secret, "TipoAmbiente": environment,
        }  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        def party(p: Party) -> dict[str, str]:
            return {"id_number": p.id_number, "id_type": p.id_type, "check_digit": p.check_digit, "name": p.name,
                    "email": p.email}  # fmt: skip

        out: dict[str, Any] = {
            "family": self.family, "number": self.number, "issue_date": self.issue_date.isoformat(),
            "issue_time": self.issue_time, "supplier": party(self.supplier), "customer": party(self.customer),
            "currency": self.currency, "line_extension": str(self.line_extension),
            "tax_exclusive": str(self.tax_exclusive), "tax_inclusive": str(self.tax_inclusive),
            "payable": str(self.payable), "concept_code": self.concept_code, "notes": list(self.notes),
            "fixture_outcome": self.fixture_outcome, "synthetic": self.synthetic, "extras": dict(self.extras),
            "lines": [
                {"description": ln.description, "quantity": str(ln.quantity), "unit_price": str(ln.unit_price),
                 "line_extension": str(ln.line_extension), "unit_code": ln.unit_code,
                 "taxes": [{"scheme": t.scheme, "rate": str(t.rate), "taxable": str(t.taxable), "amount": str(t.amount)}
                           for t in ln.taxes]}
                for ln in self.lines
            ],
        }  # fmt: skip
        if self.billing_reference:
            b = self.billing_reference
            out["billing_reference"] = {"number": b.number, "unique_code": b.unique_code,
                                        "issue_date": b.issue_date.isoformat(), "scheme_name": b.scheme_name}  # fmt: skip
        if self.payroll:
            p = self.payroll
            out["payroll"] = {"devengados": str(p.devengados), "deducciones": str(p.deducciones), "total": str(p.total),
                              "period_start": p.period_start.isoformat(), "period_end": p.period_end.isoformat()}  # fmt: skip
        return out
