"""Payroll API end to end (documented operations only): period → run → calculate → approve → post →
payslips → nómina electrónica data (simulated, never transmitted) → adjustment; idempotent replays."""

from __future__ import annotations

from typing import Any

import pytest

from payroll.testing.fixtures import standard_user_concepts
from tax.testing.fixtures import ApiClient

pytestmark = pytest.mark.django_db


def test_payroll_api_run_lifecycle_and_electronic_payroll(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000", full_name="Ana Sintética")
    hdr = {"X-Company-Id": str(w.company_id)}
    author, approver = ApiClient(w.author), ApiClient(w.approver)

    period = author.command(
        "post",
        "/api/v1/payroll/periods",
        {"code": "2026-01", "start_date": "2026-01-01", "end_date": "2026-01-31"},
        **hdr,
    )
    assert period.status_code == 201, period.json()
    body = {"period_id": period.json()["id"]}
    run = author.command("post", "/api/v1/payroll/runs", body, key="run-create-0001", **hdr)
    replay = author.command("post", "/api/v1/payroll/runs", body, key="run-create-0001", **hdr)
    assert run.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == run.json()["id"]
    run_id = run.json()["id"]

    sim = author.post(
        f"/api/v1/payroll/runs/{run_id}:simulate", {}, format="json", HTTP_X_COMPANY_ID=hdr["X-Company-Id"]
    )
    assert sim.status_code == 200 and sim.json()["persisted"] is False
    calc = author.command("post", f"/api/v1/payroll/runs/{run_id}:calculate", {}, **hdr)
    assert calc.status_code == 200, calc.json()
    assert (
        calc.json()["data"]["status"] == "CALCULATED" and calc.json()["data"]["legal_notice"] == "NOT LEGALLY VERIFIED"
    )
    assert author.command("post", f"/api/v1/payroll/runs/{run_id}:approve", {}, **hdr).status_code == 403  # SoD
    assert approver.command("post", f"/api/v1/payroll/runs/{run_id}:approve", {}, **hdr).status_code == 200
    posted = approver.command("post", f"/api/v1/payroll/runs/{run_id}:post", {}, **hdr)
    assert posted.status_code == 200 and posted.json()["data"]["status"] == "POSTED"
    again = author.command("post", f"/api/v1/payroll/runs/{run_id}:calculate", {}, **hdr)
    assert again.status_code == 409 and again.json()["code"] == "payroll_run_locked"

    slips = author.get(f"/api/v1/payroll/payslips?run_id={run_id}", HTTP_X_COMPANY_ID=hdr["X-Company-Id"]).json()
    assert slips["items"][0]["status"] == "POSTED" and slips["items"][0]["net_pay"]["amount"] == "2049095.0000"

    doc = author.command("post", "/api/v1/payroll/electronic-payroll", {"period_id": period.json()["id"]}, **hdr)
    assert doc.status_code == 201, doc.json()
    gen = author.command("post", f"/api/v1/payroll/electronic-payroll/{doc.json()['id']}:generate", {}, **hdr)
    assert gen.status_code == 200, gen.json()
    data = gen.json()["data"]
    assert data["transmission_status"] == "SIMULATED_NOT_TRANSMITTED" and data["cune"] is None
    assert data["documents"] == 1 and "FIXTURE_NOT_CERTIFIED" in data["label"]
    adj = author.command(
        "post",
        f"/api/v1/payroll/electronic-payroll/{doc.json()['id']}:adjust",
        {"parameters": {"kind": "Reemplazar"}},
        **hdr,
    )
    assert adj.status_code == 200 and adj.json()["data"]["document_type"] == "NominaIndividualDeAjuste"


def test_payroll_api_denies_without_permission(payroll_world: Any) -> None:
    from kernel.testing.tenancy import make_context

    w = payroll_world
    stranger = ApiClient(make_context(tenant_id=w.author.tenant_id, company_ids=w.author.company_ids))
    hdr = {"HTTP_X_COMPANY_ID": str(w.company_id)}
    for url in (
        "/api/v1/payroll/concepts",
        "/api/v1/payroll/runs",
        "/api/v1/payroll/periods",
        "/api/v1/payroll/novelties",
        "/api/v1/payroll/electronic-payroll",
    ):
        assert stranger.get(url, **hdr).status_code == 403, url
    resp = stranger.command(
        "post",
        "/api/v1/payroll/concepts",
        {"code": "X1", "name": "x", "concept_type": "DEVENGO"},
        **{"X-Company-Id": str(w.company_id)},
    )
    assert resp.status_code == 403
