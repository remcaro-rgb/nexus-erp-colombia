"""Application services of the DIAN gateway (owner P15).

Inside a unit of work (business transaction of the caller, C1-10 / C2-6):
  ``register_document`` — numbering check, CUFE/CUDE/CUNE, UBL build, encrypted evidence, outbox event.
Outside any unit of work (the einvoice worker, ``einvoice.worker``):
  ``transmit`` — sign (key only in the worker), submit through the active adapter, record the answer.

The issuing modules (P10 sales, P11 purchasing, P12 POS, P13 payroll) call ``register_document`` from
their own UoW with a :class:`~einvoice.model.FiscalDocumentModel`; nothing external happens before the
commit (T-P15-06). Duplicate submissions are impossible: one row per ``(issuer, family, number)`` and per
source document, a row lock around every state change, and the result of an attempt is recorded only once
(T-P15-08). The number of a document is never changed nor re-issued.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import uuid
import zipfile
from dataclasses import replace
from typing import Any

from django.db.models import Q

from einvoice import annex, builders, crypto, hashing, lifecycle, mode, signing
from einvoice import secrets as sec
from einvoice.adapters import registry
from einvoice.adapters.base import AdapterConfig, ProviderAdapter, SubmitRequest, SubmitResult
from einvoice.errors import (
    CertificateMissing,
    InvalidTransition,
    NumberingRangeMissing,
    ProductionRefused,
    ProviderContractTestFailed,
    ProviderNotConfigured,
    TransientTransmissionError,
)
from einvoice.model import FiscalDocumentModel, Party
from einvoice.models import (
    Contingency,
    DeliveryLog,
    EinvoiceSettings,
    FiscalDocument,
    FiscalEvidence,
    FiscalTransition,
    GateRecord,
    NumberingRange,
    RadianEvent,
    SigningCertificate,
    TechnologyProvider,
    TransmissionLog,
)
from kernel import clock, services, tenancy
from kernel.errors import Conflict, DomainError, NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.common import DocumentRef
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

S = FiscalDocument.State
RETENTION_YEARS = 10
CONTINGENCY_DEADLINE = dt.timedelta(hours=48)
NUMBERED_FAMILIES = frozenset({"FE", "FE_EXPORT", "FE_CONTINGENCY_DIAN", "FE_CONTINGENCY_ISSUER", "DEE_POS", "DS"})
DEV_TECHNICAL_KEY_REF = sec.PREFIX + "dev-fixture-technical-key"
DEV_SOFTWARE_PIN_REF = sec.PREFIX + "dev-fixture-software-pin"
DEV_SOFTWARE_ID = "SINTETICO-SOFTWARE-ID"
CERT_ALERT_DAYS = (30, 15, 7)


# ============================================================================ helpers


def _audit(uow: UnitOfWork, action: str, resource_type: str, resource_id: uuid.UUID | None, company_id: uuid.UUID,
           changes: dict[str, Any] | None = None, reason: str = "") -> None:  # fmt: skip
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(uow, AuditEntry(action, resource_type, resource_id, company_id, sec.redact_obj(changes or {}), reason))


def retention_until(day: dt.date) -> dt.date:
    try:
        return day.replace(year=day.year + RETENTION_YEARS)
    except ValueError:  # 29 February
        return day.replace(year=day.year + RETENTION_YEARS, day=28)


def store_evidence(
    doc: FiscalDocument | None, kind: str, content: bytes, *, event: RadianEvent | None = None
) -> FiscalEvidence:
    owner = doc or event
    assert owner is not None
    issued = doc.issue_date if doc is not None else clock.business_date()
    return FiscalEvidence.objects.create(
        tenant_id=owner.tenant_id, company_id=owner.company_id, document=doc, radian_event=event, kind=kind,
        sha256=hashlib.sha256(content).hexdigest(), size=len(content), content=crypto.encrypt(content),
        retention_until=retention_until(issued), legal_hold=True,
    )  # fmt: skip


def read_evidence(evidence: FiscalEvidence) -> bytes:
    data = crypto.decrypt(bytes(evidence.content))
    if hashlib.sha256(data).hexdigest() != evidence.sha256:
        raise crypto.EvidenceIntegrityError("evidence hash mismatch")
    return data


def latest_evidence(doc: FiscalDocument, kind: str) -> FiscalEvidence | None:
    return FiscalEvidence.objects.filter(document=doc, kind=kind).order_by("-created_at", "-id").first()


# ============================================================================ settings / adapters


def get_settings(uow: UnitOfWork, company_id: uuid.UUID) -> EinvoiceSettings:
    """Company settings (created with the DEV default: SandboxSimulator, environment 2)."""
    uow.ctx.require_company(company_id)
    row = EinvoiceSettings.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id).first()
    if row is None:
        row = EinvoiceSettings.objects.create(tenant_id=uow.ctx.tenant_id, company_id=company_id,
                                              created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id)  # fmt: skip
    return row


def read_settings(ctx: TenantContext, company_id: uuid.UUID) -> EinvoiceSettings | None:
    return EinvoiceSettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).select_related("provider").first()


def provider_config(provider: TechnologyProvider, *, resolve_secret: bool = True) -> AdapterConfig:
    credentials = sec.resolve(provider.credentials_ref) if (resolve_secret and provider.credentials_ref) else None
    return AdapterConfig(adapter=provider.adapter, provider_code=provider.code, base_url=provider.base_url,
                         environment=provider.environment, credentials=credentials,
                         mapping_version=provider.contract_version)  # fmt: skip


def adapter_for(settings_row: EinvoiceSettings | None) -> tuple[ProviderAdapter, str]:
    """Worker side: the active adapter of the company (resolves the provider credentials here only)."""
    if settings_row is None or settings_row.adapter == "simulator":
        return registry.create(AdapterConfig(adapter="simulator")), "SIM"
    if settings_row.adapter == "direct":
        return registry.create(AdapterConfig(adapter="direct")), "DIAN-DIRECT"
    provider = settings_row.provider
    if provider is None or provider.config_state != TechnologyProvider.ConfigState.ACTIVE:
        raise ProviderNotConfigured("no active technology provider for the company")
    return registry.create(provider_config(provider)), provider.code


def _software_pin(settings_row: EinvoiceSettings) -> sec.Secret:
    if settings_row.software_pin_ref:
        found = sec.resolve(settings_row.software_pin_ref)
        if found is None:
            raise ProviderNotConfigured("the software PIN secret has not been provided by the operator")
        return found
    return sec.ensure_dev_fixture(DEV_SOFTWARE_PIN_REF)


def _technical_key(rng: NumberingRange | None) -> sec.Secret:
    if rng is not None and rng.technical_key_ref:
        found = sec.resolve(rng.technical_key_ref)
        if found is None:
            raise NumberingRangeMissing("the technical key of the numbering range has not been provided")
        return found
    return sec.ensure_dev_fixture(DEV_TECHNICAL_KEY_REF)


def find_range(ctx: TenantContext, company_id: uuid.UUID, family: str, number: str, on: dt.date) -> NumberingRange:
    candidates = NumberingRange.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, status=NumberingRange.Status.ACTIVE, valid_to__gte=on
    ).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=on))
    for rng in candidates.order_by("prefix", "from_number"):
        if rng.family not in (family, "FE" if family.startswith("FE") else family):
            continue
        if not number.startswith(rng.prefix):
            continue
        digits = number[len(rng.prefix) :]
        if digits.isdigit() and rng.from_number <= int(digits) <= rng.to_number:
            return rng
    raise NumberingRangeMissing(f"no active authorized numbering range covers {number} on {on.isoformat()}")


def active_contingency(ctx: TenantContext, company_id: uuid.UUID) -> Contingency | None:
    return Contingency.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, active=True).order_by("-since").first()


def _qr(unique_code: str) -> str:
    return annex.QR_BASE_URL.value + unique_code


# ============================================================================ registration (inside the caller's UoW)


def register_document(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    source: DocumentRef,
    model: FiscalDocumentModel,
    *,
    test_set_id: str = "",
) -> FiscalDocument:
    """Create the fiscal document for ``source`` (idempotent per source). No external I/O."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    existing = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, source_type=source.type,
                                             source_id=source.id, family=model.family).first()  # fmt: skip
    if existing is not None:
        if existing.number != model.number:
            raise Conflict("the source document already has a fiscal document with another number",
                           code="validation_failed")  # fmt: skip
        return existing
    if FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, family=model.family,
                                     number=model.number).exists():  # fmt: skip
        raise Conflict(f"fiscal number {model.number} is already used (numbers are never reused)", code="validation_failed")
    settings_row = get_settings(uow, company_id)
    contingency = active_contingency(ctx, company_id)
    if contingency is not None and contingency.kind == Contingency.Kind.ISSUER_OUTAGE and model.family == "FE":
        model = replace(model, family=annex.CONTINGENCY_FAMILY)
    environment = settings_row.environment
    if mode.is_production():
        for code in (annex.family(model.family)[2],):
            annex.require_verified(code, "document type")
        mode.require_transmission_allowed(environment, gate_records(ctx, company_id))
    rng = None
    if model.family in NUMBERED_FAMILIES:
        rng = find_range(ctx, company_id, model.family, model.number, model.issue_date)
        digits = int(model.number[len(rng.prefix) :])
        if rng.last_used_number is None or digits > rng.last_used_number:
            rng.last_used_number = digits
            rng.save_versioned(ctx.principal_id, ["last_used_number"])
    secret = _technical_key(rng) if model.hash_kind == "CUFE" else _software_pin(settings_row)
    unique_code = hashing.compute(model.hash_kind, model.hash_inputs(environment, secret.reveal()))
    software_id = settings_row.software_id or DEV_SOFTWARE_ID
    xml = builders.build(model, unique_code, environment=environment, software_id=software_id, qr_url=_qr(unique_code))
    doc = FiscalDocument.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, source_type=source.type, source_id=source.id,
        family=model.family, number=model.number, issue_date=model.issue_date, unique_code=unique_code,
        model=model.to_dict(), test_set_id=test_set_id, synthetic=model.synthetic, payable=model.payable,
        simulated=settings_row.adapter == "simulator", created_by=ctx.principal_id, updated_by=ctx.principal_id,
    )  # fmt: skip
    FiscalTransition.objects.create(tenant_id=doc.tenant_id, company_id=company_id, document=doc, to_state=S.PENDING_BUILD,
                                    actor_id=ctx.principal_id, reason="registered")  # fmt: skip
    evidence = store_evidence(doc, FiscalEvidence.Kind.UNSIGNED_XML, xml)
    lifecycle.transition(uow, doc, S.BUILT, reason=f"UBL built (annex {builders.CURRENT_VERSION})", evidence=evidence)
    _audit(uow, "einvoice.document.registered", "einvoice.document", doc.id, company_id,
           {"family": doc.family, "number": doc.number})  # fmt: skip
    return doc


