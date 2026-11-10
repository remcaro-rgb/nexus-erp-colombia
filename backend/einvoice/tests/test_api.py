"""einvoice REST API: deny by default, isolation, idempotency, numbering, contingency, RADIAN, habilitación, KPIs."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from einvoice import service, worker
from einvoice.models import FiscalDocument
from einvoice.testing.fixtures import ApiClient, create_range, grant_all, invoice_model, register
from kernel import tenancy
from kernel.testing.tenancy import make_context

pytestmark = pytest.mark.django_db

RANGE = {
    "code": "FE-SETP",
    "prefix": "SETP",
    "from_number": 990000000,
    "to_number": 990000010,
    "resolution": "18760000001",
    "valid_to": "2027-12-31",
}


def test_T_P15_api_deny_by_default_and_tenant_isolation(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID
) -> None:
    for path in (
        "documents",
        "numbering-ranges",
        "providers",
        "settings",
        "contingency",
        "radian/events",
        "habilitation/test-sets",
        "certificates",
        "providers:export",
    ):
        assert api.get(f"/api/v1/einvoice/{path}").status_code == 403, path
    assert api.get("/api/v1/kpis/facturacion").status_code == 403
    grant_all(policy, ctx)
    create_range(ctx, company)
    doc = register(ctx, company, invoice_model("SETP990000001"))
    assert api.get(f"/api/v1/einvoice/documents/{doc.id}").status_code == 200
    other = make_context()
    grant_all(policy, other)
    api.use(other)
    assert api.get(f"/api/v1/einvoice/documents/{doc.id}").status_code == 404  # never 403 for foreign ids
    assert api.get("/api/v1/einvoice/documents").json()["items"] == []


def test_T_P15_api_documents_flow_retry_events_and_xml(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID
) -> None:
    grant_all(policy, ctx)
    create_range(ctx, company)
    doc = register(ctx, company, invoice_model("SETP990000002", "transient"))
    doc = service.transmit(ctx, doc.id)
    assert doc.status == "error_retryable"
    listed = api.get("/api/v1/einvoice/documents?estado=error_retryable").json()["items"]
    assert [d["id"] for d in listed] == [str(doc.id)] and listed[0]["fiscal_status"] == "SENT"
    key = f"retry-{uuid.uuid4()}"
    first = api.command("post", f"/api/v1/einvoice/documents/{doc.id}:retry", {"reason": "PT restored"}, key=key)
    assert first.status_code == 200, first.json()
    replay = api.command("post", f"/api/v1/einvoice/documents/{doc.id}:retry", {"reason": "PT restored"}, key=key)
    assert replay["Idempotent-Replayed"] == "true" and replay.json() == first.json()
    assert worker.process_due(ctx)["accepted"] == 1
    body = api.get(f"/api/v1/einvoice/documents/{doc.id}").json()
    assert body["status"] == "accepted" and body["cufe"] == doc.unique_code and body["synthetic"] is True
    events = api.get(f"/api/v1/einvoice/documents/{doc.id}/events").json()["items"]
    kinds = [e["kind"] for e in events]
    assert kinds.count("transmission") == 2 and "delivery" in kinds and events[-1]["label"].startswith("SIMULADO")
    xml = api.get(f"/api/v1/einvoice/documents/{doc.id}/xml")
    assert xml.status_code == 200 and xml["Content-Type"] == "application/xml" and b"ds:Signature" in xml.content
    resend = api.command("post", f"/api/v1/einvoice/documents/{doc.id}:resend-email", {})
    assert resend.status_code == 200
    with tenancy.scope(ctx):
        assert FiscalDocument.objects.get(pk=doc.id).deliveries.filter(status="QUEUED").count() == 2


def test_T_P15_api_numbering_ranges_and_exhaustion(api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID) -> None:
    grant_all(policy, ctx)
    created = api.command("post", "/api/v1/einvoice/numbering-ranges", RANGE)
    assert created.status_code == 201, created.json()
    rid = created.json()["id"]
    assert "technical_key_ref" not in created.json()
    with pytest.raises(Exception, match="numbering range"):
        register(ctx, company, invoice_model("SETP990000099"))  # outside the authorized range
    doc = register(ctx, company, invoice_model("SETP990000009"))
    assert doc.number == "SETP990000009"
    etag = api.get(f"/api/v1/einvoice/numbering-ranges/{rid}")["ETag"]
    blocked = api.command("patch", f"/api/v1/einvoice/numbering-ranges/{rid}", {"prefix": "OTRO"}, **{"If-Match": etag})
    assert blocked.status_code == 409
    assert api.command("delete", f"/api/v1/einvoice/numbering-ranges/{rid}").status_code == 409  # in use
    body = api.get(f"/api/v1/kpis/facturacion?period=2026-11&company_id={company}").json()
    kpi = {k["id"]: k for k in body["kpis"]}
    assert set(body) >= {"as_of", "kpis", "generated_at"}
    assert kpi["numeracion_restante"]["value"] == "1" and kpi["numeracion_restante"]["state"] == "ok"
    assert kpi["docs_emitidos"]["value"] == "1" and kpi["docs_emitidos"]["delta"] is None  # previous = 0 → '—'
    assert kpi["docs_validados_pct"]["value"] is None and kpi["docs_validados_pct"]["state"] == "no_data"
    assert kpi["docs_validados_pct"]["empty_reason"] == "Sin base para calcular"
    chart = api.get("/api/v1/kpis/facturacion/panels/estado_documentos?period=2026-11").json()
    assert chart["points"] == [
        {
            "label": "built",
            "values": {"documentos": "1"},
            "fiscal_status": "PENDING",
            "drill": "/m/facturacion/documentos?estado=built",
        }
    ]
    sync = api.command("post", "/api/v1/einvoice/numbering-ranges:sync", {})
    assert sync.status_code == 202 and sync.json()["status"] == "FAILED"  # the simulator has no ranges (manual load)


def test_T_P15_api_contingency_radian_and_habilitation(
    api: ApiClient, policy: Any, ctx: Any, company: uuid.UUID
) -> None:
    grant_all(policy, ctx)
    assert api.command("post", "/api/v1/einvoice/contingency:declare", {}).status_code == 422  # reason required
    on = api.command(
        "post", "/api/v1/einvoice/contingency:declare", {"reason": "corte", "parameters": {"kind": "ISSUER_OUTAGE"}}
    )
    assert on.status_code == 200 and on.json()["active"] is True
    panel = api.get("/api/v1/kpis/facturacion/panels/contingencia").json()
    assert panel["contingency"]["active"] is True
    off = api.command("post", "/api/v1/einvoice/contingency:end", {})
    assert off.json()["active"] is False
    assert api.get("/api/v1/kpis/facturacion/panels/contingencia").json()["contingency"]["deadline_at"] is not None

    create_range(ctx, company)
    doc = service.transmit(ctx, register(ctx, company, invoice_model("SETP990000003")).id)
    ref = {"type": "einvoice.document", "id": str(doc.id)}
    received = api.command("post", "/api/v1/einvoice/radian/events", {"document": ref, "event_code": "032"})
    assert received.status_code == 201 and received.json()["status"] == "RECORDED"
    emitted = api.command(
        "post", "/api/v1/einvoice/radian/events:emit", {"parameters": {"document": ref, "event_code": "033"}}
    )
    assert emitted.status_code == 200, emitted.json()
    assert emitted.json()["data"]["code_status"] == "OFFICIAL_FILE_PRIOR_VERSION"
    worker.process_due(ctx)
    codes = {e["event_code"]: e["status"] for e in api.get("/api/v1/einvoice/radian/events").json()["items"]}
    assert codes == {"032": "RECORDED", "033": "ACCEPTED"}

    ts = api.command("post", "/api/v1/einvoice/habilitation/test-sets", {"test_set_id": "SINTETICO-TESTSET-1"})
    assert ts.status_code == 201
    run = api.command("post", f"/api/v1/einvoice/habilitation/test-sets/{ts.json()['id']}:run", {})
    assert run.status_code == 202 and run.json()["status"] == "SUCCEEDED", run.json()
    result = api.get(f"/api/v1/einvoice/habilitation/test-sets/{ts.json()['id']}").json()
    assert result["status"] == "PASSED" and result["results"]["total"] == 4
    assert {d["family"] for d in result["results"]["documents"]} == {"FE", "NC", "ND"}


def test_T_P15_api_settings_refuse_direct_and_unknown_fields(api: ApiClient, policy: Any, ctx: Any) -> None:
    grant_all(policy, ctx)
    direct = api.command("put", "/api/v1/einvoice/settings", {"mode": "PROVIDER_SANDBOX", "adapter": "direct"})
    assert direct.status_code == 422 and direct.json()["code"] == "provider_not_configured"
    extra = api.command(
        "put", "/api/v1/einvoice/settings", {"mode": "SANDBOX_SIMULATOR", "adapter": "simulator", "x": 1}
    )
    assert extra.status_code == 422
    ok = api.command("put", "/api/v1/einvoice/settings", {"mode": "SANDBOX_SIMULATOR", "adapter": "simulator"})
    assert ok.status_code == 200 and ok.json()["adapter"] == "simulator"
    assert set(ok.json()) == {
        "id",
        "tenant_id",
        "company_id",
        "mode",
        "adapter",
        "provider_id",
        "software_id",
        "version",
        "audit",
    }
