"""F-PT-OPEN — open architecture for DIAN technology providers (T-PT-OPEN-01, T-PT-OPEN-02)."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from typing import Any

import pytest

from einvoice import service, worker
from einvoice.adapters import registry
from einvoice.adapters.base import AdapterConfig
from einvoice.adapters.contract import run_contract_suite
from einvoice.adapters.http_provider import HttpProviderAdapter
from einvoice.adapters.simulator import SandboxSimulator
from einvoice.models import FiscalDocument, TechnologyProvider, TransmissionLog
from einvoice.secrets import Secret
from einvoice.testing.dummy import InMemoryPtSandbox, LedgerDummyAdapter
from einvoice.testing.fixtures import ApiClient, create_range, grant_all, invoice_model, register, write_secret
from kernel import outbox, tenancy
from kernel.models import OutboxMessage
from kernel.testing.tenancy import make_context

pytestmark = pytest.mark.django_db

PT_TOKEN = "pt-sandbox-token-" + "7f3a9c51e2d84b06"  # synthetic credential of the in-memory PT


@pytest.fixture(autouse=True)
def _dummy_adapter() -> Any:
    LedgerDummyAdapter.ledger.clear()
    LedgerDummyAdapter.calls.clear()
    registry.register_adapter("dummy-pt", LedgerDummyAdapter)
    yield
    registry.unregister_adapter("dummy-pt")


def _http_factory(sandbox: InMemoryPtSandbox, token: str = PT_TOKEN) -> Any:
    config = AdapterConfig(adapter="http_generic", provider_code="PT1", base_url="https://pt-sandbox.invalid/api",
                           environment="SANDBOX", credentials=Secret("ref", token), options={"transport": sandbox})  # fmt: skip
    return lambda: HttpProviderAdapter(config)


# ============================================================================ T-PT-OPEN-01


def test_T_PT_OPEN_01_contract_suite_passes_for_simulator_and_a_second_dummy_adapter() -> None:
    sim = run_contract_suite(SandboxSimulator)
    dummy = run_contract_suite(lambda: LedgerDummyAdapter(AdapterConfig("dummy-pt", credentials=Secret("r", PT_TOKEN))),
                               secret_probe=PT_TOKEN)  # fmt: skip
    sandbox = InMemoryPtSandbox(expected_token=PT_TOKEN)
    http = run_contract_suite(_http_factory(sandbox), secret_probe=PT_TOKEN)
    for report in (sim, dummy, http):
        assert report.passed, report.to_json()
        assert {c.case for c in report.cases} >= {
            "accepts_valid_document", "idempotent_resubmission", "rejects_with_error_codes", "transient_is_retryable",
            "refuses_inside_unit_of_work", "production_environment_refused", "status_poll",
        }  # fmt: skip
    assert (sim.adapter, dummy.adapter, http.adapter) == ("simulator", "dummy-pt", "http_generic")
    # the credential went only in the Authorization header, never in bodies or other headers
    assert sandbox.requests
    for req in sandbox.requests:
        assert req["headers"]["Authorization"] == f"Bearer {PT_TOKEN}"
        assert PT_TOKEN.encode() not in req["body"]
        assert all(PT_TOKEN not in v for k, v in req["headers"].items() if k != "Authorization")


def test_T_PT_OPEN_01_contract_suite_detects_a_non_conforming_adapter() -> None:
    class Broken(SandboxSimulator):
        key = "broken"

        def submit(self, request: Any) -> Any:  # ignores the fixture switch, re-numbers the CUFE, no io_guard
            from einvoice.adapters.base import SubmitResult

            return SubmitResult("ACCEPTED", "T1", "0" * 96, True)

    report = run_contract_suite(Broken)
    assert not report.passed
    failed = {c.case for c in report.cases if not c.passed}
    assert {"accepts_valid_document", "rejects_with_error_codes", "refuses_inside_unit_of_work"} <= failed


def test_T_PT_OPEN_01_pluggable_provider_end_to_end_through_api_and_worker(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID, einvoice_secrets_dir: Any
) -> None:
    """A second adapter is plugged in by key only: create → contract test → activate → documents flow through it."""
    grant_all(policy, ctx)
    ref = write_secret(einvoice_secrets_dir, "pt-dummy-credentials", PT_TOKEN)
    resp = api.command("post", "/api/v1/einvoice/providers", {
        "code": "DUMMY", "name": "PT sintético", "adapter": "dummy-pt", "base_url": "https://pt-sandbox.invalid",
        "environment": "SANDBOX", "credentials_ref": ref, "contract_version": "generic-json-v1",
    })  # fmt: skip
    assert resp.status_code == 201, resp.json()
    provider_id = resp.json()["id"]

    # activation before the contract suite passed is refused
    early = api.command("put", "/api/v1/einvoice/settings",
                        {"mode": "PROVIDER_SANDBOX", "adapter": "provider", "provider_id": provider_id})  # fmt: skip
    assert early.status_code == 422 and early.json()["code"] == "provider_contract_test_failed"

    tested = api.command("post", f"/api/v1/einvoice/providers/{provider_id}:test-connection", {})
    assert tested.status_code == 200, tested.json()
    body = tested.json()
    assert body["data"]["report"]["passed"] is True and body["data"]["config_state"] == "TESTED"
    with tenancy.scope(ctx):
        assert OutboxMessage.objects.filter(event_type="einvoice.provider.tested.v1").exists()

    activated = api.command("put", "/api/v1/einvoice/settings",
                            {"mode": "PROVIDER_SANDBOX", "adapter": "provider", "provider_id": provider_id})  # fmt: skip
    assert activated.status_code == 200, activated.json()
    assert activated.json()["provider_id"] == provider_id

    create_range(ctx, company)
    doc = register(ctx, company, invoice_model("SETP990000010"))
    # the business transaction committed: the worker (outbox consumer) transmits after commit
    with tenancy.scope(ctx):
        msg = OutboxMessage.objects.filter(event_type="einvoice.document.status_changed.v1").order_by("created_at").last()
    assert msg is not None
    assert outbox.dispatch(msg.envelope)[worker.CONSUMER] == "processed"
    with tenancy.scope(ctx):
        doc = FiscalDocument.objects.get(pk=doc.id)
        log_row = TransmissionLog.objects.get(document=doc)
    assert doc.status == "accepted" and doc.adapter_key == "dummy-pt" and doc.provider_code == "DUMMY"
    assert log_row.outcome == "ACCEPTED" and LedgerDummyAdapter.calls.count(doc.unique_code) == 1


def test_T_PT_OPEN_01_switching_provider_requires_a_second_administrator(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID, einvoice_secrets_dir: Any
) -> None:
    grant_all(policy, ctx)
    ref = write_secret(einvoice_secrets_dir, "pt-dummy-credentials", PT_TOKEN)
    ids = []
    for code in ("PTA", "PTB"):
        r = api.command("post", "/api/v1/einvoice/providers", {"code": code, "name": code, "adapter": "dummy-pt",
                        "base_url": "https://pt-sandbox.invalid", "environment": "SANDBOX", "credentials_ref": ref})  # fmt: skip
        ids.append(r.json()["id"])
        assert api.command("post", f"/api/v1/einvoice/providers/{ids[-1]}:test-connection", {}).status_code == 200
    put = {"mode": "PROVIDER_SANDBOX", "adapter": "provider"}
    assert api.command("put", "/api/v1/einvoice/settings", {**put, "provider_id": ids[0]}).json()["provider_id"] == ids[0]
    pending = api.command("put", "/api/v1/einvoice/settings", {**put, "provider_id": ids[1]})
    assert pending.status_code == 200 and pending.json()["provider_id"] == ids[0] and "Warning" in pending
    second = make_context(tenant_id=ctx.tenant_id, company_ids=ctx.company_ids)
    grant_all(policy, second, ("einvoice.settings.admin",))
    api.use(second)
    approved = api.command("put", "/api/v1/einvoice/settings", {**put, "provider_id": ids[1]})
    assert approved.json()["provider_id"] == ids[1]
    with tenancy.scope(ctx):
        states = dict(TechnologyProvider.objects.values_list("code", "config_state"))
    assert states == {"PTA": "RETIRED", "PTB": "ACTIVE"}  # exactly one active provider per company


# ============================================================================ T-PT-OPEN-02


def _all_text(*parts: Any) -> str:
    return "\n".join(p if isinstance(p, str) else json.dumps(p, default=str, ensure_ascii=False) for p in parts)


def test_T_PT_OPEN_02_provider_secrets_never_in_api_responses_logs_or_exports(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID, einvoice_secrets_dir: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    caplog.set_level(logging.DEBUG)
    grant_all(policy, ctx)
    sandbox = InMemoryPtSandbox(expected_token=PT_TOKEN)
    registry.register_adapter("pt-http-test", lambda cfg: HttpProviderAdapter(
        AdapterConfig(**{**{f: getattr(cfg, f) for f in cfg.__dataclass_fields__}, "options": {"transport": sandbox}})))  # fmt: skip
    pin = "pin-" + "5521983377"
    tech_key = "tk-" + "a9b8c7d6e5f40312"
    ref = write_secret(einvoice_secrets_dir, "pt1-credentials", PT_TOKEN)
    pin_ref = write_secret(einvoice_secrets_dir, "software-pin", pin)
    tk_ref = write_secret(einvoice_secrets_dir, "range-setp-technical-key", tech_key)
    secrets_and_refs = (PT_TOKEN, pin, tech_key, ref, pin_ref, tk_ref)
    responses: list[Any] = []
    try:
        created = api.command("post", "/api/v1/einvoice/providers", {
            "code": "PT1", "name": "PT HTTP sintético", "adapter": "pt-http-test",
            "base_url": "https://pt-sandbox.invalid/api", "environment": "SANDBOX", "credentials_ref": ref,
        })  # fmt: skip
        assert created.status_code == 201, created.json()
        pid = created.json()["id"]
        responses.append(created.json())
        responses.append(api.command("post", f"/api/v1/einvoice/providers/{pid}:test-connection", {}).json())
        responses.append(api.command("put", "/api/v1/einvoice/settings", {"mode": "PROVIDER_SANDBOX", "adapter": "provider",
                                     "provider_id": pid, "software_pin_ref": pin_ref, "software_id": "SW-SYN"}).json())  # fmt: skip
        rng = api.command("post", "/api/v1/einvoice/numbering-ranges", {
            "code": "FE-SETP", "prefix": "SETP", "from_number": 990000000, "to_number": 995000000,
            "resolution": "18760000001", "valid_to": "2027-12-31", "technical_key_ref": tk_ref,
        })  # fmt: skip
        assert rng.status_code == 201, rng.json()
        responses.append(rng.json())
        # documents: accepted, rejected and a transient failure whose transport error mentions the token
        docs = [register(ctx, company, invoice_model(f"SETP99000002{i}", outcome)) for i, outcome in
                enumerate(("accept", "reject", "transient"))]  # fmt: skip
        sandbox_send = sandbox.send

        def leaky_send(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
            if b'"transient"' in (body or b"") or headers.get("X-Fixture-Outcome") == "transient":
                from einvoice.errors import TransientTransmissionError

                raise TransientTransmissionError(f"connection reset (sent {headers['Authorization']})")
            return sandbox_send(method, url, headers, body, timeout)

        sandbox.send = leaky_send  # type: ignore[method-assign]
        for d in docs:
            service.transmit(ctx, d.id)
        for path in ("providers", f"providers/{pid}", "settings", "numbering-ranges", "documents",
                     "contingency", "radian/events", "certificates"):  # fmt: skip
            r = api.get(f"/api/v1/einvoice/{path}")
            assert r.status_code == 200, (path, r.content)
            responses.append(r.json())
        for d in docs:
            responses.append(api.get(f"/api/v1/einvoice/documents/{d.id}").json())
            responses.append(api.get(f"/api/v1/einvoice/documents/{d.id}/events").json())
            responses.append(api.get(f"/api/v1/einvoice/documents/{d.id}/xml").content.decode())
        responses.append(api.get("/api/v1/kpis/facturacion").json())
        export = api.get("/api/v1/einvoice/providers:export")
        assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(export.content.decode())))
        assert rows[1][0] == "PT1" and rows[1][8] == "configurada (referencia oculta)"
        responses.append(export.content.decode())
        # the token was really used against the PT (proves the secret path works)
        assert any(req["headers"].get("Authorization") == f"Bearer {PT_TOKEN}" for req in sandbox.requests)
        with tenancy.scope(ctx):
            stored = [
                list(TransmissionLog.objects.values("error", "outcome")),
                list(FiscalDocument.objects.values("messages", "last_error", "error_codes", "model")),
                list(OutboxMessage.objects.values("envelope")),
            ]
        assert any(row["outcome"] == "TRANSIENT" for row in stored[0])
        text = _all_text(*responses, *stored, caplog.text, [r.getMessage() for r in caplog.records])
        for value in secrets_and_refs:
            assert value not in text, "a provider secret or its reference leaked"
    finally:
        registry.unregister_adapter("pt-http-test")


def test_T_PT_OPEN_02_secret_values_are_refused_as_input_and_redacted_in_logs(
    api: ApiClient, policy: Any, ctx: Any, einvoice_secrets_dir: Any, caplog: pytest.LogCaptureFixture
) -> None:
    from einvoice.secrets import RedactingFilter, resolve

    grant_all(policy, ctx)
    raw = api.command("post", "/api/v1/einvoice/providers", {"code": "X", "name": "X", "adapter": "simulator",
                      "base_url": "https://pt-sandbox.invalid", "environment": "SANDBOX", "credentials_ref": PT_TOKEN})  # fmt: skip
    assert raw.status_code == 422 and raw.json()["code"] == "field_forbidden"
    assert PT_TOKEN not in raw.content.decode()
    # a resolved secret is redacted from any log record, even when code logs it by mistake
    ref = write_secret(einvoice_secrets_dir, "leak-probe", PT_TOKEN)
    secret = resolve(ref)
    assert secret is not None and PT_TOKEN not in repr(secret) and PT_TOKEN not in str(secret)
    logger = logging.getLogger("einvoice.tests.leak")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="einvoice.tests.leak"):
        logger.info("calling PT with %s", secret.reveal(), extra={"token": secret.reveal()})
    assert PT_TOKEN not in caplog.text and "***" in caplog.text
    assert all(PT_TOKEN not in str(getattr(r, "token", "")) for r in caplog.records)