def register_received_document(
    uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, *, number: str, unique_code: str,
    issuer: Party, issue_date: dt.date,
) -> FiscalDocument:  # fmt: skip
    """A supplier invoice received by the company (buyer side), so RADIAN events can reference it."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    family = "FE_RECEIVED"
    existing = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, source_type=source.type,
                                             source_id=source.id, family=family).first()  # fmt: skip
    if existing is not None:
        return existing
    doc = FiscalDocument.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, source_type=source.type, source_id=source.id, family=family,
        number=f"{issuer.id_number}:{number}", issue_date=issue_date, unique_code=unique_code, status=S.ACCEPTED,
        model={"family": family, "number": number, "supplier": {"id_number": issuer.id_number, "name": issuer.name},
               "received": True},
        simulated=True, created_by=ctx.principal_id, updated_by=ctx.principal_id, accepted_at=clock.now(),
    )  # fmt: skip
    FiscalTransition.objects.create(tenant_id=doc.tenant_id, company_id=company_id, document=doc, to_state=S.ACCEPTED,
                                    actor_id=ctx.principal_id, reason="received from the supplier")  # fmt: skip
    return doc


# ============================================================================ worker: sign + transmit (outside any UoW)


def _signing_material(ctx: TenantContext, company_id: uuid.UUID) -> tuple[sec.Secret, signing.CertificateInfo]:
    today = clock.business_date()
    cert = (
        SigningCertificate.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, valid_to__gte=today)
        .exclude(status=SigningCertificate.Status.EXPIRED)
        .order_by("-valid_to")
        .first()
    )
    if cert is not None:
        key = sec.resolve(cert.key_ref)
        if key is None:
            raise CertificateMissing("the signing key of the certificate is not available to the worker")
        return key, signing.CertificateInfo(cert.subject, cert.thumbprint, cert.serial)
    if mode.is_production():
        raise CertificateMissing("no signing certificate (EG-2)")
    key = sec.ensure_dev_fixture()
    return key, signing.CertificateInfo("CN=CERTIFICADO SINTETICO (FIXTURE_NOT_CERTIFIED)",
                                        hashlib.sha256(b"fixture-cert").hexdigest(), "0")  # fmt: skip


def gate_records(ctx: TenantContext, company_id: uuid.UUID) -> frozenset[str]:
    return frozenset(GateRecord.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).values_list("gate", flat=True))


def _sign(uow: UnitOfWork, doc: FiscalDocument) -> None:
    unsigned = latest_evidence(doc, FiscalEvidence.Kind.UNSIGNED_XML)
    if unsigned is None:
        raise InvalidTransition("no built XML to sign")
    key, cert = _signing_material(uow.ctx, doc.company_id)
    signed = signing.sign(read_evidence(unsigned), key, cert, signature_id=str(doc.id))
    evidence = store_evidence(doc, FiscalEvidence.Kind.SIGNED_XML, signed)
    lifecycle.transition(uow, doc, S.SIGNED, reason=f"signed ({signing.SIGNATURE_MODE})", evidence=evidence)


def _prepare(uow: UnitOfWork, document_id: uuid.UUID, force: bool) -> SubmitRequest | None:
    doc = FiscalDocument.objects.select_for_update().filter(pk=document_id).first()
    if doc is None:
        raise NotFound("fiscal document not found")
    if doc.status in FiscalDocument.FINAL:
        return None
    if doc.status == S.BUILT:
        _sign(uow, doc)
    contingency = active_contingency(uow.ctx, doc.company_id)
    if doc.status in (S.SIGNED, S.ERROR_RETRYABLE) and contingency is not None:
        doc.contingency = contingency
        lifecycle.transition(uow, doc, S.CONTINGENCY_ISSUED, reason=f"contingency {contingency.kind}", fields=("contingency",))
        return None
    if doc.status == S.CONTINGENCY_ISSUED:
        if contingency is not None:
            return None
        target = S.TRANSMITTED_LATE
    elif doc.status in (S.SIGNED, S.ERROR_RETRYABLE):
        if doc.status == S.ERROR_RETRYABLE and not force and doc.next_attempt_at and doc.next_attempt_at > clock.now():
            return None
        target = S.SUBMITTED
    else:  # submitted / transmitted_late: another worker owns the attempt
        return None
    signed = latest_evidence(doc, FiscalEvidence.Kind.SIGNED_XML)
    assert signed is not None
    doc.attempts += 1
    doc.submitted_at = clock.now()
    doc.next_attempt_at = None
    lifecycle.transition(uow, doc, target, reason=f"attempt {doc.attempts}",
                         fields=("attempts", "submitted_at", "next_attempt_at"))  # fmt: skip
    model = doc.model or {}
    settings_row = read_settings(uow.ctx, doc.company_id)
    environment = settings_row.environment if settings_row is not None else annex.TEST_ENVIRONMENT
    return SubmitRequest(doc.id, doc.company_id, doc.family, doc.number, doc.unique_code, read_evidence(signed),
                         environment, str(model.get("fixture_outcome", "accept")), doc.attempts,
                         doc.test_set_id)  # fmt: skip


def _consecutive_transient(ctx: TenantContext, company_id: uuid.UUID) -> int:
    count = 0
    for outcome in (
        TransmissionLog.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, radian_event__isnull=True)
        .order_by("-at", "-id")
        .values_list("outcome", flat=True)[: lifecycle.AUTO_CONTINGENCY_AFTER]
    ):
        if outcome != "TRANSIENT":
            break
        count += 1
    return count


def _record(
    uow: UnitOfWork, request: SubmitRequest, adapter_key: str, provider_code: str,
    result: SubmitResult | None, error: str, config_error: bool,
) -> FiscalDocument:  # fmt: skip
    doc = FiscalDocument.objects.select_for_update().get(pk=request.document_id)
    if doc.status not in (S.SUBMITTED, S.TRANSMITTED_LATE) or doc.attempts != request.attempt:
        return doc  # this attempt was already recorded (duplicate delivery of the worker job)
    outcome = result.status if result is not None else ("CONFIG_ERROR" if config_error else "TRANSIENT")
    TransmissionLog.objects.create(tenant_id=doc.tenant_id, company_id=doc.company_id, document=doc,
                                   provider_code=provider_code, adapter_key=adapter_key, attempt=request.attempt,
                                   outcome=outcome, latency_ms=result.latency_ms if result else 0,
                                   error=sec.redact(error)[:1000])  # fmt: skip
    doc.adapter_key, doc.provider_code = adapter_key, provider_code
    if result is None:
        doc.last_error = sec.redact(error)[:1000]
        doc.next_attempt_at = None if config_error else lifecycle.next_attempt(doc.attempts)
        lifecycle.transition(uow, doc, S.ERROR_RETRYABLE, reason=doc.last_error,
                             fields=("last_error", "next_attempt_at", "adapter_key", "provider_code"))  # fmt: skip
        if not config_error and _consecutive_transient(uow.ctx, doc.company_id) >= lifecycle.AUTO_CONTINGENCY_AFTER:
            if active_contingency(uow.ctx, doc.company_id) is None:
                declare_contingency(uow, doc.company_id, Contingency.Kind.PROVIDER_OUTAGE,
                                    f"automatic: {lifecycle.AUTO_CONTINGENCY_AFTER} consecutive transmission failures",
                                    automatic=True)  # fmt: skip
        return doc
    evidence = store_evidence(doc, FiscalEvidence.Kind.RESPONSE, result.response_xml) if result.response_xml else None
    doc.tracking_id = result.tracking_id[:200]
    doc.messages = [sec.redact(m) for m in result.messages]
    doc.error_codes = list(result.error_codes)
    doc.simulated = result.simulated
    doc.last_error = "" if result.status != "REJECTED" else "; ".join(result.error_codes)[:1000]
    target = {"ACCEPTED": S.ACCEPTED, "ACCEPTED_WITH_NOTICES": S.ACCEPTED_WITH_NOTICES, "REJECTED": S.REJECTED}[result.status]
    fields = ["tracking_id", "messages", "error_codes", "simulated", "last_error", "adapter_key", "provider_code"]
    if target != S.REJECTED:
        doc.accepted_at = clock.now()
        fields.append("accepted_at")
    lifecycle.transition(uow, doc, target, reason=f"{result.status} by {adapter_key}", evidence=evidence, fields=tuple(fields))
    if target != S.REJECTED:
        queue_delivery(uow, doc, result.response_xml)
    return doc


def transmit(ctx: TenantContext, document_id: uuid.UUID, *, force: bool = False,
             adapter: ProviderAdapter | None = None) -> FiscalDocument:  # fmt: skip
    """Worker entry point: sign + submit one document. Never inside a unit of work."""
    request = run_in_unit_of_work(lambda u: _prepare(u, document_id, force), ctx)
    if request is None or not isinstance(request, SubmitRequest):
        with tenancy.scope(ctx):
            return FiscalDocument.objects.get(pk=document_id)
    result: SubmitResult | None = None
    error, config_error = "", False
    adapter_key, provider_code = "?", ""
    try:
        if adapter is None:
            with tenancy.scope(ctx):
                adapter, provider_code = adapter_for(read_settings(ctx, request.company_id))
        else:
            provider_code = getattr(adapter, "key", "?")
        adapter_key = adapter.key
        result = adapter.submit(request)
    except TransientTransmissionError as exc:
        error = sec.redact(exc)
    except (ProviderNotConfigured, ProductionRefused, CertificateMissing) as exc:
        error, config_error = sec.redact(exc.detail), True
    recorded = run_in_unit_of_work(
        lambda u: _record(u, request, adapter_key, provider_code, result, error, config_error), ctx
    )
    assert isinstance(recorded, FiscalDocument)
    return recorded


def retry(uow: UnitOfWork, doc: FiscalDocument) -> FiscalDocument:
    """Manual retry (role-restricted): re-queues the same signed document, same number."""
    doc = FiscalDocument.objects.select_for_update().get(pk=doc.pk)
    if doc.status == S.REJECTED:
        raise InvalidTransition("a rejected document is final: correct it with a note or a new document",
                                code="fiscal_rejected")  # fmt: skip
    if doc.status not in (S.ERROR_RETRYABLE, S.BUILT, S.SIGNED, S.CONTINGENCY_ISSUED):
        raise InvalidTransition(f"nothing to retry in state {doc.status}")
    doc.next_attempt_at = clock.now()
    doc.save_versioned(uow.ctx.principal_id, ["next_attempt_at"])
    _audit(uow, "einvoice.document.retry_requested", "einvoice.document", doc.id, doc.company_id)
    return doc


def due_documents(ctx: TenantContext, company_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Documents the worker should process now (built/signed, due retries, contingency backlog)."""
    now = clock.now()
    qs = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))
    if company_id is not None:
        qs = qs.filter(company_id=company_id)
    qs = qs.filter(
        Q(status__in=[S.BUILT, S.SIGNED])
        | Q(status=S.ERROR_RETRYABLE, next_attempt_at__lte=now)
        | Q(status=S.CONTINGENCY_ISSUED, contingency__active=False)
    )
    return list(qs.order_by("created_at", "id").values_list("id", flat=True)[:500])


