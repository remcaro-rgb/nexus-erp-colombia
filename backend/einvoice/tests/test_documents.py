"""Builders (golden files), signature, lifecycle, outbox, duplicates, contingency, production refusal,
certificates, RADIAN and retention (T-P15-01, -04 … -10, -12, -13)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.db import DatabaseError, transaction

from einvoice import builders, hashing, lifecycle, mode, service, signing, worker
from einvoice.adapters.contract import sample_model, sample_request
from einvoice.adapters.simulator import SandboxSimulator
from einvoice.errors import ProductionRefused
from einvoice.model import BillingReference, FiscalDocumentModel, PayrollTotals
from einvoice.models import (
    Contingency,
    FiscalDocument,
    FiscalEvidence,
    RadianEvent,
    SigningCertificate,
    TransmissionLog,
)
from einvoice.secrets import Secret
from einvoice.testing.fixtures import create_range, invoice_model, register
from kernel import clock, outbox, tenancy
from kernel.errors import ExternalIOForbidden, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work

GOLDEN = Path(__file__).with_name("golden")
KEY = "golden-technical-key-0000000000000000000000"
S = FiscalDocument.State


def _golden_models() -> dict[str, FiscalDocumentModel]:
    fe = sample_model("SETT990000001")
    ref = BillingReference("SETT990000001", "a" * 96, dt.date(2026, 11, 2))
    return {
        "FE": fe,
        "FE_EXPORT": replace(fe, family="FE_EXPORT", number="SETT990000002", currency="USD"),
        "FE_CONTINGENCY_ISSUER": replace(fe, family="FE_CONTINGENCY_ISSUER", number="SETT990000003"),
        "NC": replace(fe, family="NC", number="NC1001", concept_code="2", billing_reference=ref),
        "ND": replace(fe, family="ND", number="ND1001", concept_code="4", billing_reference=ref),
        "DEE_POS": replace(fe, family="DEE_POS", number="POS1001"),
        "DS": replace(fe, family="DS", number="DS1001"),
        "DS_ADJUSTMENT": replace(fe, family="DS_ADJUSTMENT", number="NA1001", billing_reference=ref),
        "NOMINA": replace(
            fe,
            family="NOMINA",
            number="NE1001",
            lines=(),
            payroll=PayrollTotals(
                Decimal("3500000.00"),
                Decimal("280000.00"),
                Decimal("3220000.00"),
                dt.date(2026, 11, 1),
                dt.date(2026, 11, 30),
            ),
        ),
    }


def _build(model: FiscalDocumentModel) -> bytes:
    code = hashing.compute(model.hash_kind, model.hash_inputs("2", KEY))
    return builders.build(model, code, environment="2", software_id="SYN-SOFTWARE", qr_url="https://example.invalid/qr")


# ============================================================================ T-P15-01


@pytest.mark.parametrize("family", sorted(_golden_models()))
def test_T_P15_01_golden_xml_per_document_type(family: str) -> None:
    model = _golden_models()[family]
    model.validate()
    xml = _build(model)
    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    golden = (GOLDEN / f"{family}.xml").read_bytes()
    assert xml == golden, f"{family}: XML changed (regenerate the golden file only for an intended change)"
    assert hashlib.sha256(golden).hexdigest() == manifest[family]
    root = ET.fromstring(xml)  # noqa: S314 - own output
    assert root.tag.endswith(model.root)
    assert b"SIMULADO, NO CERTIFICADO" in xml
    assert builders.build(model, "x" * 96, environment="2", software_id="S", qr_url="q") == builders.build(
        model, "x" * 96, environment="2", software_id="S", qr_url="q"
    )  # deterministic


def test_T_P15_01_model_refuses_floats_and_inconsistent_totals() -> None:
    with pytest.raises(ValidationFailed):
        invoice_model(payable="119000.01", tax_inclusive="119000.01")
    with pytest.raises(ValidationFailed):
        FiscalDocumentModel.from_dict({**invoice_model().to_dict(), "line_extension": 100000.0})


# ============================================================================ T-P15-04


def test_T_P15_04_signature_verifies_and_tampering_fails() -> None:
    key = Secret("k", "worker-only-signing-key-000000")
    xml = _build(_golden_models()["FE"])
    signed = signing.sign(xml, key, signing.CertificateInfo("CN=SINTETICO", "ab" * 32), signature_id="t1")
    props = signing.verify(signed, key)
    assert props["policy"] == signing.POLICY_URL and props["role"] == "supplier"
    with pytest.raises(signing.SignatureInvalid, match="document digest"):
        signing.verify(signed.replace(b"119000.00", b"119000.01", 1), key)
    with pytest.raises(signing.SignatureInvalid, match="signature value"):
        signing.verify(signed, Secret("k", "another-key"))
    with pytest.raises(signing.SignatureInvalid):
        signing.verify(signed.replace(b"supplier</xades:ClaimedRole>", b"buyer</xades:ClaimedRole>"), key)


# ============================================================================ lifecycle


@pytest.fixture
def ranged(db: Any, ctx: Any, company: uuid.UUID) -> Any:
    create_range(ctx, company)
    return ctx


def _doc(ctx: Any, pk: uuid.UUID) -> FiscalDocument:
    with tenancy.scope(ctx):
        return FiscalDocument.objects.get(pk=pk)


def test_T_P15_05_state_machine_retryable_vs_final(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    assert lifecycle.can(S.SUBMITTED, S.ACCEPTED) and not lifecycle.can(S.ACCEPTED, S.SUBMITTED)
    assert not lifecycle.can(S.REJECTED, S.SUBMITTED)  # rejected is final
    ok = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000001")).id)
    assert ok.status == S.ACCEPTED and ok.attempts == 1
    rej = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000002", "reject")).id)
    assert rej.status == S.REJECTED and rej.error_codes == ["SIM-REJ-01"]
    tmp = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000003", "transient")).id)
    assert tmp.status == S.ERROR_RETRYABLE and tmp.next_attempt_at is not None
    number = tmp.number
    assert service.transmit(ctx, tmp.id).status == S.ERROR_RETRYABLE  # backoff not elapsed: nothing happens
    again = service.transmit(ctx, tmp.id, force=True)
    assert again.status == S.ACCEPTED and again.attempts == 2 and again.number == number  # never re-numbered
    with tenancy.scope(ctx):
        states = list(again.transitions.values_list("to_state", flat=True))
    assert states == ["pending_build", "built", "signed", "submitted", "error_retryable", "submitted", "accepted"]
    with pytest.raises(Exception, match="final"):
        run_in_unit_of_work(lambda u: service.retry(u, rej), ctx)


def test_T_P15_06_no_external_call_inside_the_business_transaction(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    calls: list[str] = []
    original = SandboxSimulator.submit

    def spy(self: SandboxSimulator, request: Any) -> Any:
        calls.append(request.number)
        return original(self, request)

    SandboxSimulator.submit = spy  # type: ignore[method-assign]
    try:
        doc = register(ctx, company, invoice_model("SETP990000004"))
        assert calls == [] and _doc(ctx, doc.id).status == S.BUILT  # committed, nothing transmitted
        with pytest.raises(ExternalIOForbidden):
            run_in_unit_of_work(lambda u: SandboxSimulator().submit(sample_request()), ctx)
        calls.clear()  # the refused probe above is not a transmission
        with tenancy.scope(ctx):
            env = OutboxMessage.objects.filter(event_type="einvoice.document.status_changed.v1").last()
        assert env is not None and env.envelope["payload"]["to_status"] == "built"
        assert outbox.dispatch(env.envelope)[worker.CONSUMER] == "processed"
        assert calls == ["SETP990000004"] and _doc(ctx, doc.id).status == S.ACCEPTED
        assert outbox.dispatch(env.envelope)[worker.CONSUMER] == "duplicate"  # at-least-once, deduped
    finally:
        SandboxSimulator.submit = original  # type: ignore[method-assign]


def test_T_P15_08_duplicate_submission_gives_one_accepted_record(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    src = uuid.uuid4()
    first = register(ctx, company, invoice_model("SETP990000005"), source_id=src)
    second = register(ctx, company, invoice_model("SETP990000005"), source_id=src)
    assert first.id == second.id
    with pytest.raises(Exception, match="already used"):
        register(ctx, company, invoice_model("SETP990000005"))  # same number, other source
    a = service.transmit(ctx, first.id)
    b = service.transmit(ctx, first.id)
    assert a.status == b.status == S.ACCEPTED
    with tenancy.scope(ctx):
        assert FiscalDocument.objects.filter(number="SETP990000005").count() == 1
        assert TransmissionLog.objects.filter(document_id=first.id).count() == 1


def test_T_P15_07_contingency_switch_and_48h_deadline(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    for i in range(3):  # three consecutive transport failures → automatic contingency
        service.transmit(ctx, register(ctx, company, invoice_model(f"SETP99000010{i}", "transient")).id)
    with tenancy.scope(ctx):
        cont = Contingency.objects.get(active=True)
    assert cont.automatic and cont.kind == "PROVIDER_OUTAGE"
    during = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000110")).id)
    assert during.status == S.CONTINGENCY_ISSUED
    restored = dt.datetime(2026, 11, 3, 15, 0, tzinfo=dt.UTC)
    with clock.frozen(restored):
        ended = run_in_unit_of_work(lambda u: service.end_contingency(u, company), ctx)
    assert ended.deadline_at == restored + dt.timedelta(hours=48)
    with clock.frozen(restored + dt.timedelta(hours=49)), tenancy.scope(ctx):
        over = service.contingency_overview(ctx, company)
    assert over["overdue"] is True and over["pending_documents"] == 1
    late = service.transmit(ctx, during.id)
    assert late.status == S.ACCEPTED
    with tenancy.scope(ctx):
        assert "transmitted_late" in list(late.transitions.values_list("to_state", flat=True))
    # issuer-side outage: manual declaration switches new invoices to the contingency type
    run_in_unit_of_work(lambda u: service.declare_contingency(u, company, "ISSUER_OUTAGE", "corte de energía"), ctx)
    doc = register(ctx, company, invoice_model("SETP990000111"))
    assert doc.family == "FE_CONTINGENCY_ISSUER"


def test_T_P15_09_production_refuses_simulator_and_transmission_without_gates(
    ranged: Any, company: uuid.UUID, settings: Any
) -> None:
    ctx = ranged
    doc = register(ctx, company, invoice_model("SETP990000006"))
    settings.ERP_FISCAL_MODE = "production"
    with pytest.raises(ProductionRefused):
        SandboxSimulator()
    with pytest.raises(Exception, match="production"):
        register(ctx, company, invoice_model("SETP990000007"))
    with pytest.raises(ProductionRefused, match="EG-1"):
        service.transmit(ctx, doc.id)
    assert _doc(ctx, doc.id).status == S.BUILT  # nothing signed, nothing sent
    settings.ERP_FISCAL_MODE = "sandbox"
    with pytest.raises(ProductionRefused, match="EG-1"):
        mode.require_transmission_allowed("1")


def test_T_P15_10_certificate_expiry_alerts(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    today = dt.date(2026, 11, 10)
    run_in_unit_of_work(
        lambda u: SigningCertificate.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company,
            subject="CN=SINTETICO",
            thumbprint="ab" * 32,
            valid_to=today + dt.timedelta(days=29),
            key_ref="secret://einvoice/cert-key",
        ),
        ctx,
    )
    first = run_in_unit_of_work(lambda u: service.certificate_alerts(u, company, today), ctx)
    assert [a["threshold"] for a in first] == [30]
    assert run_in_unit_of_work(lambda u: service.certificate_alerts(u, company, today), ctx) == []
    later = run_in_unit_of_work(lambda u: service.certificate_alerts(u, company, today + dt.timedelta(days=22)), ctx)
    assert [a["threshold"] for a in later] == [7]  # 15 and 7 crossed: one alert at the tightest threshold
    expired = run_in_unit_of_work(lambda u: service.certificate_alerts(u, company, today + dt.timedelta(days=40)), ctx)
    assert expired[0]["threshold"] == 0


@pytest.mark.parametrize("code", ["030", "032", "033", "031"])
def test_T_P15_12_radian_event_xml_per_event_type(ranged: Any, company: uuid.UUID, code: str) -> None:
    ctx = ranged
    doc = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000008")).id)
    ref = DocumentRef("einvoice.document", doc.id)
    if code in ("031", "033"):
        run_in_unit_of_work(lambda u: service.record_received_event(u, company, ref, "032"), ctx)
    ev = run_in_unit_of_work(lambda u: service.emit_radian_event(u, company, ref, code, claim_concept="01"), ctx)
    with tenancy.scope(ctx):
        xml = service.read_evidence(FiscalEvidence.objects.get(radian_event=ev, kind="event_xml"))
    root = ET.fromstring(xml)  # noqa: S314 - own output
    ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    assert root.tag.endswith("ApplicationResponse")
    assert root.find(".//cbc:ResponseCode", ns).text == code  # type: ignore[union-attr]
    assert root.find(".//cbc:UUID", ns).text == ev.unique_code  # type: ignore[union-attr]
    sent = service.transmit_event(ctx, ev.id)
    assert sent.status == RadianEvent.Status.ACCEPTED


def test_T_P15_12_radian_rules_and_tacit_acceptance(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    doc = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000009")).id)
    ref = DocumentRef("einvoice.document", doc.id)
    with pytest.raises(ValidationFailed):
        run_in_unit_of_work(lambda u: service.emit_radian_event(u, company, ref, "033"), ctx)  # needs 032 first
    with pytest.raises(ValidationFailed):
        run_in_unit_of_work(lambda u: service.emit_radian_event(u, company, ref, "034"), ctx)  # system only
    received = dt.datetime(2026, 11, 3, 15, 0, tzinfo=dt.UTC)  # Tuesday
    run_in_unit_of_work(lambda u: service.record_received_event(u, company, ref, "032", received), ctx)
    with tenancy.scope(ctx):
        assert service.tacit_acceptance_due(ctx, company, dt.date(2026, 11, 6)) == []
        assert service.tacit_acceptance_due(ctx, company, dt.date(2026, 11, 9)) == [doc.id]
    events = run_in_unit_of_work(lambda u: service.apply_tacit_acceptance(u, company, dt.date(2026, 11, 9)), ctx)
    assert [e.event_code for e in events] == ["034"] and events[0].system_generated


def test_T_P15_13_fiscal_evidence_cannot_be_deleted_or_changed(ranged: Any, company: uuid.UUID) -> None:
    ctx = ranged
    doc = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000012")).id)
    with bound(ctx):
        ev = FiscalEvidence.objects.filter(document_id=doc.id, kind="signed_xml").first()
        assert ev is not None and ev.legal_hold and ev.retention_until == dt.date(2036, 11, 2)
        assert bytes(ev.content) != service.read_evidence(ev)  # encrypted at rest
        for stmt in (
            lambda: FiscalEvidence.objects.filter(pk=ev.pk).delete(),
            lambda: FiscalEvidence.objects.filter(pk=ev.pk).update(legal_hold=False),
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                stmt()
