"""Parameter store workflow and the tax API (T-P06-09, T-P06-10; security, idempotency, leaks)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from kernel.testing.tenancy import make_context
from kernel.uow import run_in_unit_of_work
from tax.models import Parameter, ParameterApproval
from tax.parameters import workflow
from tax.testing.fixtures import ApiClient, grant_all

pytestmark = pytest.mark.django_db

SOURCE = {"url": "https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0238_2025.htm",
          "title": "Res. UVT (synthetic test proposal)", "accessed_at": "2026-11-09T10:00:00-05:00"}  # fmt: skip


def _proposal_body(**over: Any) -> dict[str, Any]:
    params = {"code": "uvt", "value_type": "decimal", "value": "60000", "valid_from": "2027-01-01",
              "valid_to": "2027-12-31", "parameter_status": "VERIFIED-PRIMARY", "source": SOURCE,
              "document_ref": "filestore:synthetic-test-doc", "synthetic": True}  # fmt: skip
    params.update(over)
    return {"reason": "load 2027 value (synthetic test)", "parameters": params}


def _second_person(ctx: Any) -> Any:
    return make_context(tenant_id=ctx.tenant_id, company_ids=ctx.company_ids)


# ----------------------------------------------------------------------------- T-P06-09


def test_T_P06_09_parameter_approval_requires_two_people(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    resp = api.command("post", "/api/v1/tax/parameters:propose", _proposal_body())
    assert resp.status_code == 200, resp.json()
    proposal = resp.json()
    assert proposal["approval"] == "PROPOSED" and proposal["synthetic"] is True

    # a PROPOSED value is never used: 2027 still fails closed
    from kernel import tenancy
    from tax.parameters.store import DbParameterProvider

    with tenancy.activate(seeded), pytest.raises(Exception) as exc:
        DbParameterProvider().get("uvt", dt.date(2027, 3, 1), next(iter(seeded.company_ids)))
    assert exc.value.code == "parameter_unknown"

    # the proposer cannot approve
    same = api.command("post", f"/api/v1/tax/parameters/{proposal['id']}:approve", {"reason": "self"})
    assert same.status_code == 403 and same.json()["code"] == "sod_violation"

    # a second person with tax.parameter.approve can
    approver = _second_person(seeded)
    grant_all(policy, approver, ("tax.parameter.approve", "tax.parameter.read"))
    api.use(approver)
    ok = api.command("post", f"/api/v1/tax/parameters/{proposal['id']}:approve",
                     {"reason": "checked against the official text", "parameters": {"legal_review": True}})  # fmt: skip
    assert ok.status_code == 200, ok.json()
    body = ok.json()
    assert body["approval"] == "APPROVED" and body["legal_reviewed_by"] == str(approver.principal_id)
    assert [a.action for a in ParameterApproval.objects.filter(parameter_id=proposal["id"]).order_by("at")] == [
        "PROPOSE",
        "APPROVE",
    ]
    # approved twice → invalid transition
    again = api.command("post", f"/api/v1/tax/parameters/{proposal['id']}:approve", {})
    assert again.status_code == 409 and again.json()["code"] == "invalid_state_transition"
    # the approval event is in the outbox of the same transaction
    from kernel.models import OutboxMessage

    event = OutboxMessage.objects.get(event_type="tax.parameter.approved.v1", tenant_id=seeded.tenant_id)
    assert event.envelope["payload"] == {"parameter_id": proposal["id"], "code": "uvt", "valid_from": "2027-01-01",
                                         "status": "VERIFIED-PRIMARY"}  # fmt: skip
    with tenancy.activate(seeded):
        assert DbParameterProvider().get("uvt", dt.date(2027, 3, 1), next(iter(seeded.company_ids))).synthetic


def test_T_P06_09_approval_log_is_append_only(seeded: Any) -> None:
    from django.db import DatabaseError, transaction

    row = ParameterApproval.objects.filter(tenant_id=seeded.tenant_id).first()
    with pytest.raises(DatabaseError), transaction.atomic():
        ParameterApproval.objects.filter(pk=row.pk).update(reason="tampered")


def test_T_P06_09_change_closes_previous_range(seeded: Any) -> None:
    current = Parameter.objects.get(tenant_id=seeded.tenant_id, code="iva.general")
    assert current.valid_to is None
    data = workflow.ProposalInput(code="iva.general", value_type="percent", value="20", valid_from=dt.date(2026, 12, 1),
                                  parameter_status="VERIFIED-SECONDARY", source_id=current.source_id, synthetic=True)  # fmt: skip
    proposal = run_in_unit_of_work(lambda u: workflow.propose_change(u, current.id, data, reason="synthetic"), seeded)
    with pytest.raises(Exception) as exc:
        run_in_unit_of_work(lambda u: workflow.approve(u, proposal.id), seeded)
    assert exc.value.code == "sod_violation"
    run_in_unit_of_work(lambda u: workflow.approve(u, proposal.id), _second_person(seeded))
    current.refresh_from_db()
    assert current.valid_to == dt.date(2026, 11, 30)
    assert current.legal_history[-1]["event"] == "closed"


def test_T_P06_09_official_value_needs_source_and_document(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    body = _proposal_body(synthetic=False, document_ref="")
    resp = api.command("post", "/api/v1/tax/parameters:propose", body)
    assert resp.status_code == 422 and resp.json()["code"] == "parameter_source_required"
    no_source = _proposal_body()
    del no_source["parameters"]["source"]
    resp = api.command("post", "/api/v1/tax/parameters:propose", no_source)
    assert resp.status_code == 422 and resp.json()["code"] == "parameter_source_required"
    floaty = _proposal_body(value=60000.5)
    resp = api.command("post", "/api/v1/tax/parameters:propose", floaty)
    assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"


# ----------------------------------------------------------------------------- T-P06-10


def test_T_P06_10_impact_preview_lists_affected_documents(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    calls: list[tuple[str, dt.date]] = []

    def payroll_runs(ctx: Any, code: str, valid_from: dt.date, valid_to: Any, company: Any) -> list[dict[str, Any]]:
        calls.append((code, valid_from))
        if code != "uvt":
            return []
        return [{"document_type": "payroll.run", "document_id": "run-2027-01", "number": "NOM-2027-01",
                 "reason": "open run uses UVT"}]  # fmt: skip

    def open_invoices(ctx: Any, code: str, valid_from: dt.date, valid_to: Any, company: Any) -> list[dict[str, Any]]:
        return [{"document_type": "purchase.invoice", "document_id": "fc-9", "number": "FC-9",
                 "reason": "draft withholding"}] if code == "uvt" else []  # fmt: skip

    workflow.register_impact_provider("payroll", payroll_runs)
    workflow.register_impact_provider("purchasing", open_invoices)
    try:
        proposal = api.command("post", "/api/v1/tax/parameters:propose", _proposal_body()).json()
        resp = api.get(f"/api/v1/tax/parameters/{proposal['id']}/impact")
        assert resp.status_code == 200
        impact = resp.json()
        assert impact["count"] == 2 and impact["providers"] == ["payroll", "purchasing"]
        assert {(d["provider"], d["number"]) for d in impact["affected"]} == {("payroll", "NOM-2027-01"),
                                                                             ("purchasing", "FC-9")}  # fmt: skip
        assert calls == [("uvt", dt.date(2027, 1, 1))]
        iva = Parameter.objects.get(tenant_id=seeded.tenant_id, code="iva.general")
        assert api.get(f"/api/v1/tax/parameters/{iva.id}/impact").json()["count"] == 0
    finally:
        workflow.unregister_impact_provider("payroll")
        workflow.unregister_impact_provider("purchasing")


# ----------------------------------------------------------------------------- security, tenancy, idempotency


def test_P06_api_denies_by_default(seeded: Any, api: ApiClient, policy: Any) -> None:
    for url in ("/api/v1/tax/parameters", "/api/v1/tax/tax-codes", "/api/v1/tax/localization/municipalities",
                "/api/v1/tax/exogena/format-packs", "/api/v1/tax/sources"):  # fmt: skip
        resp = api.get(url)
        assert resp.status_code == 403, url
    resp = api.command("post", "/api/v1/tax/compute", {"parameters": {"document_date": "2026-07-01", "lines": []}})
    assert resp.status_code == 403


def test_P06_api_unauthenticated_and_no_context(seeded: Any, api: ApiClient, policy: Any) -> None:
    from rest_framework.test import APIClient

    anonymous = APIClient()
    assert anonymous.get("/api/v1/tax/parameters").status_code in (401, 403)
    api.client.credentials()  # authenticated user but no tenant context
    assert api.get("/api/v1/tax/parameters").status_code == 401


def test_P06_api_tenant_isolation(seeded: Any, api: ApiClient, policy: Any, company: uuid.UUID) -> None:
    grant_all(policy, seeded)
    mine = api.get("/api/v1/tax/parameters?code=uvt").json()["items"]
    assert len(mine) == 1
    other = make_context()
    grant_all(policy, other)
    api.use(other)
    assert api.get("/api/v1/tax/parameters").json()["items"] == []
    assert api.get(f"/api/v1/tax/parameters/{mine[0]['id']}").status_code == 404
    assert api.get("/api/v1/tax/tax-codes").json()["items"] == []
    # foreign company header is refused
    resp = api.get("/api/v1/tax/tax-codes", HTTP_X_COMPANY_ID=str(company))
    assert resp.status_code in (403, 404, 422)


def test_P06_api_timeline_paging_and_filters(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    timeline = api.get("/api/v1/tax/parameters?code=retefuente.servicios.base_uvt").json()["items"]
    assert [(p["valid_from"], p["parameter_status"]) for p in sorted(timeline, key=lambda p: p["valid_from"])] == [
        ("2026-01-01", "DISPUTED"),
        ("2026-05-07", "UNKNOWN"),
        ("2026-07-01", "DISPUTED"),
    ]
    page1 = api.get("/api/v1/tax/parameters?limit=5").json()
    page2 = api.get(f"/api/v1/tax/parameters?limit=5&cursor={page1['next_cursor']}").json()
    assert len(page1["items"]) == 5 and not {p["id"] for p in page1["items"]} & {p["id"] for p in page2["items"]}
    unknown = api.get("/api/v1/tax/parameters?parameter_status=UNKNOWN").json()["items"]
    assert unknown and all(p["value"] == {"type": "unknown"} for p in unknown)
    assert api.get("/api/v1/tax/parameters?limit=0").json()["code"] == "filter_invalid"


def test_P06_api_compute_and_idempotent_replay(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    body = {"parameters": {"document_type": "purchase.invoice", "document_date": "2026-07-15",
                           "seller": {"regime": "RESPONSABLE_IVA"},
                           "buyer": {"regime": "RESPONSABLE_IVA", "vat_withholding_agent": True},
                           "lines": [{"line_ref": "1", "base_amount": "1000000", "tax_codes": ["IVA19", "RETEIVA"]}]}}  # fmt: skip
    first = api.command("post", "/api/v1/tax/compute", body, key="compute-key-0001")
    assert first.status_code == 200, first.json()
    data = first.json()["data"]
    assert data["totals"] == {"IVA19": "190000.00", "RETEIVA": "28500.00"}
    assert data["tax_total"] == "190000.00" and data["withholding_total"] == "28500.00"
    replay = api.command("post", "/api/v1/tax/compute", body, key="compute-key-0001")
    assert replay.json() == first.json() and replay["Idempotent-Replayed"] == "true"
    floaty = {"parameters": {**body["parameters"], "lines": [{"base_amount": 10.5, "tax_codes": ["IVA19"]}]}}
    assert api.command("post", "/api/v1/tax/compute", floaty).status_code == 422
    # statutory VAT rates are open-ended; the UVT-based withholding base has no 2027 row → fails closed
    unknown = {"parameters": {**body["parameters"], "document_date": "2027-02-01",
                              "buyer": {"regime": "RESPONSABLE_IVA", "withholding_agent": True},
                              "lines": [{"base_amount": "500000", "concept": "RF-SERVICIOS"}]}}  # fmt: skip
    resp = api.command("post", "/api/v1/tax/compute", unknown)
    assert resp.status_code == 422 and resp.json()["code"] == "tax_parameter_unknown"


def test_P06_api_master_data_crud_with_etag(seeded: Any, api: ApiClient, policy: Any) -> None:
    grant_all(policy, seeded)
    created = api.command("post", "/api/v1/tax/tax-codes", {"code": "INC8", "name": "Impuesto al consumo",
                                                              "tax_type": "INC", "rate_parameter_code": "inc.general"})  # fmt: skip
    assert created.status_code == 201, created.json()
    tid, etag = created.json()["id"], created["ETag"]
    assert (
        api.command("post", "/api/v1/tax/tax-codes", {"code": "INC8", "name": "dup", "tax_type": "INC"}).status_code
        == 409
    )
    no_match = api.command("patch", f"/api/v1/tax/tax-codes/{tid}", {"name": "x"})
    assert no_match.status_code == 428
    stale = api.command("patch", f"/api/v1/tax/tax-codes/{tid}", {"name": "x"}, **{"If-Match": 'W/"stale"'})
    assert stale.status_code == 412
    ok = api.command("patch", f"/api/v1/tax/tax-codes/{tid}", {"name": "INC 8 %"}, **{"If-Match": etag})
    assert ok.status_code == 200 and ok.json()["name"] == "INC 8 %" and ok.json()["version"] == 2
    assert api.command("delete", f"/api/v1/tax/tax-codes/{tid}").status_code == 204
    assert api.get(f"/api/v1/tax/tax-codes/{tid}").json()["status"] == "ARCHIVED"  # archived, never deleted
    bad = api.command("post", "/api/v1/tax/tax-codes", {"code": "X", "name": "y", "tax_type": "VAT", "z": 1})
    assert bad.status_code == 422
    profile = api.command("post", "/api/v1/tax/party-tax-profiles",
                          {"party": {"id": str(uuid.uuid4()), "kind": "vendor"}, "regime": "RESPONSABLE_IVA",
                           "withholding_agent": True, "responsibilities": ["O-13"]})  # fmt: skip
    assert profile.status_code == 201 and profile.json()["party"]["kind"] == "vendor"
    ica = api.command("post", "/api/v1/tax/ica-tariffs", {"code": "ICA-X", "name": "t", "municipality_code": "999",
                                                           "activity_code": "4711"})  # fmt: skip
    assert ica.status_code == 422
    cal = api.command("post", "/api/v1/tax/calendars", {"code": "RF", "name": "Retención", "obligation": "retefuente",
                                                         "year": 2026, "due_dates": {"periods": [
                                                             {"period": "2026-01", "by_digit": {"1": "2026-02-10"}}]}})  # fmt: skip
    assert cal.status_code == 201, cal.json()


def test_P06_api_sources_nit_and_localization(seeded: Any, api: ApiClient, policy: Any) -> None:
    from localization_co.loaders import load_defaults

    load_defaults()
    grant_all(policy, seeded)
    src = api.command("post", "/api/v1/tax/sources", SOURCE)
    assert src.status_code == 201 and api.get(f"/api/v1/tax/sources/{src.json()['id']}").status_code == 200
    ok = api.command("post", "/api/v1/tax/localization/nit:validate", {"parameters": {"nit": "800.197.268-4"}})
    assert ok.json()["status"] == "VALID" and ok.json()["data"]["formatted"] == "800.197.268-4"
    bad = api.command("post", "/api/v1/tax/localization/nit:validate", {"parameters": {"nit": "800197268", "dv": "5"}})
    assert bad.json()["status"] == "INVALID" and bad.json()["data"]["check_digit"] == 4
    muns = api.get("/api/v1/tax/localization/municipalities?q=Bogo").json()["items"]
    assert [(m["code"], m["department"]) for m in muns] == [("11001", "Bogotá, D.C.")]
    assert api.get(f"/api/v1/tax/localization/municipalities/{muns[0]['id']}").json()["name"] == "Bogotá, D.C."
    hols = api.get("/api/v1/tax/localization/holidays?year=2026").json()["items"]
    assert len(hols) == 18 and hols[0]["date"] == "2026-01-01"
    assert api.get(f"/api/v1/tax/localization/holidays/{hols[3]['id']}?year=2026").json()["name"] == hols[3]["name"]