# ============================================================================ delivery (AttachedDocument ZIP)


def _mask_email(email: str) -> str:
    if "@" not in email:
        return ""
    user, _, domain = email.partition("@")
    return f"{user[:1]}***@{domain}"


def queue_delivery(uow: UnitOfWork, doc: FiscalDocument, response_xml: bytes) -> DeliveryLog:
    """AttachedDocument (signed XML + validation response) in a ZIP, queued for the buyer (local capture in DEV)."""
    signed = latest_evidence(doc, FiscalEvidence.Kind.SIGNED_XML)
    model = doc.model or {}
    customer = model.get("customer") or {}
    supplier = model.get("supplier") or {}
    if signed is None or not customer:
        return DeliveryLog.objects.create(tenant_id=doc.tenant_id, company_id=doc.company_id, document=doc,
                                          status="SKIPPED", detail="no buyer data / signed XML")  # fmt: skip
    from einvoice.builders import ubl

    attached = ubl.attached_document(
        number=f"AD-{doc.number}", issue_date=doc.issue_date, issue_time=str(model.get("issue_time", "00:00:00-05:00")),
        sender=Party(str(supplier.get("id_number", "")), name=str(supplier.get("name", ""))),
        receiver=Party(str(customer.get("id_number", "")), name=str(customer.get("name", ""))),
        document_number=doc.number, document_unique_code=doc.unique_code, signed_document=read_evidence(signed),
        validation_response=response_xml or b"<SinRespuesta/>", environment="2",
    )  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(f"ad{doc.number}.xml", date_time=(2026, 1, 1, 0, 0, 0))
        zf.writestr(info, attached)
    content = buf.getvalue()
    evidence = store_evidence(doc, FiscalEvidence.Kind.ATTACHED_DOCUMENT, content)
    email = str(customer.get("email", ""))
    status, detail = ("QUEUED", "local capture (Mailpit) — no real e-mail in DEV") if email else ("SKIPPED", "buyer has no e-mail")
    return DeliveryLog.objects.create(tenant_id=doc.tenant_id, company_id=doc.company_id, document=doc,
                                      recipient_masked=_mask_email(email), status=status,
                                      attachment_sha256=evidence.sha256, detail=detail)  # fmt: skip


