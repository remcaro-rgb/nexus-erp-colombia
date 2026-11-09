"""API: authentication required, deny by default, idempotency, problem+json, formulas validate/simulate, KPIs."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from kernel.tenancy import TenantContext

pytestmark = [pytest.mark.django_db, pytest.mark.urls("workflow.tests.urls")]
CURRENT: dict[str, TenantContext | None] = {"ctx": None}


def resolve(request):  # type: ignore[no-untyped-def]
    return CURRENT["ctx"]


@pytest.fixture
def client(settings):  # type: ignore[no-untyped-def]
    settings.ERP_TENANT_CONTEXT_RESOLVER = "workflow.tests.test_api.resolve"
    CURRENT["ctx"] = None
    yield APIClient()
    CURRENT["ctx"] = None


def key() -> str:
    return str(uuid.uuid4())


FORMULA = {
    "code": "comision",
    "domain": "pricing",
    "expression": "round(venta * pct / 100, 2)",
    "inputs": {"venta": "money", "pct": "percent"},
    "result_type": "money",
    "tests": {"cases": [{"inputs": {"venta": "1000", "pct": "5"}, "expected": "50"}]},
}


def test_unauthenticated_is_401_problem_json(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/workflow/formulas")
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_deny_by_default(client, user_factory) -> None:  # type: ignore[no-untyped-def]
    CURRENT["ctx"] = user_factory()
    assert client.get("/api/v1/workflow/audit-events").status_code == 403
    r = client.post("/api/v1/workflow/formulas", FORMULA, format="json", HTTP_IDEMPOTENCY_KEY=key())
    assert r.status_code == 403
    assert client.get("/api/v1/workflow/formulas").json()["items"] == []  # row scope: nothing readable


def test_formula_validate_and_simulate(client, user_factory) -> None:  # type: ignore[no-untyped-def]
    CURRENT["ctx"] = user_factory("workflow.formula.read")
    r = client.post("/api/v1/workflow/formulas:validate", {"parameters": FORMULA}, format="json",
                    HTTP_IDEMPOTENCY_KEY=key())  # fmt: skip
    assert r.status_code == 200 and r.json()["status"] == "VALID"
    bad = {**FORMULA, "expression": "venta.__class__"}
    r = client.post(
        "/api/v1/workflow/formulas:validate", {"parameters": bad}, format="json", HTTP_IDEMPOTENCY_KEY=key()
    )
    assert r.json()["status"] == "INVALID"
    err = r.json()["data"]["errors"][0]
    assert err["code"] == "formula_syntax_error" and err["position"] == 5
    sim = {**FORMULA, "values": {"venta": "2000", "pct": "7.5"}}
    r = client.post(
        "/api/v1/workflow/formulas:simulate", {"parameters": sim}, format="json", HTTP_IDEMPOTENCY_KEY=key()
    )
    assert r.status_code == 200 and r.json()["data"]["result"] == "150.0000"
    assert any(t["expr"] == "venta * pct" for t in r.json()["data"]["trace"])
    r = client.post("/api/v1/workflow/formulas:simulate", {"parameters": {**sim, "expression": "venta / 0"}},
                    format="json", HTTP_IDEMPOTENCY_KEY=key())  # fmt: skip
    assert r.status_code == 422 and r.json()["code"] == "formula_division_by_zero"
    r = client.post("/api/v1/workflow/formulas:validate", {"parameters": FORMULA}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"


def test_formula_crud_and_lifecycle_over_api(client, user_factory) -> None:  # type: ignore[no-untyped-def]
    author = user_factory("workflow.formula.create", "workflow.formula.update", "workflow.formula.read")
    approver = user_factory("workflow.formula.approve", "workflow.formula.read")
    CURRENT["ctx"] = author
    k = key()
    r = client.post("/api/v1/workflow/formulas", FORMULA, format="json", HTTP_IDEMPOTENCY_KEY=k)
    assert r.status_code == 201 and r.json()["formula_status"] == "DRAFT" and r["ETag"]
    fid = r.json()["id"]
    again = client.post("/api/v1/workflow/formulas", FORMULA, format="json", HTTP_IDEMPOTENCY_KEY=k)
    assert again.status_code == 201 and again.json()["id"] == fid  # replayed, not duplicated
    r = client.patch(f"/api/v1/workflow/formulas/{fid}", {"description": "Comisión"}, format="json",
                     HTTP_IDEMPOTENCY_KEY=key())  # fmt: skip
    assert r.status_code == 428
    etag = client.get(f"/api/v1/workflow/formulas/{fid}")["ETag"]
    r = client.patch(f"/api/v1/workflow/formulas/{fid}", {"description": "Comisión"}, format="json",
                     HTTP_IDEMPOTENCY_KEY=key(), HTTP_IF_MATCH=etag)  # fmt: skip
    assert r.status_code == 200 and r.json()["description"] == "Comisión"
    r = client.post(f"/api/v1/workflow/formulas/{fid}:submit", {}, format="json", HTTP_IDEMPOTENCY_KEY=key())
    assert r.json()["status"] == "SUBMITTED"
    r = client.post(f"/api/v1/workflow/formulas/{fid}:approve", {}, format="json", HTTP_IDEMPOTENCY_KEY=key())
    assert r.status_code == 403
    CURRENT["ctx"] = approver
    r = client.post(f"/api/v1/workflow/formulas/{fid}:approve", {}, format="json", HTTP_IDEMPOTENCY_KEY=key())
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert len(client.get(f"/api/v1/workflow/formulas/{fid}/versions").json()["items"]) == 1


def test_audit_api_masks_fields_and_verifies(client, user_factory, grants) -> None:  # type: ignore[no-untyped-def]
    from workflow.formula.lifecycle import FormulaService

    author = user_factory("workflow.formula.create", "workflow.formula.read")
    FormulaService().create(author, next(iter(author.company_ids)), FORMULA)
    reader = user_factory("workflow.audit_event.read")
    CURRENT["ctx"] = reader
    items = client.get("/api/v1/workflow/audit-events?resource_type=workflow.formula").json()["items"]
    assert items and items[0]["changes"]["expression"] == {"changed": True} and items[0]["ip"] is None
    grants.readable[(reader.principal_id, "workflow.formula")] = frozenset({"expression"})
    items = client.get("/api/v1/workflow/audit-events?resource_type=workflow.formula").json()["items"]
    assert items[0]["changes"]["expression"]["after"] == FORMULA["expression"]
    r = client.post("/api/v1/workflow/audit:verify", {}, format="json", HTTP_IDEMPOTENCY_KEY=key())
    assert r.json()["status"] == "VERIFIED"


def test_kpis_and_notifications(client, user_factory, company) -> None:  # type: ignore[no-untyped-def]
    CURRENT["ctx"] = user_factory("workflow.approval.read", "workflow.notification.read")
    body = client.get("/api/v1/kpis/aprobaciones").json()
    assert body["kpis"]["pendientes"]["value"] == 0
    assert body["kpis"]["tiempo_aprobacion"]["value"] is None
    assert body["kpis"]["tiempo_aprobacion"]["empty_reason"] == "Sin base para calcular"
    assert "eventos_auditoria" not in body["kpis"]  # no audit permission → KPI absent
    n = client.get("/api/v1/workflow/notifications").json()
    assert n["items"] == [] and n["unread_count"] == 0
    r = client.get("/api/v1/workflow/notifications", HTTP_X_COMPANY_ID=str(uuid.uuid4()))
    assert r.status_code in (403, 422)


def test_scheduled_jobs_api(client, user_factory) -> None:  # type: ignore[no-untyped-def]
    from workflow.scheduler.registry import JOB_TYPES, register_job_type

    register_job_type("api_test", "reporting.report.read", "x", lambda *a: {}, replace=True)
    try:
        CURRENT["ctx"] = user_factory("workflow.scheduled_job.create", "workflow.scheduled_job.read",
                                      "workflow.scheduled_job.update")  # fmt: skip
        r = client.post("/api/v1/workflow/scheduled-jobs", {"code": "R1", "job_type": "api_test", "cron": "0 7 * * 1"},
                        format="json", HTTP_IDEMPOTENCY_KEY=key())  # fmt: skip
        assert r.status_code == 201 and r.json()["timezone"] == "America/Bogota"
        jid = r.json()["id"]
        r = client.post(f"/api/v1/workflow/scheduled-jobs/{jid}:pause", {}, format="json", HTTP_IDEMPOTENCY_KEY=key())
        assert r.json()["status"] == "PAUSED"
        assert len(client.get("/api/v1/workflow/scheduled-jobs").json()["items"]) == 1
    finally:
        JOB_TYPES.pop("api_test", None)


def test_webhook_secret_shown_once_never_replayed(client, user_factory) -> None:  # type: ignore[no-untyped-def]
    CURRENT["ctx"] = user_factory("workflow.webhook.create", "workflow.webhook.read")
    body = {"url": "http://webhook-sink:9000/h", "event_types": ["ledger.*"], "service_account_id": str(uuid.uuid4())}
    k = key()
    first = client.post("/api/v1/workflow/webhooks", body, format="json", HTTP_IDEMPOTENCY_KEY=k)
    assert first.status_code == 201 and len(first.json()["signing_secret_once"]) == 64
    replay = client.post("/api/v1/workflow/webhooks", body, format="json", HTTP_IDEMPOTENCY_KEY=k)
    assert replay.json()["id"] == first.json()["id"] and "signing_secret_once" not in replay.json()
    listed = client.get("/api/v1/workflow/webhooks").json()["items"]
    assert listed and "secret" not in str(listed)
    bad = client.post("/api/v1/workflow/webhooks", {**body, "url": "https://api.realbank.com.co/h"}, format="json",
                      HTTP_IDEMPOTENCY_KEY=key())  # fmt: skip
    assert bad.status_code == 422 and bad.json()["code"] == "production_endpoint_refused"
