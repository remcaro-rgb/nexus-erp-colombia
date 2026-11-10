"""The tax engine (P06): VAT/INC, ICA, ReteFuente, ReteIVA, ReteICA with an explanation trace.

Pure calculation: no writes, callable inside or outside a unit of work. Every
rate, base and threshold comes from the parameter provider (C1-9): a missing or
UNKNOWN value raises ``tax_parameter_unknown`` (fail closed, D-029); DISPUTED or
synthetic values compute, add a warning and are kept in ``parameter_snapshots``.

Rounding (shared/07 §8, D-001): taxes are computed per line and summed (mode
``LINE``, default) or rounded once per document (mode ``DOCUMENT``); in
``DOCUMENT`` mode the difference between the document total and the sum of the
rounded lines is returned as ``rounding_adjustment`` (the ROUNDING line, posted
by the caller), limited to a tolerance (default COP 1.00).

Who withholds: the **buyer** (the payer) practices withholdings on the
**seller**. Applicability is decided by the matrix rules of each tax code; a
withholding concept without its own rules uses :data:`DEFAULT_RETEFUENTE_RULES`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from kernel.errors import ParameterUnknown, ValidationFailed
from kernel.parameters import ParameterValue
from tax.engine import matrix
from tax.engine.types import (
    DocumentInput,
    DocumentLine,
    LineTax,
    TaxCodeDef,
    TaxResult,
    TraceStep,
    WithholdingConceptDef,
)
from tax.errors import TaxCodeInactive, TaxParameterUnknown

CENT = Decimal("0.01")
DEFAULT_ROUNDING_TOLERANCE = Decimal("1.00")
UVT_CODE = "uvt"

# Data, not law: the payer withholds income tax only when it is a withholding
# agent and the payee is not an autorretenedor. Tenants override per tax code.
DEFAULT_RETEFUENTE_RULES: tuple[dict[str, Any], ...] = (
    {"when": {"seller.autorretenedor": True}, "apply": False, "reason": "el vendedor es autorretenedor"},
    {"when": {"buyer.withholding_agent": True}, "apply": True, "reason": "el comprador es agente de retención"},
    {"apply": False, "reason": "el comprador no es agente de retención"},
)


class ParameterSource(Protocol):
    def get(self, code: str, at_date: dt.date, company: uuid.UUID) -> ParameterValue: ...


def round_cop(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


class _Run:
    """Collects snapshots, warnings and the trace of one computation."""

    def __init__(self, parameters: ParameterSource, doc: DocumentInput) -> None:
        self.parameters = parameters
        self.doc = doc
        self.snapshots: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.warnings: list[str] = []
        self.trace: list[TraceStep] = []

    def step(self, step: str, line: DocumentLine | None, code: str | None, detail: str, **data: Any) -> None:
        self.trace.append(TraceStep(step, line.line_ref if line else None, code, detail, data))

    def param(self, code: str, line: DocumentLine | None, tax_code: str | None, purpose: str) -> ParameterValue:
        try:
            value = self.parameters.get(code, self.doc.document_date, self.doc.company_id)
        except ParameterUnknown as exc:
            raise TaxParameterUnknown(
                f"parámetro {code} desconocido para {self.doc.document_date.isoformat()} ({purpose})",
                parameter=code,
                date=self.doc.document_date.isoformat(),
                purpose=purpose,
            ) from exc
        snap = value.snapshot()
        key = (code, snap.get("valid_from"))
        if key not in self.snapshots:
            self.snapshots[key] = snap
            if value.synthetic:
                self.warnings.append(f"{code}: FIXTURE — NOT LEGALLY VERIFIED")
            elif value.requires_warning:
                self.warnings.append(f"{code}: DISPUTED — se guardó la instantánea del parámetro")
        shown = "tabla" if isinstance(value.value, (list, dict)) else value.value
        self.step(
            "parameter", line, tax_code, f"{purpose}: {code} = {shown}",
            parameter=code, value=str(value.value), status=value.status.value, source_id=value.source_id,
            valid_from=snap.get("valid_from"), valid_to=snap.get("valid_to"), synthetic=value.synthetic,
        )  # fmt: skip
        return value

    def decimal_param(self, code: str, line: DocumentLine | None, tax_code: str | None, purpose: str) -> Decimal:
        value = self.param(code, line, tax_code, purpose).value
        if not isinstance(value, Decimal):
            raise TaxParameterUnknown(f"parámetro {code} no es numérico", parameter=code)
        return value


class TaxEngine:
    def __init__(
        self,
        parameters: ParameterSource,
        tax_codes: Mapping[str, TaxCodeDef],
        concepts: Mapping[str, WithholdingConceptDef] | None = None,
        ica_tariffs: Mapping[tuple[str, str], str] | None = None,
        rounding_tolerance: Decimal = DEFAULT_ROUNDING_TOLERANCE,
    ) -> None:
        self.parameters = parameters
        self.tax_codes = dict(tax_codes)
        self.concepts = dict(concepts or {})
        self.ica_tariffs = dict(ica_tariffs or {})  # (municipality DANE code, CIIU) -> rate parameter code
        self.rounding_tolerance = rounding_tolerance

    # ------------------------------------------------------------------ helpers
    def _code(self, code: str) -> TaxCodeDef:
        definition = self.tax_codes.get(code)
        if definition is None:
            raise ValidationFailed(f"código de impuesto desconocido {code!r}", field="tax_codes")
        if not definition.active:
            raise TaxCodeInactive(f"código de impuesto inactivo {code!r}", field="tax_codes")
        return definition

    def _min_base(self, run: _Run, uvt_param: str, line: DocumentLine, code: str) -> Decimal:
        uvt_count = run.decimal_param(uvt_param, line, code, "base mínima en UVT")
        uvt = run.decimal_param(UVT_CODE, line, code, "valor de la UVT")
        min_base = uvt_count * uvt
        run.step(
            "min_base",
            line,
            code,
            f"base mínima = {uvt_count} UVT × {uvt} = {min_base}",
            parameter=uvt_param,
            uvt=str(uvt),
            uvt_count=str(uvt_count),
            min_base=str(min_base),
        )
        return min_base

    # ------------------------------------------------------------------ main
    def compute(self, doc: DocumentInput) -> TaxResult:
        if doc.rounding_mode not in ("LINE", "DOCUMENT"):
            raise ValidationFailed("rounding_mode must be LINE or DOCUMENT", field="rounding_mode")
        run = _Run(self.parameters, doc)
        run.step("document", None, None,
                 f"documento {doc.document_date.isoformat()} municipio={doc.municipality or '-'} "
                 f"redondeo={doc.rounding_mode}",
                 seller=_profile_json(doc.seller), buyer=_profile_json(doc.buyer))  # fmt: skip
        results: list[tuple[LineTax, Decimal]] = []  # (tax, unrounded)
        line_taxes: dict[str, dict[str, Decimal]] = defaultdict(dict)  # line_ref -> tax_type -> unrounded amount

        # Phase A — taxes on the sale (IVA, INC, ICA, OTHER)
        for line in doc.lines:
            for code in line.tax_codes:
                definition = self._code(code)
                if definition.withholding:
                    continue
                decision = matrix.decide(definition.applicability, doc, line)
                run.step(
                    "applicability",
                    line,
                    code,
                    decision.reason,
                    applies=decision.applies,
                    rule_index=decision.rule_index,
                )
                if not decision.applies:
                    continue
                params = [definition.rate_parameter_code]
                rate = run.decimal_param(definition.rate_parameter_code, line, code, "tarifa")
                base = line.base_amount
                if definition.base_rule == "PERCENT_OF_BASE":
                    pct = run.decimal_param(definition.base_percent_parameter_code, line, code, "porcentaje de la base")
                    params.append(definition.base_percent_parameter_code)
                    base = base * pct
                    run.step(
                        "base",
                        line,
                        code,
                        f"base = {line.base_amount} × {pct} = {base}",
                        parameter=definition.base_percent_parameter_code,
                    )
                unrounded = base * rate
                line_taxes[line.line_ref][definition.tax_type] = (
                    line_taxes[line.line_ref].get(definition.tax_type, Decimal(0)) + unrounded
                )
                tax = LineTax(line.line_ref, code, definition.tax_type, base, rate, unrounded, round_cop(unrounded),
                              False, tuple(params), definition.account_role)  # fmt: skip
                run.step(
                    "amount",
                    line,
                    code,
                    f"{base} × {rate} = {unrounded} → {tax.amount}",
                    base=str(base),
                    rate=str(rate),
                    unrounded=str(unrounded),
                    amount=str(tax.amount),
                )
                results.append((tax, unrounded))

        # Phase B — withholdings practiced by the buyer
        results += self._withholdings(run, doc, line_taxes)
        return self._finish(run, doc, results)

    def _withholdings(
        self, run: _Run, doc: DocumentInput, line_taxes: dict[str, dict[str, Decimal]]
    ) -> list[tuple[LineTax, Decimal]]:
        # candidates: (line, key, definition-like, rules, base, rate_param, min_base_param)
        candidates: list[dict[str, Any]] = []
        for line in doc.lines:
            if line.concept:
                concept = self.concepts.get(line.concept)
                if concept is None:
                    raise ValidationFailed(f"concepto de retención desconocido {line.concept!r}", field="concept")
                if not concept.active:
                    raise TaxCodeInactive(f"concepto de retención inactivo {line.concept!r}", field="concept")
                candidates.append({"line": line, "code": concept.code, "tax_type": concept.tax_type,
                                   "rules": DEFAULT_RETEFUENTE_RULES, "base_rule": "NET", "base_tax_type": "",
                                   "rate_param": concept.rate_parameter_code, "min_param": concept.base_parameter_code,
                                   "account_role": f"WITHHOLDING_{concept.tax_type}"})  # fmt: skip
            for code in line.tax_codes:
                definition = self._code(code)
                if not definition.withholding:
                    continue
                candidates.append({"line": line, "code": code, "tax_type": definition.tax_type,
                                   "rules": definition.applicability or (
                                       DEFAULT_RETEFUENTE_RULES if definition.tax_type == "RETEFUENTE" else ()),
                                   "base_rule": definition.base_rule, "base_tax_type": definition.base_tax_type,
                                   "rate_param": definition.rate_parameter_code,
                                   "min_param": definition.min_base_uvt_parameter_code,
                                   "account_role": definition.account_role})  # fmt: skip

        applicable: list[dict[str, Any]] = []
        for cand in candidates:
            line = cand["line"]  # a DocumentLine of doc.lines (candidates built above)
            decision = matrix.decide(tuple(cand["rules"]), doc, line)
            run.step(
                "applicability",
                line,
                cand["code"],
                decision.reason,
                applies=decision.applies,
                rule_index=decision.rule_index,
            )
            if not decision.applies:
                continue
            if cand["base_rule"] == "TAX_AMOUNT":
                base = line_taxes.get(line.line_ref, {}).get(cand["base_tax_type"], Decimal(0))
                run.step(
                    "base", line, cand["code"], f"base = {cand['base_tax_type']} de la línea = {base}", base=str(base)
                )
                if base == 0:
                    continue
            else:
                base = line.base_amount
            cand["base"] = base
            applicable.append(cand)

        # Minimum bases are compared with the payment amount of the document per withholding code.
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for cand in applicable:
            totals[cand["code"]] += cand["base"]
        out: list[tuple[LineTax, Decimal]] = []
        checked: dict[str, bool] = {}
        for cand in applicable:
            line, code = cand["line"], cand["code"]
            params: list[str] = []
            if cand["min_param"]:
                if code not in checked:
                    min_base = self._min_base(run, cand["min_param"], line, code)
                    checked[code] = totals[code] >= min_base
                    run.step("min_base_check", line, code,
                             f"base del documento {totals[code]} {'≥' if checked[code] else '<'} mínimo {min_base}",
                             document_base=str(totals[code]), min_base=str(min_base), applies=checked[code],
                             parameter=cand["min_param"])  # fmt: skip
                params += [cand["min_param"], UVT_CODE]
                if not checked[code]:
                    continue
            rate_param = cand["rate_param"]
            if cand["tax_type"] == "RETEICA" and not rate_param:
                rate_param = self._ica_rate_param(run, doc, line, code)
            if not rate_param:
                raise TaxParameterUnknown(f"{code}: sin parámetro de tarifa", parameter=code)
            rate = run.decimal_param(rate_param, line, code, "tarifa de retención")
            params.insert(0, rate_param)
            unrounded = cand["base"] * rate
            tax = LineTax(line.line_ref, code, cand["tax_type"], cand["base"], rate, unrounded, round_cop(unrounded),
                          True, tuple(params), cand["account_role"])  # fmt: skip
            run.step(
                "amount",
                line,
                code,
                f"{cand['base']} × {rate} = {unrounded} → {tax.amount}",
                base=str(cand["base"]),
                rate=str(rate),
                unrounded=str(unrounded),
                amount=str(tax.amount),
            )
            out.append((tax, unrounded))
        return out

    def _ica_rate_param(self, run: _Run, doc: DocumentInput, line: DocumentLine, code: str) -> str:
        key = (doc.municipality or "", line.activity_code or "")
        rate_param = self.ica_tariffs.get(key)
        run.step(
            "ica_tariff",
            line,
            code,
            f"tarifa ICA municipio={key[0] or '-'} actividad={key[1] or '-'}",
            municipality=key[0],
            activity=key[1],
            parameter=rate_param,
        )
        if not rate_param:
            raise TaxParameterUnknown(
                f"no hay tarifa ICA cargada para el municipio {key[0] or '-'} y la actividad {key[1] or '-'} (B-13)",
                parameter=f"ica:{key[0]}:{key[1]}",
            )
        return rate_param

    def _finish(self, run: _Run, doc: DocumentInput, results: list[tuple[LineTax, Decimal]]) -> TaxResult:
        totals: dict[str, Decimal] = {}
        unrounded_by_code: dict[str, Decimal] = defaultdict(Decimal)
        rounded_by_code: dict[str, Decimal] = defaultdict(Decimal)
        withholding_codes: set[str] = set()
        for tax, unrounded in results:
            unrounded_by_code[tax.tax_code] += unrounded
            rounded_by_code[tax.tax_code] += tax.amount
            if tax.withholding:
                withholding_codes.add(tax.tax_code)
        adjustment = Decimal("0.00")
        for code in unrounded_by_code:
            # Withholdings are always the sum of the rounded lines (one certificate line each).
            if doc.rounding_mode == "DOCUMENT" and code not in withholding_codes:
                totals[code] = round_cop(unrounded_by_code[code])
                diff = totals[code] - rounded_by_code[code]
                if diff:
                    run.step("rounding", None, code,
                             f"total documento {totals[code]} − Σ líneas {rounded_by_code[code]} = {diff}",
                             adjustment=str(diff))  # fmt: skip
                adjustment += diff
            else:
                totals[code] = rounded_by_code[code]
        if abs(adjustment) > self.rounding_tolerance:
            raise ValidationFailed(
                f"ajuste de redondeo {adjustment} supera la tolerancia {self.rounding_tolerance}",
                code="rounding_tolerance_exceeded",
            )
        tax_total = sum((v for k, v in totals.items() if k not in withholding_codes), Decimal("0.00"))
        withholding_total = sum((v for k, v in totals.items() if k in withholding_codes), Decimal("0.00"))
        run.step(
            "totals",
            None,
            None,
            f"impuestos {tax_total}; retenciones {withholding_total}",
            tax_total=str(tax_total),
            withholding_total=str(withholding_total),
            adjustment=str(adjustment),
        )
        return TaxResult(
            lines=tuple(t for t, _ in results),
            totals=totals,
            tax_total=tax_total,
            withholding_total=withholding_total,
            rounding_adjustment=adjustment,
            parameter_snapshots=tuple(run.snapshots.values()),
            warnings=tuple(run.warnings),
            trace=tuple(run.trace),
            rounding_mode=doc.rounding_mode,
        )


def _profile_json(p: Any) -> dict[str, Any]:
    return {"person_type": p.person_type, "regime": p.regime, "gran_contribuyente": p.gran_contribuyente,
            "autorretenedor": p.autorretenedor, "withholding_agent": p.withholding_agent,
            "vat_withholding_agent": p.vat_withholding_agent}  # fmt: skip