def resend_email(uow: UnitOfWork, doc: FiscalDocument) -> DeliveryLog:
    if doc.status not in (S.ACCEPTED, S.ACCEPTED_WITH_NOTICES):
        raise InvalidTransition("only validated documents are delivered to the buyer")
    response = latest_evidence(doc, FiscalEvidence.Kind.RESPONSE)
    return queue_delivery(uow, doc, read_evidence(response) if response else b"")


def record_bounce(uow: UnitOfWork, doc: FiscalDocument, detail: str) -> DeliveryLog:
    """Bounce handling: a new BOUNCED row (the delivery log is append-only) and an audit entry."""
    row = DeliveryLog.objects.create(tenant_id=doc.tenant_id, company_id=doc.company_id, document=doc, status="BOUNCED",
                                     detail=sec.redact(detail)[:500])  # fmt: skip
    _audit(uow, "einvoice.delivery.bounced", "einvoice.document", doc.id, doc.company_id)
    return row


# ============================================================================ contingency (R-FE-05)


def declare_contingency(uow: UnitOfWork, company_id: uuid.UUID, kind: str, reason: str, *, automatic: bool = False) -> Contingency:
    uow.ctx.require_company(company_id)
    if kind not in Contingency.Kind.values:
        raise ValidationFailed(f"kind must be one of {', '.join(Contingency.Kind.values)}", field="kind")
    current = Contingency.objects.select_for_update().filter(tenant_id=uow.ctx.tenant_id, company_id=company_id,
                                                             active=True).first()  # fmt: skip
    if current is not None:
        return current
    row = Contingency.objects.create(tenant_id=uow.ctx.tenant_id, company_id=company_id, kind=kind, automatic=automatic,
                                     reason=reason[:500], declared_by=uow.ctx.principal_id,
                                     created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id)  # fmt: skip
    _audit(uow, "einvoice.contingency.declared", "einvoice.contingency", row.id, company_id,
           {"kind": kind, "automatic": automatic}, reason)  # fmt: skip
    return row


