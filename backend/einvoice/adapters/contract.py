"""Provider-adapter contract suite (T-P15-11, T-PT-OPEN-01): every adapter must pass it before activation.

``run_contract_suite(factory, secret_probe=...)`` is plain Python (no pytest) so the same suite runs
in the tests, in ``POST /providers/{id}:test-connection`` (draft → tested) and from the habilitación
helper. Each case gets a fresh adapter from ``factory`` and a synthetic signed document.
"""

from __future__ import annotations

import datetime as dt
import secrets as std_secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from einvoice import builders, hashing, signing
from einvoice.adapters.base import FINAL_STATUSES, ProviderAdapter, SubmitRequest, SubmitResult
from einvoice.errors import AdapterNotSupported, ProductionRefused, ProviderNotConfigured, TransientTransmissionError
from einvoice.model import FiscalDocumentModel, Line, Party, TaxLine
from einvoice.secrets import Secret
from kernel import io_guard
from kernel.errors import ExternalIOForbidden

SUITE_VERSION = "pt-contract-1"


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ContractReport:
    adapter: str
    suite_version: str
    cases: tuple[CaseResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(c.passed for c in self.cases)

    def to_json(self) -> dict[str, object]:
        return {"adapter": self.adapter, "suite_version": self.suite_version, "passed": self.passed,
                "cases": [{"case": c.case, "passed": c.passed, "detail": c.detail} for c in self.cases]}  # fmt: skip


def sample_model(number: str = "SETT990000001") -> FiscalDocumentModel:
    line = Line("Servicio sintético de prueba", Decimal("1"), Decimal("100000.00"), Decimal("100000.00"),
                (TaxLine("01", Decimal("19.00"), Decimal("100000.00"), Decimal("19000.00")),))  # fmt: skip
    return FiscalDocumentModel(
        family="FE", number=number, issue_date=dt.date(2026, 11, 2), issue_time="10:00:00-05:00",
        supplier=Party("900000001", "31", "1", "EMPRESA SINTÉTICA S.A.S."),
        customer=Party("222222222222", "13", "", "CONSUMIDOR SINTÉTICO"),
        lines=(line,), line_extension=Decimal("100000.00"), tax_exclusive=Decimal("100000.00"),
        tax_inclusive=Decimal("119000.00"), payable=Decimal("119000.00"),
    )  # fmt: skip


def sample_request(outcome: str = "accept", *, attempt: int = 1, document_id: uuid.UUID | None = None) -> SubmitRequest:
    model = sample_model()
    key = std_secrets.token_hex(20)
    code = hashing.compute("CUFE", model.hash_inputs("2", key))
    xml = builders.build(model, code, environment="2", software_id="SYN-SOFTWARE", qr_url="https://example.invalid/qr")
    signed = signing.sign(xml, Secret("contract-suite", key), signing.CertificateInfo("CN=SINTETICO", "00" * 32),
                          signature_id="contract")  # fmt: skip
    return SubmitRequest(document_id or uuid.UUID("00000000-0000-7000-8000-00000000c0de"), uuid.UUID(int=1), "FE",
                         model.number, code, signed, "2", outcome, attempt)  # fmt: skip


def _no_leak(result: SubmitResult, probe: str) -> bool:
    if not probe:
        return True
    text = repr(result) + " ".join(result.messages) + " ".join(result.error_codes) + result.response_xml.decode("utf-8", "replace")
    return probe not in text


def run_contract_suite(factory: Callable[[], ProviderAdapter], *, secret_probe: str = "") -> ContractReport:
    cases: list[CaseResult] = []
    name = "?"

    def case(label: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn()
            cases.append(CaseResult(label, True, detail or ""))
        except AssertionError as exc:
            cases.append(CaseResult(label, False, str(exc)[:300]))
        except Exception as exc:  # noqa: BLE001 - a crashing adapter fails the case, never the suite
            text = str(exc)
            if secret_probe and secret_probe in text:
                text = "(message withheld: it contained a credential)"
            cases.append(CaseResult(label, False, f"{type(exc).__name__}: {text}"[:300]))

    try:
        first = factory()
        name = first.key
    except Exception as exc:  # noqa: BLE001 - adapter construction failure is a contract failure
        return ContractReport(name, SUITE_VERSION, (CaseResult("construct", False, f"{type(exc).__name__}: {exc}"[:300]),))

    def identity() -> str:
        assert isinstance(first, ProviderAdapter), "does not implement the ProviderAdapter protocol"
        assert first.key and isinstance(first.key, str), "adapter key missing"
        assert first.certified is False, "no adapter may claim certification in the DEV-only edition"
        return f"key={first.key} simulated={first.simulated}"

    def accepts() -> str:
        req = sample_request("accept")
        res = factory().submit(req)
        assert res.status == "ACCEPTED", f"expected ACCEPTED, got {res.status}"
        assert res.unique_code in (None, req.unique_code), "adapter altered the CUFE"
        assert res.tracking_id, "no tracking id"
        assert _no_leak(res, secret_probe), "credential leaked into the result"
        return res.tracking_id[:12]

    def idempotent() -> None:
        adapter = factory()
        req = sample_request("accept")
        a, b = adapter.submit(req), adapter.submit(req)
        assert (a.status, a.tracking_id) == (b.status, b.tracking_id), "re-submission changed the outcome"

    def notices() -> None:
        res = factory().submit(sample_request("accept_with_notices"))
        assert res.status == "ACCEPTED_WITH_NOTICES" and res.messages, "notices not reported"

    def rejects() -> None:
        res = factory().submit(sample_request("reject"))
        assert res.status == "REJECTED" and res.final, f"expected a final REJECTED, got {res.status}"
        assert res.error_codes, "rejection without error codes"
        assert _no_leak(res, secret_probe), "credential leaked into a rejection"

    def transient() -> None:
        adapter = factory()
        try:
            res = adapter.submit(sample_request("transient", attempt=1))
        except TransientTransmissionError as exc:
            assert not (secret_probe and secret_probe in str(exc)), "credential leaked into a transport error"
        else:
            raise AssertionError(f"a transient failure must raise TransientTransmissionError, got {res.status}")
        res2 = adapter.submit(sample_request("transient", attempt=2))
        assert res2.status == "ACCEPTED", "the retry after a transient failure was not accepted"

    def status_poll() -> None:
        adapter = factory()
        req = sample_request("accept")
        res = adapter.submit(req)
        polled = adapter.status(req, res.tracking_id)
        assert polled.status == res.status and polled.status in FINAL_STATUSES, "status poll disagrees"

    def tampered() -> str:
        # The PT/DIAN re-validates; the adapter must return its final answer and never hide a rejection.
        req = sample_request("accept")
        res = factory().submit(replace(req, xml=req.xml.replace(b"</cbc:ID>", b"9</cbc:ID>", 1)))
        assert res.status in FINAL_STATUSES, f"non-final answer {res.status}"
        return f"status={res.status}"

    def refuses_in_uow() -> None:
        token = io_guard.enter_uow()
        try:
            factory().submit(sample_request("accept"))
        except ExternalIOForbidden:
            return
        finally:
            io_guard.exit_uow(token)
        raise AssertionError("the adapter performed a call inside a unit of work")

    def production_refused() -> None:
        try:
            factory().submit(replace(sample_request("accept"), environment="1"))
        except (ProductionRefused, ProviderNotConfigured):
            return
        raise AssertionError("environment 1 (producción) was not refused")

    def numbering() -> str:
        try:
            ranges = factory().numbering_ranges("900000001", "SYN-SOFTWARE")
        except AdapterNotSupported:
            return "not supported (manual load)"
        assert isinstance(ranges, list), "numbering_ranges must return a list"
        return f"{len(ranges)} range(s)"

    case("identity", identity)
    case("accepts_valid_document", accepts)
    case("idempotent_resubmission", idempotent)
    case("accepts_with_notices", notices)
    case("rejects_with_error_codes", rejects)
    case("transient_is_retryable", transient)
    case("status_poll", status_poll)
    case("tampered_document_final_answer", tampered)
    case("refuses_inside_unit_of_work", refuses_in_uow)
    case("production_environment_refused", production_refused)
    case("numbering_ranges", numbering)
    return ContractReport(name, SUITE_VERSION, tuple(cases))
