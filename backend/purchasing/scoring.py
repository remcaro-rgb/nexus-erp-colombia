"""Quote normalization, scoring and the explainable recommendation (F-PUR-CYCLE, D-009, D-010).

Pure functions (no I/O, Decimal only), so the comparison is reproducible and unit-testable.

**Normalization** of every quote line (quote currency → COP at the quote-date rate):

* ``base_qty = quantity × factor_to_base`` (quoted UoM / presentation → base UoM, D-013);
* ``net = quantity × unit_price × (1 − discount%)`` (rounded to the amount precision);
* ``non_recoverable_tax = net × tax%`` when the tax is not recoverable (recoverable VAT is not a cost);
* ``freight``: the explicit line freight, else the header freight allocated by net value (remainder to the
  last line);
* ``landed_cop = (net + non_recoverable_tax + freight) × fx_rate``;
* ``unit_landed_cop = landed_cop / base_qty`` (unit-cost precision, half-up);
* ``comparable_cop = unit_landed_cop × requested base qty`` — every quote is compared on the quantity the
  RFQ asked for, whatever quantity or presentation the vendor offered.

**Scoring** (weights editable per company, sum 100; default 60/20/10/10):

* price ``min(total) / total``; lead time ``(min(lead) + 1) / (lead + 1)``; vendor rating ``rating / 5``;
  payment terms ``terms / max(terms)`` (1 when every vendor offers 0 days);
* points per criterion ``weight × factor`` (4 decimals, half-up); score = Σ points.
* Ties (equal score): lower comparable COP total, then earlier receipt, then vendor code ascending.

Only RECEIVED quotes covering every RFQ line are scored; LATE quotes are excluded unless the RFQ was
reopened (D-010). The recommendation never awards by itself.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from kernel.errors import ValidationFailed
from kernel.money import AMOUNT, QTY, UNIT_COST, q_amount

WEIGHT_KEYS = ("price", "lead_time", "rating", "payment_terms")
DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "price": Decimal(60),
    "lead_time": Decimal(20),
    "rating": Decimal(10),
    "payment_terms": Decimal(10),
}
CRITERION_LABELS = {
    "price": "precio puesto",
    "lead_time": "plazo de entrega",
    "rating": "calificación del proveedor",
    "payment_terms": "condiciones de pago",
}
EXCLUSION_LABELS = {
    "LATE": "cotización tardía, no puntúa (D-010)",
    "WITHDRAWN": "cotización retirada",
    "DISQUALIFIED": "cotización descalificada",
    "INCOMPLETE": "no cubre todas las líneas solicitadas",
}
TIE_RULES = ("menor total puesto en COP", "recepción más temprana", "código de proveedor ascendente")
MAX_RATING = Decimal(5)
FACTOR_PLACES = 4
POINT_PLACES = 4
HUNDRED = Decimal(100)


def _q(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def fmt(value: Decimal, places: int) -> str:
    """Fixed-point text (no exponent, no float)."""
    return f"{_q(value, places):f}"


def validate_weights(raw: Mapping[str, Any] | None) -> dict[str, Decimal]:
    """Weights per D-009 (keys price, lead_time, rating, payment_terms; each ≥ 0; sum = 100)."""
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    unknown = sorted(set(raw) - set(WEIGHT_KEYS))
    if unknown:
        raise ValidationFailed(f"unknown scoring weights: {', '.join(unknown)}", field="scoring_weights")
    out: dict[str, Decimal] = {}
    for key in WEIGHT_KEYS:
        value = raw.get(key, 0)
        if isinstance(value, float | bool):
            raise ValidationFailed("scoring weights must be decimal strings or integers", field="scoring_weights")
        try:
            weight = Decimal(str(value))
        except ArithmeticError as exc:
            raise ValidationFailed(f"invalid weight for {key}", field="scoring_weights") from exc
        if not weight.is_finite() or weight < 0:
            raise ValidationFailed(f"weight for {key} must be ≥ 0", field="scoring_weights")
        out[key] = weight
    if sum(out.values()) != HUNDRED:
        raise ValidationFailed("scoring weights must sum to 100 (D-009)", field="scoring_weights")
    return out


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class RfqLineSpec:
    rfq_line_id: uuid.UUID
    line_no: int
    requested_base_qty: Decimal
    base_uom: str


@dataclass(frozen=True, slots=True)
class QuoteLineInput:
    rfq_line_id: uuid.UUID
    quantity: Decimal
    uom: str
    factor_to_base: Decimal
    unit_price: Decimal
    discount_pct: Decimal = Decimal(0)
    tax_rate_pct: Decimal = Decimal(0)
    tax_recoverable: bool = True
    freight_amount: Decimal | None = None
    quote_line_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class QuoteInput:
    quote_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_code: str
    vendor_name: str
    received_at: dt.datetime
    currency: str
    fx_rate: Decimal
    lead_time_days: int
    payment_terms_days: int
    rating: Decimal
    lines: tuple[QuoteLineInput, ...]
    freight_amount: Decimal = Decimal(0)
    status: str = "RECEIVED"


# --------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class NormalizedLine:
    rfq_line_id: uuid.UUID
    line_no: int
    quote_line_id: uuid.UUID | None
    offered_qty: Decimal
    offered_uom: str
    base_qty: Decimal
    requested_base_qty: Decimal
    net: Decimal
    non_recoverable_tax: Decimal
    freight: Decimal
    landed_currency: Decimal
    landed_cop: Decimal
    unit_landed_cop: Decimal
    comparable_cop: Decimal
    short_quantity: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "rfq_line_id": str(self.rfq_line_id),
            "line_no": self.line_no,
            "quote_line_id": str(self.quote_line_id) if self.quote_line_id else None,
            "offered_qty": fmt(self.offered_qty, QTY),
            "offered_uom": self.offered_uom,
            "base_qty": fmt(self.base_qty, QTY),
            "requested_base_qty": fmt(self.requested_base_qty, QTY),
            "net": fmt(self.net, AMOUNT),
            "non_recoverable_tax": fmt(self.non_recoverable_tax, AMOUNT),
            "freight": fmt(self.freight, AMOUNT),
            "landed_currency": fmt(self.landed_currency, AMOUNT),
            "landed_cop": fmt(self.landed_cop, AMOUNT),
            "unit_landed_cop": fmt(self.unit_landed_cop, UNIT_COST),
            "comparable_cop": fmt(self.comparable_cop, AMOUNT),
            "short_quantity": self.short_quantity,
        }


@dataclass(frozen=True, slots=True)
class NormalizedQuote:
    quote: QuoteInput
    lines: tuple[NormalizedLine, ...]
    comparable_total_cop: Decimal
    lead_time_days: int
    excluded: str | None = None  # LATE | WITHDRAWN | DISQUALIFIED | INCOMPLETE


@dataclass(frozen=True, slots=True)
class ScoredQuote:
    normalized: NormalizedQuote
    factors: dict[str, Decimal]
    points: dict[str, Decimal]
    score: Decimal
    rank: int = 0


@dataclass(frozen=True, slots=True)
class Comparison:
    weights: dict[str, Decimal]
    ranked: tuple[ScoredQuote, ...]
    excluded: tuple[NormalizedQuote, ...]
    recommended: ScoredQuote | None
    tie_rule: str | None
    explanation: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        def quote_json(n: NormalizedQuote) -> dict[str, Any]:
            q = n.quote
            return {
                "quote_id": str(q.quote_id),
                "vendor_id": str(q.vendor_id),
                "vendor_code": q.vendor_code,
                "vendor_name": q.vendor_name,
                "received_at": q.received_at.isoformat(),
                "currency": q.currency,
                "fx_rate": fmt(q.fx_rate, 10),
                "lead_time_days": n.lead_time_days,
                "payment_terms_days": q.payment_terms_days,
                "rating": fmt(q.rating, 2),
                "comparable_total_cop": fmt(n.comparable_total_cop, AMOUNT),
                "lines": [line.to_json() for line in n.lines],
            }

        return {
            "weights": {k: fmt(v, 2) for k, v in self.weights.items()},
            "ranked": [
                {
                    **quote_json(s.normalized),
                    "rank": s.rank,
                    "factors": {k: fmt(v, FACTOR_PLACES) for k, v in s.factors.items()},
                    "points": {k: fmt(v, POINT_PLACES) for k, v in s.points.items()},
                    "score": fmt(s.score, POINT_PLACES),
                }
                for s in self.ranked
            ],
            "excluded": [{**quote_json(n), "reason": n.excluded} for n in self.excluded],
            "recommended_quote_id": str(self.recommended.normalized.quote.quote_id) if self.recommended else None,
            "tie_rule": self.tie_rule,
            "explanation": self.explanation,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- normalization


def _allocate_freight(nets: Sequence[Decimal], explicit: Sequence[Decimal | None], header: Decimal) -> list[Decimal]:
    """Explicit line freight wins; the header freight is spread over the other lines by net value."""
    out = [e if e is not None else Decimal(0) for e in explicit]
    open_idx = [i for i, e in enumerate(explicit) if e is None]
    if header == 0 or not open_idx:
        return out
    base = sum((nets[i] for i in open_idx), Decimal(0))
    allocated = Decimal(0)
    for pos, i in enumerate(open_idx):
        if pos == len(open_idx) - 1:
            share = header - allocated
        elif base == 0:
            share = q_amount(header / len(open_idx))
        else:
            share = q_amount(header * nets[i] / base)
        out[i] = share
        allocated += share
    return out


def normalize_quote(quote: QuoteInput, rfq_lines: Sequence[RfqLineSpec]) -> NormalizedQuote:
    if quote.fx_rate <= 0:
        raise ValidationFailed("fx_rate must be positive", field="fx_rate")
    by_line = {line.rfq_line_id: line for line in quote.lines}
    specs = sorted(rfq_lines, key=lambda s: s.line_no)
    covered = [s for s in specs if s.rfq_line_id in by_line]
    nets: list[Decimal] = []
    for spec in covered:
        ql = by_line[spec.rfq_line_id]
        if ql.quantity <= 0 or ql.factor_to_base <= 0 or ql.unit_price < 0:
            raise ValidationFailed(f"line {spec.line_no}: quantity, factor and price must be positive")
        if not Decimal(0) <= ql.discount_pct <= HUNDRED:
            raise ValidationFailed(f"line {spec.line_no}: discount must be 0–100 %")
        nets.append(q_amount(ql.quantity * ql.unit_price * (HUNDRED - ql.discount_pct) / HUNDRED))
    freights = _allocate_freight(nets, [by_line[s.rfq_line_id].freight_amount for s in covered], quote.freight_amount)
    lines: list[NormalizedLine] = []
    for spec, net, freight in zip(covered, nets, freights, strict=True):
        ql = by_line[spec.rfq_line_id]
        base_qty = _q(ql.quantity * ql.factor_to_base, QTY)
        tax = Decimal(0) if ql.tax_recoverable else q_amount(net * ql.tax_rate_pct / HUNDRED)
        landed_ccy = net + tax + freight
        landed_cop = q_amount(landed_ccy * quote.fx_rate)
        unit = _q(landed_cop / base_qty, UNIT_COST)
        lines.append(
            NormalizedLine(
                rfq_line_id=spec.rfq_line_id,
                line_no=spec.line_no,
                quote_line_id=ql.quote_line_id,
                offered_qty=ql.quantity,
                offered_uom=ql.uom,
                base_qty=base_qty,
                requested_base_qty=spec.requested_base_qty,
                net=net,
                non_recoverable_tax=tax,
                freight=freight,
                landed_currency=landed_ccy,
                landed_cop=landed_cop,
                unit_landed_cop=unit,
                comparable_cop=q_amount(unit * spec.requested_base_qty),
                short_quantity=base_qty < spec.requested_base_qty,
            )
        )
    excluded: str | None = None
    if quote.status in ("LATE", "WITHDRAWN", "DISQUALIFIED"):
        excluded = quote.status
    elif len(covered) != len(specs):
        excluded = "INCOMPLETE"
    return NormalizedQuote(
        quote=quote,
        lines=tuple(lines),
        comparable_total_cop=sum((line.comparable_cop for line in lines), Decimal(0)),
        lead_time_days=quote.lead_time_days,
        excluded=excluded,
    )


# --------------------------------------------------------------------------- scoring


def _ratio(num: Decimal, den: Decimal) -> Decimal:
    if den == 0:
        return Decimal(1)
    return _q(num / den, FACTOR_PLACES)


def score(normalized: Sequence[NormalizedQuote], weights: Mapping[str, Decimal]) -> list[ScoredQuote]:
    eligible = [n for n in normalized if n.excluded is None]
    if not eligible:
        return []
    min_total = min(n.comparable_total_cop for n in eligible)
    min_lead = min(n.lead_time_days for n in eligible)
    max_terms = max(n.quote.payment_terms_days for n in eligible)
    scored = []
    for n in eligible:
        factors = {
            "price": Decimal(1) if n.comparable_total_cop == 0 else _ratio(min_total, n.comparable_total_cop),
            "lead_time": _ratio(Decimal(min_lead + 1), Decimal(n.lead_time_days + 1)),
            "rating": _ratio(n.quote.rating, MAX_RATING),
            "payment_terms": _ratio(Decimal(n.quote.payment_terms_days), Decimal(max_terms))
            if max_terms
            else Decimal(1),
        }
        points = {k: _q(weights[k] * factors[k], POINT_PLACES) for k in WEIGHT_KEYS}
        scored.append(ScoredQuote(n, factors, points, sum(points.values(), Decimal(0))))
    scored.sort(
        key=lambda s: (
            -s.score,
            s.normalized.comparable_total_cop,
            s.normalized.quote.received_at,
            s.normalized.quote.vendor_code,
        )
    )
    return [ScoredQuote(s.normalized, s.factors, s.points, s.score, rank=i + 1) for i, s in enumerate(scored)]


def _tie_rule(ranked: Sequence[ScoredQuote]) -> tuple[str | None, list[ScoredQuote]]:
    if len(ranked) < 2 or ranked[0].score != ranked[1].score:
        return None, []
    tied = [s for s in ranked if s.score == ranked[0].score]
    a, b = tied[0].normalized, tied[1].normalized
    if a.comparable_total_cop != b.comparable_total_cop:
        return TIE_RULES[0], tied
    if a.quote.received_at != b.quote.received_at:
        return TIE_RULES[1], tied
    return TIE_RULES[2], tied


def explain(
    weights: Mapping[str, Decimal],
    ranked: Sequence[ScoredQuote],
    excluded: Sequence[NormalizedQuote],
    tie_rule: str | None,
    tied: Sequence[ScoredQuote],
) -> str:
    out: list[str] = []
    if ranked:
        top = ranked[0]
        out.append(
            f"Recomendación: {top.normalized.quote.vendor_code} — {top.normalized.quote.vendor_name} "
            f"con {fmt(top.score, POINT_PLACES)} puntos de 100."
        )
    else:
        out.append("Sin recomendación: ninguna cotización elegible.")
    out.append(
        "Criterios (D-009): " + "; ".join(f"{CRITERION_LABELS[k]} {fmt(weights[k], 2)}" for k in WEIGHT_KEYS) + "."
    )
    for s in ranked:
        n = s.normalized
        q = n.quote
        out.append(
            f"{s.rank}. {q.vendor_code}: total puesto {fmt(n.comparable_total_cop, 2)} COP; "
            f"precio {fmt(weights['price'], 2)} × {fmt(s.factors['price'], FACTOR_PLACES)} = "
            f"{fmt(s.points['price'], POINT_PLACES)}; "
            f"plazo {n.lead_time_days} días: {fmt(weights['lead_time'], 2)} × "
            f"{fmt(s.factors['lead_time'], FACTOR_PLACES)} = {fmt(s.points['lead_time'], POINT_PLACES)}; "
            f"calificación {fmt(q.rating, 2)}/5: {fmt(weights['rating'], 2)} × "
            f"{fmt(s.factors['rating'], FACTOR_PLACES)} = {fmt(s.points['rating'], POINT_PLACES)}; "
            f"pago a {q.payment_terms_days} días: {fmt(weights['payment_terms'], 2)} × "
            f"{fmt(s.factors['payment_terms'], FACTOR_PLACES)} = {fmt(s.points['payment_terms'], POINT_PLACES)}; "
            f"puntaje {fmt(s.score, POINT_PLACES)}."
        )
    if tie_rule:
        codes = ", ".join(s.normalized.quote.vendor_code for s in tied)
        out.append(f"Empate en {fmt(tied[0].score, POINT_PLACES)} puntos entre {codes}: desempate por {tie_rule}.")
    for n in sorted(excluded, key=lambda x: x.quote.vendor_code):
        out.append(f"Excluida: {n.quote.vendor_code} ({EXCLUSION_LABELS[n.excluded or 'DISQUALIFIED']}).")
    out.append("La recomendación no adjudica: la adjudicación requiere motivo si difiere y aprobación.")
    return "\n".join(out)


def compare(
    quotes: Sequence[QuoteInput], rfq_lines: Sequence[RfqLineSpec], weights: Mapping[str, Any] | None = None
) -> Comparison:
    w = validate_weights(weights)
    normalized = [normalize_quote(q, rfq_lines) for q in quotes]
    ranked = score(normalized, w)
    excluded = tuple(n for n in normalized if n.excluded is not None)
    tie_rule, tied = _tie_rule(ranked)
    warnings = []
    if len(ranked) < 3:
        warnings.append("fewer_than_3_quotes")
    if any(line.short_quantity for s in ranked for line in s.normalized.lines):
        warnings.append("short_quantity_offered")
    return Comparison(
        weights=w,
        ranked=tuple(ranked),
        excluded=excluded,
        recommended=ranked[0] if ranked else None,
        tie_rule=tie_rule,
        explanation=explain(w, ranked, excluded, tie_rule, tied),
        warnings=tuple(warnings),
    )