def end_contingency(uow: UnitOfWork, company_id: uuid.UUID) -> Contingency:
    """Service restored: the 48 h late-transmission deadline starts now."""
    row = Contingency.objects.select_for_update().filter(tenant_id=uow.ctx.tenant_id, company_id=company_id,
                                                         active=True).first()  # fmt: skip
    if row is None:
        raise InvalidTransition("no active contingency")
    now = clock.now()
    row.active, row.restored_at, row.deadline_at, row.ended_by = False, now, deadline_from(now), uow.ctx.principal_id
    row.save_versioned(uow.ctx.principal_id, ["active", "restored_at", "deadline_at", "ended_by"])
    _audit(uow, "einvoice.contingency.ended", "einvoice.contingency", row.id, company_id)
    return row


def deadline_from(restored_at: dt.datetime) -> dt.datetime:
    """R-FE-05: contingency documents must be transmitted within 48 h of the service restoration."""
    return restored_at + CONTINGENCY_DEADLINE


def contingency_overview(ctx: TenantContext, company_id: uuid.UUID) -> dict[str, Any]:
    now = clock.now()
    current = active_contingency(ctx, company_id)
    last = current or Contingency.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("-since").first()
    pending = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status=S.CONTINGENCY_ISSUED)
    deadline = last.deadline_at if last is not None and not last.active else None
    return {
        "id": str(last.id) if last else str(uuid.UUID(int=0)),
        "tenant_id": str(ctx.tenant_id),
        "company_id": str(company_id),
        "active": current is not None,
        "since": current.since.isoformat() if current else None,
        "kind": last.kind if last else None,
        "automatic": bool(last.automatic) if last else False,
        "restored_at": last.restored_at.isoformat() if last and last.restored_at else None,
        "deadline_at": deadline.isoformat() if deadline else None,
        "remaining_seconds": max(0, int((deadline - now).total_seconds())) if deadline else None,
        "overdue": bool(deadline and deadline < now and pending.exists()),
        "pending_documents": pending.count(),
        "version": last.version if last else 0,
    }


# ============================================================================ numbering ranges


