"""Unit tests of the quote normalization and scoring engine (no database).

T-PUR-CYCLE-02: UoM/presentation → base, FX at the quote date, discounts, non-recoverable taxes and
freight allocation, checked against a golden comparison table computed by hand.
T-PUR-CYCLE-03: scoring and tie rules are deterministic and explainable (golden explanation text).
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kernel.errors import ValidationFailed
from purchasing import scoring
from purchasing.scoring import QuoteInput, QuoteLineInput, RfqLineSpec

L1, L2 = uuid.UUID(int=1), uuid.UUID(int=2)
T0 = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)


def quote(
    code: str,
    lines: list[QuoteLineInput],
    *,
    currency: str = "COP",
    fx: str = "1",
    lead: int = 5,
    terms: int = 30,
    rating: str = "3",
    freight: str = "0",
    minutes: int = 0,
    status: str = "RECEIVED",
) -> QuoteInput:
    return QuoteInput(
        quote_id=uuid.uuid5(uuid.NAMESPACE_OID, code),
        vendor_id=uuid.uuid5(uuid.NAMESPACE_DNS, code),
        vendor_code=code,
        vendor_name=f"Proveedor {code[-1]} (SINTÉTICO)",
        received_at=T0 + dt.timedelta(minutes=minutes),
        currency=currency,
        fx_rate=Decimal(fx),
        lead_time_days=lead,
        payment_terms_days=terms,
        rating=Decimal(rating),
        lines=tuple(lines),
        freight_amount=Decimal(freight),
        status=status,
    )


def qline(
    rfq_line: uuid.UUID,
    qty: str,
    uom: str,
    price: str,
    *,
    factor: str = "1",
    discount: str = "0",
    tax: str = "0",
    recoverable: bool = True,
    freight: str | None = None,
) -> QuoteLineInput:
    return QuoteLineInput(
        rfq_line_id=rfq_line,
        quantity=Decimal(qty),
        uom=uom,
        factor_to_base=Decimal(factor),
        unit_price=Decimal(price),
        discount_pct=Decimal(discount),
        tax_rate_pct=Decimal(tax),
        tax_recoverable=recoverable,
        freight_amount=None if freight is None else Decimal(freight),
    )


SPECS = [RfqLineSpec(L1, 1, Decimal("120"), "KG"), RfqLineSpec(L2, 2, Decimal("24"), "UND")]


def golden_quotes() -> list[QuoteInput]:
    return [
        # COP; L1 in bags of 25 kg with 10 % discount (recoverable VAT), L2 with non-recoverable 19 % VAT;
        # header freight 50,000 COP spread by net value.
        quote(
            "PROV-A",
            [
                qline(L1, "5", "BULTO25", "100000", factor="25", discount="10", tax="19", recoverable=True),
                qline(L2, "24", "UND", "15000", tax="19", recoverable=False),
            ],
            freight="50000",
        ),
        # USD at the quote-date rate 4,000; L1 with explicit line freight; L2 in boxes of 12 with 5 % discount,
        # non-recoverable VAT and the header freight (10 USD) as the only line without explicit freight.
        quote(
            "PROV-B",
            [
                qline(L1, "120", "KG", "0.95", freight="5"),
                qline(L2, "2", "CAJA12", "40", factor="12", discount="5", tax="19", recoverable=False),
            ],
            currency="USD",
            fx="4000",
            freight="10",
            minutes=1,
        ),
        # Covers only L1 and offers less than requested: compared per base unit, excluded as INCOMPLETE.
        quote("PROV-C", [qline(L1, "100", "KG", "3500")], minutes=2),
    ]


GOLDEN_TABLE = {
    "PROV-A": {
        "comparable_total_cop": "909288.8889",
        "lines": [
            {
                "line_no": 1,
                "base_qty": "125.000000",
                "net": "450000.0000",
                "non_recoverable_tax": "0.0000",
                "freight": "27777.7778",
                "landed_cop": "477777.7778",
                "unit_landed_cop": "3822.22222240",
                "comparable_cop": "458666.6667",
                "short_quantity": False,
            },
            {
                "line_no": 2,
                "base_qty": "24.000000",
                "net": "360000.0000",
                "non_recoverable_tax": "68400.0000",
                "freight": "22222.2222",
                "landed_cop": "450622.2222",
                "unit_landed_cop": "18775.92592500",
                "comparable_cop": "450622.2222",
                "short_quantity": False,
            },
        ],
    },
    "PROV-B": {
        "comparable_total_cop": "877760.0000",
        "lines": [
            {
                "line_no": 1,
                "base_qty": "120.000000",
                "net": "114.0000",
                "non_recoverable_tax": "0.0000",
                "freight": "5.0000",
                "landed_cop": "476000.0000",
                "unit_landed_cop": "3966.66666667",
                "comparable_cop": "476000.0000",
                "short_quantity": False,
            },
            {
                "line_no": 2,
                "base_qty": "24.000000",
                "net": "76.0000",
                "non_recoverable_tax": "14.4400",
                "freight": "10.0000",
                "landed_cop": "401760.0000",
                "unit_landed_cop": "16740.00000000",
                "comparable_cop": "401760.0000",
                "short_quantity": False,
            },
        ],
    },
    "PROV-C": {
        "comparable_total_cop": "420000.0000",
        "lines": [
            {
                "line_no": 1,
                "base_qty": "100.000000",
                "net": "350000.0000",
                "non_recoverable_tax": "0.0000",
                "freight": "0.0000",
                "landed_cop": "350000.0000",
                "unit_landed_cop": "3500.00000000",
                "comparable_cop": "420000.0000",
                "short_quantity": True,
            },
        ],
    },
}
KEYS = (
    "line_no",
    "base_qty",
    "net",
    "non_recoverable_tax",
    "freight",
    "landed_cop",
    "unit_landed_cop",
    "comparable_cop",
    "short_quantity",
)


def test_T_PUR_CYCLE_02_normalization_golden_comparison_table() -> None:
    comparison = scoring.compare(golden_quotes(), SPECS)
    data = comparison.to_json()
    rows = {r["vendor_code"]: r for r in [*data["ranked"], *data["excluded"]]}
    for code, expected in GOLDEN_TABLE.items():
        assert rows[code]["comparable_total_cop"] == expected["comparable_total_cop"], code
        got = [{k: line[k] for k in KEYS} for line in rows[code]["lines"]]
        assert got == expected["lines"], code
    # PROV-C is excluded (does not cover line 2); PROV-B (USD, cheaper landed in COP) ranks before PROV-A.
    assert [r["vendor_code"] for r in data["ranked"]] == ["PROV-B", "PROV-A"]
    assert data["excluded"][0]["vendor_code"] == "PROV-C" and data["excluded"][0]["reason"] == "INCOMPLETE"
    assert rows["PROV-B"]["fx_rate"] == "4000.0000000000"
    assert "fewer_than_3_quotes" in data["warnings"]


def test_T_PUR_CYCLE_02_freight_allocation_keeps_the_header_total_exactly() -> None:
    # 100 split by net 1:1:1 → 33.3333 + 33.3333 + remainder 33.3334 (never more, never less).
    shares = scoring._allocate_freight([Decimal(10)] * 3, [None, None, None], Decimal(100))
    assert shares == [Decimal("33.3333"), Decimal("33.3333"), Decimal("33.3334")]
    assert sum(shares) == Decimal(100)


def test_T_PUR_CYCLE_02_presentation_factor_and_currency_are_validated() -> None:
    bad = quote("PROV-X", [qline(L1, "1", "KG", "1", factor="0"), qline(L2, "1", "UND", "1")])
    with pytest.raises(ValidationFailed):
        scoring.compare([bad], SPECS)
    with pytest.raises(ValidationFailed):
        scoring.compare([quote("PROV-Y", [qline(L1, "1", "KG", "1")], fx="0")], SPECS)


@given(
    qty=st.integers(min_value=1, max_value=500),
    factor=st.sampled_from(["1", "6", "12", "25", "0.5"]),
    price=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000"), places=2),
    fx=st.sampled_from(["1", "3950.5", "4321.1234"]),
)
def test_T_PUR_CYCLE_02_unit_price_per_base_is_independent_of_the_presentation(
    qty: int, factor: str, price: Decimal, fx: str
) -> None:
    """Quoting N presentations of factor f at f×p is the same base-unit landed price as N×f base units at p."""
    spec = [RfqLineSpec(L1, 1, Decimal("60"), "KG")]
    f = Decimal(factor)
    in_presentation = scoring.normalize_quote(
        quote("PROV-P", [qline(L1, str(qty), "PRES", str(price * f), factor=factor)], fx=fx), spec
    )
    in_base = scoring.normalize_quote(quote("PROV-Q", [qline(L1, str(qty * f), "KG", str(price))], fx=fx), spec)
    a, b = in_presentation.lines[0], in_base.lines[0]
    assert a.base_qty == b.base_qty
    assert abs(a.unit_landed_cop - b.unit_landed_cop) <= Decimal("0.0001") / a.base_qty + Decimal("0.00000001")


# --------------------------------------------------------------------------- T-PUR-CYCLE-03


def single(
    code: str, unit: str, *, lead: int, rating: str, terms: int, minutes: int, status: str = "RECEIVED"
) -> QuoteInput:
    return quote(
        code, [qline(L1, "10", "UND", unit)], lead=lead, rating=rating, terms=terms, minutes=minutes, status=status
    )


SPEC_10 = [RfqLineSpec(L1, 1, Decimal("10"), "UND")]


def scenario_received_first() -> list[QuoteInput]:
    return [
        single("PROV-A", "1000", lead=4, rating="4", terms=30, minutes=60),
        single("PROV-B", "1000", lead=4, rating="4", terms=30, minutes=0),
        single("PROV-C", "1250", lead=5, rating="3", terms=60, minutes=120),
        single("PROV-D", "500", lead=1, rating="5", terms=90, minutes=999, status="LATE"),
    ]


GOLDEN_EXPLANATION = "\n".join(
    [
        "Recomendación: PROV-B — Proveedor B (SINTÉTICO) con 93.0000 puntos de 100.",
        "Criterios (D-009): precio puesto 60.00; plazo de entrega 20.00; calificación del proveedor 10.00; "
        "condiciones de pago 10.00.",
        "1. PROV-B: total puesto 10000.00 COP; precio 60.00 × 1.0000 = 60.0000; "
        "plazo 4 días: 20.00 × 1.0000 = 20.0000; "
        "calificación 4.00/5: 10.00 × 0.8000 = 8.0000; pago a 30 días: 10.00 × 0.5000 = 5.0000; puntaje 93.0000.",
        "2. PROV-A: total puesto 10000.00 COP; precio 60.00 × 1.0000 = 60.0000; "
        "plazo 4 días: 20.00 × 1.0000 = 20.0000; "
        "calificación 4.00/5: 10.00 × 0.8000 = 8.0000; pago a 30 días: 10.00 × 0.5000 = 5.0000; puntaje 93.0000.",
        "3. PROV-C: total puesto 12500.00 COP; precio 60.00 × 0.8000 = 48.0000; "
        "plazo 5 días: 20.00 × 0.8333 = 16.6660; "
        "calificación 3.00/5: 10.00 × 0.6000 = 6.0000; pago a 60 días: 10.00 × 1.0000 = 10.0000; puntaje 80.6660.",
        "Empate en 93.0000 puntos entre PROV-B, PROV-A: desempate por recepción más temprana.",
        "Excluida: PROV-D (cotización tardía, no puntúa (D-010)).",
        "La recomendación no adjudica: la adjudicación requiere motivo si difiere y aprobación.",
    ]
)


def test_T_PUR_CYCLE_03_golden_explanation_and_earlier_receipt_tie_rule() -> None:
    result = scoring.compare(scenario_received_first(), SPEC_10)
    assert result.explanation == GOLDEN_EXPLANATION
    assert result.recommended is not None and result.recommended.normalized.quote.vendor_code == "PROV-B"
    assert result.tie_rule == "recepción más temprana"
    assert [s.score for s in result.ranked] == [Decimal("93.0000"), Decimal("93.0000"), Decimal("80.6660")]
    assert [e.quote.vendor_code for e in result.excluded] == ["PROV-D"]  # LATE never scores (D-010)


def test_T_PUR_CYCLE_03_tie_on_score_is_won_by_the_lower_landed_total() -> None:
    weights = {"price": 50, "lead_time": 50, "rating": 0, "payment_terms": 0}
    quotes = [
        single("PROV-F", "2000", lead=0, rating="3", terms=30, minutes=0),  # price 0.5 → 25 + lead 1 → 50 = 75
        single("PROV-E", "1000", lead=1, rating="3", terms=30, minutes=5),  # price 1 → 50 + lead 0.5 → 25 = 75
    ]
    result = scoring.compare(quotes, SPEC_10, weights)
    assert [s.normalized.quote.vendor_code for s in result.ranked] == ["PROV-E", "PROV-F"]
    assert result.ranked[0].score == result.ranked[1].score == Decimal("75.0000")
    assert result.tie_rule == "menor total puesto en COP"
    assert (
        "Empate en 75.0000 puntos entre PROV-E, PROV-F: desempate por menor total puesto en COP." in result.explanation
    )


def test_T_PUR_CYCLE_03_full_tie_is_won_by_the_vendor_code() -> None:
    quotes = [
        single("PROV-Z", "1000", lead=3, rating="4", terms=30, minutes=0),
        single("PROV-M", "1000", lead=3, rating="4", terms=30, minutes=0),
    ]
    result = scoring.compare(quotes, SPEC_10)
    assert [s.normalized.quote.vendor_code for s in result.ranked] == ["PROV-M", "PROV-Z"]
    assert result.tie_rule == "código de proveedor ascendente"


def test_T_PUR_CYCLE_03_deterministic_whatever_the_input_order() -> None:
    base = scoring.compare(scenario_received_first(), SPEC_10)
    rng = random.Random(20261109)
    for _ in range(20):
        shuffled = scenario_received_first()
        rng.shuffle(shuffled)
        again = scoring.compare(shuffled, SPEC_10)
        assert again.explanation == base.explanation
        assert again.to_json() == base.to_json()


def test_T_PUR_CYCLE_03_weights_must_sum_100_and_be_known() -> None:
    with pytest.raises(ValidationFailed):
        scoring.validate_weights({"price": 60, "lead_time": 20, "rating": 10, "payment_terms": 5})
    with pytest.raises(ValidationFailed):
        scoring.validate_weights({"price": 100, "color": 0})
    with pytest.raises(ValidationFailed):
        scoring.validate_weights({"price": 60.0, "lead_time": 20, "rating": 10, "payment_terms": 10})
    assert scoring.validate_weights(None) == scoring.DEFAULT_WEIGHTS


@given(
    st.lists(
        st.tuples(st.integers(1, 5000), st.integers(0, 30), st.integers(0, 5), st.integers(0, 120)),
        min_size=1,
        max_size=6,
    )
)
def test_T_PUR_CYCLE_03_scores_are_bounded_and_the_recommendation_is_the_top_score(
    rows: list[tuple[int, int, int, int]],
) -> None:
    quotes = [
        single(f"PROV-{i:02d}", str(p), lead=lead, rating=str(r), terms=t, minutes=i)
        for i, (p, lead, r, t) in enumerate(rows)
    ]
    result = scoring.compare(quotes, SPEC_10)
    assert all(Decimal(0) <= s.score <= Decimal(100) for s in result.ranked)
    assert result.recommended is not None
    assert result.recommended.score == max(s.score for s in result.ranked)
    assert [s.rank for s in result.ranked] == list(range(1, len(rows) + 1))
