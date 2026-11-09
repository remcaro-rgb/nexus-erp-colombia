"""Tax engine tests (T-P06-02 … T-P06-07) over the real 2026 seed in PostgreSQL."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kernel.errors import ParameterNotVerified
from kernel.parameters import ParameterStatus, ParameterValue
from kernel.testing.fakes import FakeParameterProvider
from kernel.uow import run_in_unit_of_work
from tax.engine.engine import TaxEngine
from tax.engine.types import DocumentInput, DocumentLine, PartyProfile, TaxCodeDef
from tax.errors import TaxParameterUnknown
from tax.parameters.store import DbParameterProvider
from tax.service import compute_document

D = Decimal
JULY = dt.date(2026, 7, 15)
AGENT = PartyProfile(regime="RESPONSABLE_IVA", withholding_agent=True, vat_withholding_agent=True)
NOT_AGENT = PartyProfile(regime="RESPONSABLE_IVA")
SELLER = PartyProfile(regime="RESPONSABLE_IVA")
SELLER_NO_IVA = PartyProfile(person_type="NATURAL", regime="NO_RESPONSABLE_IVA")


@pytest.fixture
def synthetic(db: Any, p06_ctx: Any, company: uuid.UUID) -> Any:
    """Seed + the synthetic dev fixture (labelled NOT LEGALLY VERIFIED) for concepts whose official rate is UNKNOWN."""
    from tax.wiring import seed_tax

    run_in_unit_of_work(lambda uow: seed_tax(uow, {"companies": [str(company)], "synthetic_dev": True}), p06_ctx)
    return p06_ctx


def _compute(ctx: Any, company: uuid.UUID, lines: list[DocumentLine], date: dt.date = JULY, **kw: Any) -> Any:
    from kernel import tenancy

    kw.setdefault("seller", SELLER)
    kw.setdefault("buyer", AGENT)
    with tenancy.activate(ctx):
        return compute_document(ctx.tenant_id, company, "purchase.invoice", date, None, lines, **kw)


def _wh(result: Any, code: str) -> list[Any]:
    return [t for t in result.lines if t.tax_code == code]


# ----------------------------------------------------------------------------- T-P06-02


@pytest.mark.django_db
@pytest.mark.parametrize(("base", "applies"), [(D("104747"), False), (D("104748"), True), (D("104749"), True)])
def test_T_P06_02_uvt_minimum_base_exact_boundary(synthetic: Any, company: uuid.UUID, base: Decimal,
                                                  applies: bool) -> None:  # fmt: skip
    # Decreto 572 art. 2: no services withholding below 2 UVT; 2 × 52,374 = 104,748 (UVT 2026, S-08).
    result = _compute(synthetic, company, [DocumentLine("1", base, (), "SYN-RF-SERVICIOS")])
    check = next(s for s in result.trace if s.step == "min_base_check")
    assert check.data["min_base"] == "104748"
    assert bool(_wh(result, "SYN-RF-SERVICIOS")) is applies
    if applies:
        assert _wh(result, "SYN-RF-SERVICIOS")[0].amount == (base * D("0.04")).quantize(D("0.01"))


@pytest.mark.django_db
def test_T_P06_02_minimum_base_uses_document_total_per_concept(synthetic: Any, company: uuid.UUID) -> None:
    lines = [
        DocumentLine("1", D("60000"), (), "SYN-RF-SERVICIOS"),
        DocumentLine("2", D("44748"), (), "SYN-RF-SERVICIOS"),
    ]
    result = _compute(synthetic, company, lines)
    assert [t.line_ref for t in _wh(result, "SYN-RF-SERVICIOS")] == ["1", "2"]


@pytest.mark.django_db
def test_T_P06_02_official_rate_unknown_fails_closed_above_base(seeded: Any, company: uuid.UUID) -> None:
    assert not _compute(seeded, company, [DocumentLine("1", D("104747"), (), "RF-SERVICIOS")]).lines
    with pytest.raises(TaxParameterUnknown) as exc:
        _compute(seeded, company, [DocumentLine("1", D("104748"), (), "RF-SERVICIOS")])
    assert exc.value.code == "tax_parameter_unknown"
    assert exc.value.extra["parameter"] == "retefuente.servicios.tarifa"


# ----------------------------------------------------------------------------- T-P06-03


@pytest.mark.django_db
def test_T_P06_03_withholding_sets_switch_on_2026_07_01(synthetic: Any, company: uuid.UUID) -> None:
    from kernel.testing.tenancy import make_context
    from tax.models import Parameter
    from tax.parameters import workflow

    ctx = synthetic
    line = [DocumentLine("1", D("150000"), (), "SYN-RF-SERVICIOS")]
    # 2026-06-30: the suspension window; the pre-572 base was not opened → UNKNOWN → fails closed.
    with pytest.raises(TaxParameterUnknown):
        _compute(ctx, company, line, dt.date(2026, 6, 30))

    # A tenant loads a (synthetic, labelled) pre-572 value for the window through the two-person workflow.
    unknown = Parameter.objects.get(tenant_id=ctx.tenant_id, code="retefuente.servicios.base_uvt",
                                    valid_from=dt.date(2026, 5, 7))  # fmt: skip
    data = workflow.ProposalInput(
        code="retefuente.servicios.base_uvt", value_type="uvt", value="4", valid_from=dt.date(2026, 5, 7),
        valid_to=dt.date(2026, 6, 30), parameter_status="VERIFIED-SECONDARY", source_id=unknown.source_id,
        synthetic=True, notes="synthetic pre-572 set for the test",
    )  # fmt: skip
    proposal = run_in_unit_of_work(lambda u: workflow.propose_change(u, unknown.id, data), ctx)
    approver = make_context(tenant_id=ctx.tenant_id, company_ids=ctx.company_ids)
    run_in_unit_of_work(lambda u: workflow.approve(u, proposal.id), approver)

    before = _compute(ctx, company, line, dt.date(2026, 6, 30))
    after = _compute(ctx, company, line, dt.date(2026, 7, 1))
    min_before = next(s for s in before.trace if s.step == "min_base_check").data["min_base"]
    min_after = next(s for s in after.trace if s.step == "min_base_check").data["min_base"]
    assert (min_before, min_after) == ("209496", "104748")  # 4 UVT vs 2 UVT
    assert _wh(before, "SYN-RF-SERVICIOS") == []  # 150,000 < 4 UVT
    assert _wh(after, "SYN-RF-SERVICIOS")[0].amount == D("6000.00")
    snap = {s["code"]: s for s in after.parameter_snapshots}
    assert snap["retefuente.servicios.base_uvt"]["status"] == "DISPUTED"
    assert snap["retefuente.servicios.base_uvt"]["valid_from"] == "2026-07-01"
    assert {s["code"]: s for s in before.parameter_snapshots}["retefuente.servicios.base_uvt"]["synthetic"] is True
    history = Parameter.objects.get(tenant_id=ctx.tenant_id, code="retefuente.servicios.base_uvt",
                                    valid_from=dt.date(2026, 7, 1)).legal_history  # fmt: skip
    assert [e["event"] for e in history] == ["published", "suspended", "suspension_revoked", "applies_again"]


# ----------------------------------------------------------------------------- T-P06-04


@pytest.mark.django_db
def test_T_P06_04_unknown_raises(seeded: Any, company: uuid.UUID) -> None:
    from kernel import tenancy

    with tenancy.activate(seeded):
        provider = DbParameterProvider()
        with pytest.raises(Exception) as exc:
            provider.get("uvt", dt.date(2027, 1, 1), company)  # 2027: no rows (R-TX-02)
        assert exc.value.code == "parameter_unknown"
        with pytest.raises(Exception) as exc:
            provider.get("retefuente.compras.tarifa", JULY, company)  # UNKNOWN row
        assert exc.value.code == "parameter_unknown"
    lines = [DocumentLine("1", D("100000"), ("IVA19",))]
    with pytest.raises(TaxParameterUnknown):
        _compute(seeded, company, lines, dt.date(2025, 12, 31))  # store starts 2026-01-01


@pytest.mark.django_db
def test_T_P06_04_production_rules_refuse_unstamped_values(seeded: Any, company: uuid.UUID) -> None:
    from kernel import tenancy

    with tenancy.activate(seeded):
        with pytest.raises(ParameterNotVerified):
            DbParameterProvider(production=True).get("uvt", JULY, company)
        assert DbParameterProvider(production=False).get("uvt", JULY, company).value == D("52374")
        lines = [DocumentLine("1", D("100000"), ("IVA19",))]
        with pytest.raises(ParameterNotVerified):
            compute_document(seeded.tenant_id, company, "FV", JULY, None, lines, seller=SELLER, buyer=NOT_AGENT,
                             parameters=DbParameterProvider(production=True))  # fmt: skip


@pytest.mark.django_db
def test_T_P06_04_production_accepts_stamped_values(seeded: Any, company: uuid.UUID) -> None:
    from kernel import clock, tenancy
    from tax.models import Parameter

    Parameter.objects.filter(tenant_id=seeded.tenant_id, code="uvt").update(
        legal_reviewed_by=uuid.uuid4(), legal_reviewed_at=clock.now()
    )
    with tenancy.activate(seeded):
        value = DbParameterProvider(production=True).get("uvt", JULY, company)
    assert value.legal_review_stamp and value.status is ParameterStatus.VERIFIED_PRIMARY


@pytest.mark.django_db
def test_T_P06_04_disputed_computes_and_snapshots(synthetic: Any, company: uuid.UUID) -> None:
    result = _compute(synthetic, company, [DocumentLine("1", D("200000"), (), "SYN-RF-SERVICIOS")])
    snap = {s["code"]: s for s in result.parameter_snapshots}
    assert snap["retefuente.servicios.base_uvt"]["status"] == "DISPUTED"
    assert snap["retefuente.servicios.base_uvt"]["source_id"] == "S-09"
    assert any("retefuente.servicios.base_uvt: DISPUTED" in w for w in result.warnings)
    assert any("FIXTURE — NOT LEGALLY VERIFIED" in w for w in result.warnings)  # synthetic rate is labelled
    assert _wh(result, "SYN-RF-SERVICIOS")[0].amount == D("8000.00")


# ----------------------------------------------------------------------------- T-P06-05


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("buyer_agent", "seller_regime", "expected"),
    [(True, "RESPONSABLE_IVA", True), (True, "NO_RESPONSABLE_IVA", False), (False, "RESPONSABLE_IVA", False),
     (False, "NO_RESPONSABLE_IVA", False)],
)  # fmt: skip
def test_T_P06_05_reteiva_only_agent_buyer_and_vat_responsible_seller(
    seeded: Any, company: uuid.UUID, buyer_agent: bool, seller_regime: str, expected: bool
) -> None:
    seller = PartyProfile(regime=seller_regime)
    buyer = PartyProfile(regime="RESPONSABLE_IVA", vat_withholding_agent=buyer_agent)
    # IVA applies only when the seller is VAT-responsible (matrix of IVA19); ReteIVA needs both conditions.
    result = _compute(seeded, company, [DocumentLine("1", D("1000000"), ("IVA19", "RETEIVA"))], seller=seller,
                      buyer=buyer)  # fmt: skip
    reteiva = _wh(result, "RETEIVA")
    assert bool(reteiva) is expected
    if expected:
        assert _wh(result, "IVA19")[0].amount == D("190000.00")
        assert reteiva[0].base_amount == D("190000.00") and reteiva[0].amount == D("28500.00")  # 15 % of VAT
    decision = next(s for s in result.trace if s.step == "applicability" and s.tax_code == "RETEIVA")
    assert decision.data["applies"] is expected


# ----------------------------------------------------------------------------- T-P06-06


@pytest.mark.django_db
def test_T_P06_06_rounding_per_line_vs_document(seeded: Any, company: uuid.UUID) -> None:
    lines = [DocumentLine(str(i), D("33.33"), ("IVA19",)) for i in range(1, 4)]  # 6.3327 each
    per_line = _compute(seeded, company, lines, buyer=NOT_AGENT)
    per_doc = _compute(seeded, company, lines, buyer=NOT_AGENT, rounding_mode="DOCUMENT")
    assert [t.amount for t in per_line.lines] == [D("6.33")] * 3
    assert per_line.totals["IVA19"] == D("18.99") and per_line.rounding_adjustment == 0
    assert per_doc.totals["IVA19"] == D("19.00")
    assert per_doc.rounding_adjustment == D("0.01")  # posted to the ROUNDING line by the caller
    assert any(s.step == "rounding" for s in per_doc.trace)


@given(st.lists(st.decimals(min_value=D("0.01"), max_value=D("99999999.99"), places=2), min_size=1, max_size=20))
def test_T_P06_06_rounding_properties(bases: list[Decimal]) -> None:
    provider = FakeParameterProvider(
        {"iva.general": ParameterValue("iva.general", D("0.19"), ParameterStatus.VERIFIED_PRIMARY, "S-10")}
    )
    engine = TaxEngine(provider, {"IVA19": TaxCodeDef("IVA19", "IVA", "iva.general")})
    lines = tuple(DocumentLine(str(i), b, ("IVA19",)) for i, b in enumerate(bases))
    for mode in ("LINE", "DOCUMENT"):
        doc = DocumentInput(uuid.uuid4(), JULY, SELLER, NOT_AGENT, lines, rounding_mode=mode)
        res = engine.compute(doc)
        assert sum(t.amount for t in res.lines) + res.rounding_adjustment == res.totals["IVA19"]
        assert abs(res.rounding_adjustment) <= D("0.005") * len(lines)
        assert all(t.amount == t.amount.quantize(D("0.01")) for t in res.lines)


# ----------------------------------------------------------------------------- T-P06-07


@pytest.mark.django_db
def test_T_P06_07_explanation_trace_complete(synthetic: Any, company: uuid.UUID) -> None:
    lines = [DocumentLine("1", D("1000000"), ("IVA19", "RETEIVA"), "SYN-RF-SERVICIOS"),
             DocumentLine("2", D("50000"), ("IVA5",))]  # fmt: skip
    result = _compute(synthetic, company, lines)
    trace = [s.to_json() for s in result.trace]
    used = {s["data"]["parameter"] for s in trace if s["step"] == "parameter"}
    # every parameter of every computed amount is explained with value, status and source
    for tax in result.lines:
        assert set(tax.parameters) <= used, tax
        assert any(s["step"] == "amount" and s["line_ref"] == tax.line_ref and s["tax_code"] == tax.tax_code
                   and s["data"]["amount"] == str(tax.amount) for s in trace)  # fmt: skip
    for s in trace:
        if s["step"] == "parameter":
            assert {"value", "status", "source_id", "valid_from"} <= set(s["data"])
    # every snapshot appears in the trace, and every (line, code) has an applicability decision
    assert {snap["code"] for snap in result.parameter_snapshots} == used
    decided = {(s["line_ref"], s["tax_code"]) for s in trace if s["step"] == "applicability"}
    assert decided == {("1", "IVA19"), ("1", "RETEIVA"), ("1", "SYN-RF-SERVICIOS"), ("2", "IVA5")}
    assert trace[0]["step"] == "document" and trace[-1]["step"] == "totals"
    assert result.to_json()["trace"] == trace


# ----------------------------------------------------------------------------- engine edge cases


@pytest.mark.django_db
def test_P06_engine_inactive_and_unknown_codes(seeded: Any, company: uuid.UUID) -> None:
    from tax.models import TaxCode

    with pytest.raises(Exception) as exc:
        _compute(seeded, company, [DocumentLine("1", D("10"), ("NOPE",))])
    assert exc.value.code == "validation_failed"
    TaxCode.objects.filter(tenant_id=seeded.tenant_id, code="IVA5").update(status="ARCHIVED")
    with pytest.raises(Exception) as exc:
        _compute(seeded, company, [DocumentLine("1", D("10"), ("IVA5",))])
    assert exc.value.code == "tax_code_inactive"


@pytest.mark.django_db
def test_P06_engine_ica_without_tariff_fails_closed(seeded: Any, company: uuid.UUID) -> None:
    with pytest.raises(TaxParameterUnknown) as exc:
        _compute(seeded, company, [DocumentLine("1", D("500000"), ("RETEICA",), activity_code="4711")],
                 municipality="05001")  # fmt: skip
    assert "B-13" in str(exc.value)


@pytest.mark.django_db
def test_P06_engine_synthetic_ica_tariff_computes_with_label(synthetic: Any, company: uuid.UUID) -> None:
    result = _compute(synthetic, company, [DocumentLine("1", D("1000000"), ("RETEICA",), activity_code="4711")],
                      municipality="11001")  # fmt: skip
    assert _wh(result, "RETEICA")[0].amount == D("9660.00")  # 9.66 per mil (synthetic)
    assert any("syn.ica.11001.default: FIXTURE" in w for w in result.warnings)


@pytest.mark.django_db
def test_P06_engine_autorretenedor_seller_is_not_withheld(synthetic: Any, company: uuid.UUID) -> None:
    seller = PartyProfile(regime="RESPONSABLE_IVA", autorretenedor=True)
    result = _compute(synthetic, company, [DocumentLine("1", D("1000000"), (), "SYN-RF-SERVICIOS")], seller=seller)
    assert result.lines == ()
    assert "autorretenedor" in next(s for s in result.trace if s.step == "applicability").detail


@pytest.mark.django_db
def test_P06_kernel_tax_service_uses_party_profiles(seeded: Any, company: uuid.UUID) -> None:
    from kernel import tenancy
    from kernel.interfaces.tax import TaxLineInput, TaxService
    from tax.models import PartyTaxProfile
    from tax.service import EngineTaxService

    vendor = uuid.uuid4()
    who = {"tenant_id": seeded.tenant_id, "company_id": company}
    PartyTaxProfile.objects.create(**who, party_id=company, regime="RESPONSABLE_IVA", vat_withholding_agent=True,
                                   withholding_agent=True)  # fmt: skip
    PartyTaxProfile.objects.create(**who, party_id=vendor, regime="RESPONSABLE_IVA")
    svc = EngineTaxService()
    assert isinstance(svc, TaxService)
    lines = [TaxLineInput("1", D("1000000"), ("IVA19", "RETEIVA"))]
    with tenancy.activate(seeded):
        taxes = svc.compute(company, "purchase.invoice", JULY, vendor, lines)
        withholdings = svc.withholdings(company, "purchase.invoice", JULY, vendor, lines)
        sales = svc.withholdings(company, "FV", JULY, vendor, lines)  # vendor is not an agent → no ReteIVA
    assert [(t.tax_code, t.amount) for t in taxes.lines] == [("IVA19", D("190000.00"))]
    assert [(t.tax_code, t.amount, t.withholding) for t in withholdings.lines] == [("RETEIVA", D("28500.00"), True)]
    assert sales.lines == ()
    assert {s["code"] for s in taxes.parameter_snapshots} >= {"iva.general"}