def sync_numbering_ranges(ctx: TenantContext, company_id: uuid.UUID, adapter: ProviderAdapter | None = None) -> list[dict[str, Any]]:
    """GetNumberingRange through the active adapter (outside a UoW); upserted by :func:`apply_synced_ranges`."""
    if adapter is None:
        adapter, _ = adapter_for(read_settings(ctx, company_id))
    settings_row = read_settings(ctx, company_id)
    found = adapter.numbering_ranges("", settings_row.software_id if settings_row else "")
    out = []
    for r in found:
        ref = ""
        if r.technical_key is not None:
            ref = f"{sec.PREFIX}range-{r.prefix.lower() or 'np'}-{r.from_number}"
            path = sec.secrets_dir() / ref[len(sec.PREFIX) :]
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(r.technical_key.reveal(), encoding="utf-8")
            path.chmod(0o600)
        out.append({"code": f"{r.prefix or 'NP'}-{r.from_number}", "prefix": r.prefix, "from_number": r.from_number,
                    "to_number": r.to_number, "resolution": r.resolution, "valid_from": r.valid_from,
                    "valid_to": r.valid_to, "technical_key_ref": ref})  # fmt: skip
    return out


def apply_synced_ranges(uow: UnitOfWork, company_id: uuid.UUID, rows: list[dict[str, Any]]) -> list[NumberingRange]:
    out = []
    for row in rows:
        obj = NumberingRange.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, code=row["code"]).first()
        if obj is None:
            obj = NumberingRange.objects.create(tenant_id=uow.ctx.tenant_id, company_id=company_id, source="SYNC",
                                                synthetic=False, created_by=uow.ctx.principal_id, **row)  # fmt: skip
        out.append(obj)
    return out


def range_usage(rng: NumberingRange) -> dict[str, Any]:
    size = rng.to_number - rng.from_number + 1
    used = 0 if rng.last_used_number is None else rng.last_used_number - rng.from_number + 1
    remaining = size - used
    pct = (used * 100) // size if size else 100
    return {"size": size, "used": used, "remaining": remaining, "used_pct": pct,
            "alert": "EXHAUSTED" if remaining <= 0 else "95" if pct >= 95 else "80" if pct >= 80 else None}  # fmt: skip


# ============================================================================ certificates (EG-2)


def certificate_alerts(uow: UnitOfWork, company_id: uuid.UUID, today: dt.date | None = None) -> list[dict[str, Any]]:
    """30/15/7-day expiry alerts (each threshold once per certificate, audited)."""
    day = today or clock.business_date()
    alerts = []
    for cert in SigningCertificate.objects.select_for_update().filter(tenant_id=uow.ctx.tenant_id, company_id=company_id):
        left = (cert.valid_to - day).days
        if left < 0 and cert.status != SigningCertificate.Status.EXPIRED:
            cert.status = SigningCertificate.Status.EXPIRED
            cert.save_versioned(uow.ctx.principal_id, ["status"])
            alerts.append({"certificate_id": str(cert.id), "threshold": 0, "days_left": left})
            continue
        due = [t for t in CERT_ALERT_DAYS if left <= t and t not in cert.alerts_sent]
        if not due or left < 0:
            continue
        threshold = min(due)
        cert.alerts_sent = sorted({*cert.alerts_sent, *due}, reverse=True)
        cert.save_versioned(uow.ctx.principal_id, ["alerts_sent"])
        _audit(uow, "einvoice.certificate.expiry_alert", "einvoice.certificate", cert.id, company_id,
               {"threshold": threshold, "days_left": left})  # fmt: skip
        alerts.append({"certificate_id": str(cert.id), "threshold": threshold, "days_left": left})
    return alerts


# ============================================================================ technology providers (F-PT-OPEN)


def provider_fingerprint(provider: TechnologyProvider) -> str:
    raw = "|".join([provider.adapter, provider.base_url, provider.environment, provider.contract_version,
                    hashlib.sha256(provider.credentials_ref.encode()).hexdigest()])  # fmt: skip
    return hashlib.sha256(raw.encode()).hexdigest()


def run_provider_contract(ctx: TenantContext, provider: TechnologyProvider) -> dict[str, Any]:
    """Contract suite against the provider's adapter (sandbox/simulator only, outside a UoW)."""
    from einvoice.adapters.contract import run_contract_suite

    if provider.environment != "SANDBOX":
        raise ProviderNotConfigured("only SANDBOX providers can be tested")
    config = provider_config(provider)
    probe = config.credentials.reveal() if config.credentials else ""
    report = run_contract_suite(lambda: registry.create(config), secret_probe=probe)
    return sec.redact_obj(report.to_json())  # type: ignore[no-any-return]


def record_provider_test(uow: UnitOfWork, provider: TechnologyProvider, report: dict[str, Any]) -> TechnologyProvider:
    provider = TechnologyProvider.objects.select_for_update().get(pk=provider.pk)
    provider.last_test = report
    provider.last_tested_at = clock.now()
    fields = ["last_test", "last_tested_at"]
    if report.get("passed"):
        provider.tested_fingerprint = provider_fingerprint(provider)
        fields.append("tested_fingerprint")
        if provider.config_state == TechnologyProvider.ConfigState.DRAFT:
            provider.config_state = TechnologyProvider.ConfigState.TESTED
            fields.append("config_state")
    provider.save_versioned(uow.ctx.principal_id, fields)
    uow.emit("einvoice.provider.tested.v1", {"provider_id": str(provider.id), "passed": bool(report.get("passed"))},
             company_id=provider.company_id)  # fmt: skip
    _audit(uow, "einvoice.provider.tested", "einvoice.provider", provider.id, provider.company_id,
           {"passed": bool(report.get("passed"))})  # fmt: skip
    if not report.get("passed"):
        failed = [c["case"] for c in report.get("cases", []) if not c.get("passed")]
        raise ProviderContractTestFailed(f"provider contract suite failed: {', '.join(failed) or 'construct'}",
                                         cases=report.get("cases", []))  # fmt: skip
    return provider


