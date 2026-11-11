"""Semantic layer API and engine guarantees (P17 T-P17-01/02/03/08/09/10) over the real ledger (synthetic data)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from kernel import ids, services
from kernel.errors import PermissionDenied
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.reporting import ReportQuery, ReportQueryService
from kernel.interfaces.scheduler import SchedulerService
from kernel.models import OutboxMessage
from kernel.testing.fakes import FakeSchedulerService
from kernel.uow import run_in_unit_of_work
from reporting import engine, reports
from reporting.models import ReportRun, SavedReport
from reporting.testing.support import TODAY, ApiClient, World

D = Decimal
pytestmark = pytest.mark.django_db
SALES = {"field": "account_code", "op": "eq", "value": "413505"}


def sales(world: World) -> None:
    for n, amount in enumerate(("100000", "200000", "50000")):
        world.post("sales.invoice", f"FV-{n}", amount)
    world.post("sales.invoice", "FV-OCT", "70000", day=dt.date(2026, 10, 20))


def query(api: ApiClient, params: dict[str, Any], **kw: Any) -> Any:
    return api.post("/api/v1/reporting/query", {"parameters": params}, **kw)


def test_semantic_model_is_filtered_by_permission_and_exports(world: World, api: ApiClient) -> None:
    r = api.get("/api/v1/reporting/semantic-model")
    assert r.status_code == 200, r.content
    entities = {e["entity"]: e for e in r.json()["entities"]}
    assert set(entities) == {"ledger.journal_line", "treasury.voucher", "payroll.payslip"}
    fields = {f["key"] for f in entities["ledger.journal_line"]["fields"]}
    assert "party_id" not in fields and {"cost_center", "branch_id", "account_code"} <= fields  # masked = absent
    world.policy.revoke(world.ctx("analyst").principal_id, "treasury.receipt.read", "payroll.payslip.read")
    assert {e["entity"] for e in api.get("/api/v1/reporting/semantic-model").json()["entities"]} == {
        "ledger.journal_line"
    }
    csv = api.get("/api/v1/reporting/semantic-model", format="csv")
    assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv")
    assert api.as_("outsider").get("/api/v1/reporting/semantic-model").status_code == 403


def test_T_P17_01_query_api_respects_row_policy_and_company_scope(world: World, api: ApiClient) -> None:
    sales(world)
    norte, sur = world.branch("N1", head=True), world.branch("S1")
    world.post("sales.invoice", "FV-N", "1000", branch=norte)
    world.post("sales.invoice", "FV-S", "2000", branch=sur)
    body = {"entity": "ledger.journal_line", "measures": ["net_credit"], "filters": [SALES]}
    r = query(api, body)
    assert r.status_code == 200, r.content
    assert r.json()["totals"]["net_credit"] == "423000.0000"
    seller = world.user("seller", ("reporting.*", "ledger.entry.read"))
    world.policy.branches[seller.principal_id] = {sur.id}
    scoped = query(api.as_("seller"), body)
    assert scoped.json()["totals"]["net_credit"] == "2000.0000"  # only its own branch's postings
    other = world.user("other_co", ("reporting.*", "ledger.entry.read"), companies=[world.other_company])
    assert other is not None
    assert query(api.as_("other_co"), body).json()["totals"]["net_credit"] == "0"  # no cross-company rows
    # a company outside the context is refused, never silently widened
    foreign = query(api.as_("analyst"), {**body, "companies": [str(world.other_company)]})
    assert foreign.status_code == 422
    # the entity permission itself is required (deny by default)
    world.policy.revoke(world.ctx("analyst").principal_id, "ledger.entry.read")
    assert query(api.as_("analyst"), body).status_code == 403


def test_T_P17_02_masked_fields_rejected_in_select_filter_group_and_sort(world: World, api: ApiClient) -> None:
    party = ids.new_id()
    from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService

    req = PostingRequest(
        world.company,
        TODAY,
        DocumentRef("sales.invoice", ids.new_id(), "FV-P"),
        "test",
        (
            PostingLine("CASH", "DEBIT", D("10"), party=PartyRef(party, "customer")),
            PostingLine("SALES", "CREDIT", D("10"), party=PartyRef(party, "customer")),
        ),
    )
    world.run("admin", lambda u: services.get(PostingService).post(u, req))
    base = {"entity": "ledger.journal_line", "measures": ["net_credit"]}
    attempts = [
        {**base, "dimensions": ["party_id"]},
        {**base, "filters": [{"field": "party_id", "op": "eq", "value": str(party)}]},
        {**base, "filters": [{"field": "party_id", "op": "is_null"}]},
        {**base, "sort": [{"field": "party_id", "dir": "asc"}]},
    ]
    for attempt in attempts:
        r = query(api, attempt)
        assert r.status_code == 403 and r.json()["code"] == "field_forbidden", (attempt, r.content)
    # with the field grant the same query works
    world.policy.fields[world.ctx("analyst").principal_id] = {"party_id"}
    ok = query(api, {**base, "dimensions": ["party_id"], "filters": [{"field": "party_id", "op": "not_null"}]})
    assert ok.status_code == 200 and ok.json()["rows"] == [{"party_id": str(party), "net_credit": "0.0000"}]


def test_invalid_queries_answer_semantic_query_invalid(world: World, api: ApiClient) -> None:
    base = {"entity": "ledger.journal_line", "measures": ["net"]}
    for bad, reason in (
        ({"entity": "x.y", "measures": ["net"]}, "unknown_entity"),
        ({**base, "measures": ["nope"]}, "unknown_measure"),
        ({**base, "dimensions": ["nope"]}, "unknown_field"),
        ({**base, "filters": [{"field": "account_code", "op": "regex", "value": "4"}]}, "operator"),
        ({**base, "filters": [{"field": "posting_date", "op": "eq", "value": "ayer"}]}, "value"),
        ({**base, "filters": [{"field": "account_code", "op": "in_subtree", "value": ["x"]}]}, "operator"),
        ({**base, "limit": 10_001}, "range"),
        ({**base, "sql": "select 1"}, "invalid"),
    ):
        r = query(api, bad)
        assert r.status_code == 400, (bad, r.content)
        body = r.json()
        assert body["code"] == "semantic_query_invalid"
        assert reason in str(body), (bad, body)
    # values never reach SQL text: an injection attempt is just a value that matches nothing
    r = query(api, {**base, "filters": [{"field": "account_code", "op": "eq", "value": "1' OR '1'='1"}]})
    assert r.status_code == 200 and r.json()["totals"]["net"] == "0"


def test_T_P17_03_k_anonymity_suppresses_small_salary_groups(world: World) -> None:
    from payroll.models import PayrollPeriod, PayrollRun, Payslip

    big, small = world.node("cost_center", "NOM-A", "DETALLE"), world.node("cost_center", "NOM-B", "DETALLE")

    def seed(u: Any) -> None:
        period = PayrollPeriod.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.company,
            code="2026-10",
            start_date=dt.date(2026, 10, 1),
            end_date=dt.date(2026, 10, 31),
        )
        run = PayrollRun.objects.create(tenant_id=world.tenant_id, company_id=world.company, period=period)
        for n in range(9):
            Payslip.objects.create(
                tenant_id=world.tenant_id,
                company_id=world.company,
                run=run,
                employee_id=ids.new_id(),
                employee_name=f"Empleado {n} (SINTÉTICO)",
                cost_center_id=(big if n < 6 else small).id,
                earnings=D("2000000") + n,
                net_pay=D("1800000"),
            )

    world.run("admin", seed)
    result = engine.run(
        world.ctx("analyst"),
        [world.company],
        {"entity": "payroll.payslip", "measures": ["earnings", "payslips"], "dimensions": ["cost_center"]},
        engine.QueryOptions(use_cache=False),
    )
    rows = {r["cost_center"]: r for r in result["rows"]}
    assert rows[str(big.id)]["earnings"] == "12000015.0000" and not rows[str(big.id)].get("suppressed")
    assert rows[str(small.id)]["earnings"] is None and rows[str(small.id)]["suppressed"] is True
    assert result["totals"]["earnings"] == "12000015.0000"  # totals never leak the suppressed group
    assert result["warnings"][0]["code"] == "k_anonymity_suppressed"
    # employee identity fields are PII: masked without an explicit field grant
    with pytest.raises(engine.FieldForbidden):
        engine.run(
            world.ctx("analyst"),
            [world.company],
            {"entity": "payroll.payslip", "measures": ["earnings"], "dimensions": ["employee_name"]},
        )


def test_T_P17_08_query_cost_guard(world: World, api: ApiClient, monkeypatch: Any) -> None:
    sales(world)
    body = {"entity": "ledger.journal_line", "measures": ["net"], "dimensions": ["account_code"]}
    explained = api.post("/api/v1/reporting/query:explain", {"parameters": body})
    assert explained.status_code == 200, explained.content
    data = explained.json()["data"]
    assert data["estimate"]["total_cost"] > 0 and data["interactive"] is True
    assert "%s" in data["sql"] and "413505" not in data["sql"]  # parameterized, values not in the SQL text
    monkeypatch.setattr(engine, "MAX_INTERACTIVE_COST", 0)
    heavy = query(api, body)
    assert heavy.status_code == 400 and heavy.json()["code"] == "semantic_query_invalid"
    assert heavy.json().get("reason") == "too_expensive" or "too_expensive" in str(heavy.json())
    # the scheduler (non-interactive) path is not blocked by the interactive estimate
    res = engine.run(world.ctx("analyst"), [world.company], body, engine.QueryOptions(interactive=False))
    assert res["rows"]
    # row limit + truncation flag
    monkeypatch.setattr(engine, "MAX_INTERACTIVE_COST", 5_000_000)
    limited = query(api, {**body, "limit": 1})
    assert limited.json()["truncated"] is True and len(limited.json()["rows"]) == 1


def test_T_P17_10_cache_keyed_by_policy_version_and_data(world: World) -> None:
    sales(world)
    ctx = world.ctx("analyst")
    body = {"entity": "ledger.journal_line", "measures": ["net_credit"], "filters": [SALES]}
    first = engine.run(ctx, [world.company], body)
    again = engine.run(ctx, [world.company], body)
    assert first["cached"] is False and again["cached"] is True and again["rows"] == first["rows"]
    world.policy.grant(ctx.principal_id, "reporting.extra.read")  # any policy change bumps policy_version
    assert engine.run(ctx, [world.company], body)["cached"] is False
    world.post("sales.invoice", "FV-NEW", "1")  # new postings change the freshness probe
    fresh = engine.run(ctx, [world.company], body)
    assert fresh["cached"] is False and fresh["totals"]["net_credit"] == "420001.0000"
    # another principal never reads someone else's cached result
    world.user("twin", ("reporting.*", "ledger.entry.read"))
    assert engine.run(world.ctx("twin"), [world.company], body)["cached"] is False


def test_time_grain_compare_and_hierarchy_labels(world: World, api: ApiClient) -> None:
    sales(world)
    r = query(
        api,
        {
            "entity": "ledger.journal_line",
            "measures": ["net_credit"],
            "dimensions": ["posting_date"],
            "time_grain": "month",
            "filters": [SALES],
        },
    )
    assert [(x["posting_date"], x["net_credit"]) for x in r.json()["rows"]] == [
        ("2026-10-01", "70000.0000"),
        ("2026-11-01", "350000.0000"),
    ]
    cmp = query(
        api,
        {
            "entity": "ledger.journal_line",
            "measures": ["net_credit"],
            "compare_to": "previous_period",
            "filters": [SALES, {"field": "posting_date", "op": "between", "value": ["2026-11-01", "2026-11-30"]}],
        },
    )
    assert cmp.status_code == 200, cmp.content
    assert cmp.json()["rows"] == [{"net_credit": "350000.0000", "net_credit__compare": "70000.0000"}]


def test_report_query_service_interface(world: World) -> None:
    sales(world)
    svc = services.get(ReportQueryService)
    assert isinstance(svc, engine.SemanticReportQueryService)
    ctx = world.ctx("analyst")
    assert "ledger.journal_line" in svc.models(ctx)
    res = svc.run(
        ctx, ReportQuery("ledger.journal_line", ("net_credit",), ("account_code",), {"account_code": "413505"})
    )
    assert res.columns == ("account_code", "net_credit") and res.rows == (("413505", "420000.0000"),)
    with pytest.raises(PermissionDenied):
        svc.run(world.ctx("outsider"), ReportQuery("ledger.journal_line", ("net",)))


def test_saved_reports_crud_run_publish_archive_and_kpis(world: World, api: ApiClient) -> None:
    sales(world)
    url = "/api/v1/reporting/reports"
    definition = {
        "query": {
            "entity": "ledger.journal_line",
            "measures": ["net_credit"],
            "dimensions": ["account_code"],
            "filters": [SALES],
        },
        "view": "bar",
    }
    bad = api.post(
        url,
        {"code": "R1", "name": "Ventas", "definition": {"query": {**definition["query"], "dimensions": ["party_id"]}}},
    )
    assert bad.status_code == 403 and bad.json()["code"] == "field_forbidden"
    created = api.post(url, {"code": "R1", "name": "Ventas por cuenta", "definition": definition})
    assert created.status_code == 201, created.content
    rid = created.json()["id"]
    assert created["Location"].endswith(rid)
    assert api.post(url, {"code": "R1", "name": "x", "definition": definition}).status_code == 409
    etag = api.get(f"{url}/{rid}")["ETag"]
    assert api.send("patch", f"{url}/{rid}", {"name": "Ventas"}).status_code == 428
    patched = api.send("patch", f"{url}/{rid}", {"name": "Ventas mensuales"}, if_match=etag)
    assert patched.status_code == 200 and patched.json()["name"] == "Ventas mensuales"
    run = api.post(f"{url}/{rid}:run")
    assert run.status_code == 200, run.content
    assert run.json()["rows"] == [{"account_code": "413505", "net_credit": "420000.0000"}]
    with world.scope():
        stored = ReportRun.objects.get(id=run.json()["run_id"])
        assert stored.state == "READY" and stored.row_count == 1 and stored.ledger_cutoff_seq >= 4
        assert OutboxMessage.objects.filter(event_type="reporting.report_run.ready.v1").count() == 1
    # another analyst does not see a draft; after publishing it does, and runs it with its own policy
    world.user(
        "colleague", ("reporting.report.read", "reporting.report.update", "reporting.query.read", "ledger.entry.read")
    )
    assert api.as_("colleague").get(f"{url}/{rid}").status_code == 404
    assert api.as_("analyst").post(f"{url}/{rid}:publish").status_code == 200
    assert api.as_("colleague").get(f"{url}/{rid}").status_code == 200
    assert api.as_("colleague").send("patch", f"{url}/{rid}", {"name": "x"}, if_match="*").status_code == 404
    kpis = api.as_("analyst").get("/api/v1/kpis/reportes", period="2026-11")
    assert kpis.status_code == 200, kpis.content
    values = {k["id"]: k["value"] for k in kpis.json()["kpis"]}
    assert values["ejecuciones"] == "1" and values["ejecuciones_fallidas"] == "0"
    assert int(values["reportes_disponibles"]) >= 1
    panel = api.get("/api/v1/kpis/reportes/panels/ejecuciones_diarias", period="2026-11").json()
    assert panel["state"] == "ok" and any(p["values"]["ejecuciones"] == "1" for p in panel["points"])
    # delete refused while runs exist; archive works and stops runs
    assert api.send("delete", f"{url}/{rid}").status_code == 409
    assert api.post(f"{url}/{rid}:archive").json()["status"] == "ARCHIVED"
    assert api.post(f"{url}/{rid}:run").status_code == 409


def test_T_P17_09_scheduled_report_runs_with_the_owners_current_permissions(world: World, api: ApiClient) -> None:
    sales(world)
    fake = FakeSchedulerService()
    with services.override(SchedulerService, fake):
        created = api.post(
            "/api/v1/reporting/reports",
            {
                "code": "R-SCH",
                "name": "Programado",
                "definition": {
                    "query": {"entity": "ledger.journal_line", "measures": ["net_credit"], "filters": [SALES]}
                },
            },
        ).json()
        sched = api.post(
            f"/api/v1/reporting/reports/{created['id']}:schedule",
            {"parameters": {"cron": "30 7 * * *", "format": "pdf"}},
        )
        assert sched.status_code == 200, sched.content
        (job,) = fake.jobs.values()
        assert job.task_name == reports.JOB_TYPE and job.args["report_id"] == created["id"]
    owner = world.ctx("analyst")
    with world.scope("analyst"):
        report = SavedReport.objects.get(id=created["id"])
        assert report.schedule == "30 7 * * *" and report.scheduled_job_id == job.id

    class Job:
        company_id = world.company

    out = run_in_unit_of_work(lambda u: reports.scheduled_handler(owner, Job(), dict(job.args), "k1"), owner)
    assert out["rows"] == 1
    with world.scope():
        assert OutboxMessage.objects.filter(event_type="reporting.report.scheduled_run_completed.v1").count() == 1
    # the owner loses the ledger read permission: the next scheduled run fails closed (no data delivered)
    world.policy.revoke(owner.principal_id, "ledger.entry.read")
    with pytest.raises(PermissionDenied):
        run_in_unit_of_work(lambda u: reports.scheduled_handler(owner, Job(), dict(job.args), "k2"), owner)
    with world.scope():
        assert ReportRun.objects.filter(report_id=created["id"], trigger="scheduler").count() == 1