def select_provider(uow: UnitOfWork, settings_row: EinvoiceSettings, provider: TechnologyProvider | None) -> bool:
    """Make ``provider`` the active one. Returns True when applied, False when waiting for a second admin.

    Exactly one active provider per company; the first activation needs one administrator, a *switch*
    from an active provider needs a second, different administrator (F-PT-OPEN, SoD).
    """
    if provider is None:
        return True
    if provider.config_state not in (TechnologyProvider.ConfigState.TESTED, TechnologyProvider.ConfigState.ACTIVE):
        raise ProviderContractTestFailed("the provider has not passed the contract suite (run :test-connection)")
    if provider.tested_fingerprint != provider_fingerprint(provider):
        raise ProviderContractTestFailed("the provider configuration changed since its contract test; test it again")
    current = settings_row.provider
    if current is not None and current.pk != provider.pk and current.config_state == TechnologyProvider.ConfigState.ACTIVE:
        if settings_row.pending_provider_id != provider.pk or settings_row.pending_requested_by in (None, uow.ctx.principal_id):
            settings_row.pending_provider = provider
            settings_row.pending_requested_by = uow.ctx.principal_id
            settings_row.save_versioned(uow.ctx.principal_id, ["pending_provider", "pending_requested_by"])
            _audit(uow, "einvoice.provider.switch_requested", "einvoice.provider", provider.id, provider.company_id)
            return False
    for other in TechnologyProvider.objects.select_for_update().filter(
        tenant_id=provider.tenant_id, company_id=provider.company_id, config_state=TechnologyProvider.ConfigState.ACTIVE
    ).exclude(pk=provider.pk):
        other.config_state = TechnologyProvider.ConfigState.RETIRED
        other.save_versioned(uow.ctx.principal_id, ["config_state"])
    if provider.config_state != TechnologyProvider.ConfigState.ACTIVE:
        provider.config_state = TechnologyProvider.ConfigState.ACTIVE
        provider.save_versioned(uow.ctx.principal_id, ["config_state"])
    settings_row.provider = provider
    settings_row.pending_provider = None
    settings_row.pending_requested_by = None
    _audit(uow, "einvoice.provider.activated", "einvoice.provider", provider.id, provider.company_id)
    return True


# ============================================================================ RADIAN


def _event_rules(uow: UnitOfWork, doc: FiscalDocument, code: str) -> None:
    seen = set(RadianEvent.objects.filter(fiscal_document=doc).exclude(status=RadianEvent.Status.REJECTED)
               .values_list("event_code", flat=True))  # fmt: skip
    if code in seen:
        raise Conflict(f"RADIAN event {code} already registered for this document", code="validation_failed")
    if code in ("031", "033", "034") and "032" not in seen:
        raise ValidationFailed("reclamo/aceptación need the 'recibo del bien' event (032) first", field="event_code")
    if code in ("031", "033", "034") and seen & {"031", "033", "034"}:
        raise ValidationFailed("the invoice already has an acceptance or a claim", field="event_code")


def resolve_document(ctx: TenantContext, company_id: uuid.UUID, ref: DocumentRef) -> FiscalDocument:
    qs = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
    doc = qs.filter(pk=ref.id).first() if ref.type == "einvoice.document" else None
    doc = doc or qs.filter(source_type=ref.type, source_id=ref.id).order_by("created_at").first()
    if doc is None:
        raise NotFound("fiscal document not found for the reference")
    return doc


def emit_radian_event(
    uow: UnitOfWork, company_id: uuid.UUID, ref: DocumentRef, event_code: str, *,
    claim_concept: str = "", system: bool = False,
) -> RadianEvent:  # fmt: skip
    """Build, number and store an ApplicationResponse for ``event_code`` (transmitted by the worker)."""
    code = annex.radian_event(event_code)
    if code.value in annex.SYSTEM_ONLY_EVENTS and not system:
        raise ValidationFailed("tacit acceptance (034) is computed by the gateway, never requested", field="event_code")
    if mode.is_production():
        annex.require_verified(code, "RADIAN event code")
    if event_code == "031" and claim_concept not in annex.CLAIM_CONCEPTS:
        raise ValidationFailed("a claim needs a claim concept (Concepto de Reclamo)", field="claim_concept")
    doc = resolve_document(uow.ctx, company_id, ref)
    if doc.status not in (S.ACCEPTED, S.ACCEPTED_WITH_NOTICES):
        raise ValidationFailed("RADIAN events reference validated invoices only", field="document")
    _event_rules(uow, doc, event_code)
    settings_row = get_settings(uow, company_id)
    model = doc.model or {}
    supplier = Party(str((model.get("supplier") or {}).get("id_number", "0")))
    customer = Party(str((model.get("customer") or {}).get("id_number", "0")))
    received = bool(model.get("received"))
    sender, receiver = (customer, supplier) if not received else (supplier, customer)
    if received:
        sender = Party("0")  # the buying company itself (its NIT is carried by P11 data; synthetic here)
    seq = RadianEvent.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id).count() + 1
    number = f"EV{seq:08d}"
    now = clock.now().astimezone(clock.tz())
    issue_time = now.strftime("%H:%M:%S%z")
    issue_time = f"{issue_time[:-2]}:{issue_time[-2:]}"
    pin = _software_pin(settings_row)
    doc_number = str(model.get("number", doc.number))
    type_code = "01"
    unique = hashing.compute("CUDE_AR", {"NumDE": number, "FecEmi": now.date().isoformat(), "HorEmi": issue_time,
                                         "NitFE": sender.id_number, "DocAdq": receiver.id_number,
                                         "ResponseCode": event_code, "ID": doc_number, "DocumentTypeCode": type_code,
                                         "SoftwarePIN": pin.reveal()})  # fmt: skip
    event = RadianEvent.objects.create(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, document_type=ref.type, document_id=ref.id,
        fiscal_document=doc, document_number=doc_number, document_unique_code=doc.unique_code, event_code=event_code,
        direction=RadianEvent.Direction.EMITTED, number=number, unique_code=unique, claim_concept=claim_concept,
        system_generated=system, created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    from einvoice.builders import ubl

    xml = ubl.application_response(
        number=number, issue_date=now.date(), issue_time=issue_time, unique_code=unique,
        environment=settings_row.environment, sender=sender, receiver=receiver, response_code=event_code,
        response_label=code.label, document_number=doc_number, document_unique_code=doc.unique_code,
        document_type_code=type_code, claim_concept=claim_concept,
        software_id=settings_row.software_id or DEV_SOFTWARE_ID,
    )  # fmt: skip
    store_evidence(None, FiscalEvidence.Kind.EVENT_XML, xml, event=event)
    _audit(uow, "einvoice.radian.emitted", "einvoice.radian_event", event.id, company_id, {"event_code": event_code})
    return event


def record_received_event(uow: UnitOfWork, company_id: uuid.UUID, ref: DocumentRef, event_code: str,
                          occurred_at: dt.datetime | None = None) -> RadianEvent:  # fmt: skip
    """An event received from the buyer about one of our invoices (e.g. 032 recibo del bien)."""
    annex.radian_event(event_code)
    doc = resolve_document(uow.ctx, company_id, ref)
    _event_rules(uow, doc, event_code)
    event = RadianEvent.objects.create(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, document_type=ref.type, document_id=ref.id,
        fiscal_document=doc, document_number=doc.number, document_unique_code=doc.unique_code, event_code=event_code,
        direction=RadianEvent.Direction.RECEIVED, status=RadianEvent.Status.RECORDED,
        occurred_at=occurred_at or clock.now(), created_by=uow.ctx.principal_id, updated_by=uow.ctx.principal_id,
    )  # fmt: skip
    return event


def transmit_event(ctx: TenantContext, event_id: uuid.UUID, adapter: ProviderAdapter | None = None) -> RadianEvent:
    """Worker: sign + submit an emitted RADIAN event."""

    def prepare(uow: UnitOfWork) -> SubmitRequest | None:
        ev = RadianEvent.objects.select_for_update().get(pk=event_id)
        if ev.status != RadianEvent.Status.PENDING or ev.direction != RadianEvent.Direction.EMITTED:
            return None
        xml = FiscalEvidence.objects.filter(radian_event=ev, kind=FiscalEvidence.Kind.EVENT_XML).order_by("-created_at").first()
        assert xml is not None
        key, cert = _signing_material(uow.ctx, ev.company_id)
        signed = signing.sign(read_evidence(xml), key, cert, signature_id=str(ev.id))
        store_evidence(None, FiscalEvidence.Kind.SIGNED_XML, signed, event=ev)
        ev.status = RadianEvent.Status.SUBMITTED
        ev.save_versioned(uow.ctx.principal_id, ["status"])
        return SubmitRequest(ev.id, ev.company_id, "RADIAN", ev.number, ev.unique_code, signed, kind="event")

    request = run_in_unit_of_work(prepare, ctx)
    if not isinstance(request, SubmitRequest):
        with tenancy.scope(ctx):
            return RadianEvent.objects.get(pk=event_id)
    result: SubmitResult | None = None
    error = ""
    adapter_key, provider_code = "?", ""
    try:
        if adapter is None:
            with tenancy.scope(ctx):
                adapter, provider_code = adapter_for(read_settings(ctx, request.company_id))
        adapter_key = adapter.key
        result = adapter.submit(request)
    except (TransientTransmissionError, DomainError) as exc:
        error = sec.redact(exc)

    def record(uow: UnitOfWork) -> RadianEvent:
        ev = RadianEvent.objects.select_for_update().get(pk=event_id)
        if ev.status != RadianEvent.Status.SUBMITTED:
            return ev
        TransmissionLog.objects.create(tenant_id=ev.tenant_id, company_id=ev.company_id, radian_event=ev,
                                       provider_code=provider_code, adapter_key=adapter_key, attempt=1,
                                       outcome=result.status if result else "TRANSIENT", error=error[:1000])  # fmt: skip
        if result is None:
            ev.status = RadianEvent.Status.PENDING
        else:
            ev.status = RadianEvent.Status.REJECTED if result.status == "REJECTED" else RadianEvent.Status.ACCEPTED
            ev.tracking_id = result.tracking_id[:200]
            ev.messages = [sec.redact(m) for m in result.messages]
            if result.response_xml:
                store_evidence(None, FiscalEvidence.Kind.RESPONSE, result.response_xml, event=ev)
        ev.save_versioned(uow.ctx.principal_id, ["status", "tracking_id", "messages"])
        return ev

    out = run_in_unit_of_work(record, ctx)
    assert isinstance(out, RadianEvent)
    return out


def tacit_acceptance_due(ctx: TenantContext, company_id: uuid.UUID, today: dt.date | None = None) -> list[uuid.UUID]:
    """Invoices with 'recibo del bien' (032) and no claim/acceptance after 3 business days (R-FE-10)."""
    from localization_co import holidays

    day = today or clock.business_date()
    due = []
    received = RadianEvent.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, event_code="032")
    for ev in received.select_related("fiscal_document"):
        doc = ev.fiscal_document
        if doc is None or (doc.model or {}).get("received"):
            continue
        start = ev.occurred_at.astimezone(clock.tz()).date()
        if holidays.add_business_days(start, 3) >= day:
            continue
        if RadianEvent.objects.filter(fiscal_document=doc, event_code__in=["031", "033", "034"]).exists():
            continue
        due.append(doc.id)
    return due


def apply_tacit_acceptance(uow: UnitOfWork, company_id: uuid.UUID, today: dt.date | None = None) -> list[RadianEvent]:
    out = []
    for doc_id in tacit_acceptance_due(uow.ctx, company_id, today):
        out.append(emit_radian_event(uow, company_id, DocumentRef("einvoice.document", doc_id), "034", system=True))
    return out
