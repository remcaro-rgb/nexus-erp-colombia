"""HTTP behaviour of ``/api/v1/consolidation/…`` and ``/api/v1/intercompany/…`` against the frozen contracts."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from rest_framework.test import APIClient

from consolidation import service
from consolidation.models import ConsolidationRun
from consolidation.testing import context as test_context
from consolidation.tests.conftest import PERMISSIONS, World, s10_world
from kernel import ids, tenancy
from kernel.models import FeatureFlagOverride

pytestmark = pytest.mark.django_db
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "openapi"
_docs: dict[str, Any] = {}


def _doc(name: str) -> Any:
    if name not in _docs:
        _docs[name] = yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))
    return _docs[name]


def _resolve(node: Any, base: str) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target, _, pointer = node["$ref"].partition("#")
            doc_name = base if not target else str(Path(base).parent / target).replace("modules/../", "")
            cur = _doc(doc_name)
            for part in pointer.strip("/").split("/"):
                cur = cur[part]
            return _resolve(cur, doc_name)
        return {k: _resolve(v, base) for k, v in node.items() if not k.startswith("x-")}
    if isinstance(node, list):
        return [_resolve(v, base) for v in node]
    return node


def conforms(module: str, schema: str, body: Any) -> None:
    base = f"modules/{module}.yaml"
    jsonschema.validate(body, _resolve(_doc(base)["components"]["schemas"][schema], base))


def conforms_root(schema: str, body: Any) -> None:
    jsonschema.validate(body, _resolve(_doc("root.yaml")["components"]["schemas"][schema], "root.yaml"))


@pytest.fixture
def api(world: World, settings: Any) -> Any:
    settings.ROOT_URLCONF = "consolidation.testing.urls"
    settings.ERP_TENANT_CONTEXT_RESOLVER = "consolidation.testing.context.resolve_context"

    def client(ctx: Any = None) -> APIClient:
        c = APIClient()
        c.credentials(HTTP_X_TEST_CONTEXT=test_context.register(ctx or world.ctx))
        return c

    return client


def idem() -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"test-{ids.new_id()}"}


def _group(c: APIClient, world: World, code: str = "GRUPO") -> Any:
    body = {
        "code": code,
        "name": "Grupo Andina (SINTÉTICO)",
        "companies": [str(world.a), str(world.b)],
        "presentation_currency": "COP",
    }
    return c.post("/api/v1/consolidation/groups", body, format="json", **idem())


ROUTES = [
    ("get", "/api/v1/consolidation/groups"),
    ("get", "/api/v1/consolidation/runs"),
    ("get", "/api/v1/consolidation/reports/balance"),
    ("get", "/api/v1/consolidation/reports/income-statement"),
    ("get", "/api/v1/consolidation/reports/eliminations"),
    ("get", "/api/v1/intercompany/agreements"),
    ("get", "/api/v1/intercompany/reconciliation"),
    ("post", "/api/v1/consolidation/groups"),
    ("post", "/api/v1/consolidation/runs"),
    ("post", "/api/v1/intercompany/agreements"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_unauthenticated_is_401(api: Any, method: str, path: str) -> None:
    assert getattr(APIClient(), method)(path, {}, format="json").status_code == 401


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_deny_by_default_403(api: Any, world: World, method: str, path: str) -> None:
    stranger = world.user([world.a, world.b], perms=[])
    resp = getattr(api(stranger), method)(path, {}, format="json", **idem())
    assert resp.status_code == 403, resp.content
    assert resp.json()["code"] == "policy_denied"


def test_group_lifecycle_contract_etag_and_idempotency(api: Any, world: World) -> None:
    c = api()
    key = idem()
    body = {
        "code": "G1",
        "name": "Grupo 1 (SINTÉTICO)",
        "companies": [str(world.a), str(world.b)],
        "presentation_currency": "COP",
    }
    created = c.post("/api/v1/consolidation/groups", body, format="json", **key)
    assert created.status_code == 201 and created["Idempotent-Replayed"] == "false"
    conforms("consolidation", "ConsolidationGroup", created.json())
    replay = c.post("/api/v1/consolidation/groups", body, format="json", **key)
    assert (
        replay.status_code == 201
        and replay["Idempotent-Replayed"] == "true"
        and replay.json()["id"] == created.json()["id"]
    )
    gid = created.json()["id"]
    got = c.get(f"/api/v1/consolidation/groups/{gid}")
    assert got.status_code == 200 and got.json()["companies"] == [str(world.a), str(world.b)]
    no_match = c.patch(f"/api/v1/consolidation/groups/{gid}", {"name": "X"}, format="json", **idem())
    assert no_match.status_code == 428
    stale = c.patch(
        f"/api/v1/consolidation/groups/{gid}", {"name": "X"}, format="json", HTTP_IF_MATCH='W/"stale"', **idem()
    )
    assert stale.status_code == 412
    ok = c.patch(
        f"/api/v1/consolidation/groups/{gid}",
        {"name": "Grupo renombrado"},
        format="json",
        HTTP_IF_MATCH=got["ETag"],
        **idem(),
    )
    assert ok.status_code == 200 and ok.json()["version"] == 2
    unknown = c.post("/api/v1/consolidation/groups", {**body, "owner": "x"}, format="json", **idem())
    assert unknown.status_code == 422
    listed = c.get("/api/v1/consolidation/groups")
    assert [g["code"] for g in listed.json()["items"]] == ["G1"]
    assert c.delete(f"/api/v1/consolidation/groups/{gid}", **idem()).status_code == 204
    assert c.get(f"/api/v1/consolidation/groups/{gid}").json()["status"] == "ARCHIVED"


def test_writes_require_idempotency_key(api: Any, world: World) -> None:
    resp = api().post("/api/v1/consolidation/groups", {"code": "X"}, format="json")
    assert resp.status_code == 400 and resp.json()["code"] == "idempotency_key_required"


def test_run_preview_post_and_reports_over_http(api: Any, world: World) -> None:
    s10_world(world)
    c = api()
    gid = _group(c, world).json()["id"]
    with tenancy.scope(world.ctx):
        group = service.get_group(world.ctx, uuid.UUID(gid), "consolidation.group.read")
    world.run(lambda u: service.configure_member(u, group, world.b, investment_account_code="120505"))
    run = c.post(
        "/api/v1/consolidation/runs",
        {"group_id": gid, "period": "2026-11", "document_date": "2026-11-30"},
        format="json",
        **idem(),
    )
    assert run.status_code == 201, run.content
    conforms("consolidation", "ConsolidationRun", run.json())
    rid = run.json()["id"]
    preview = c.post(f"/api/v1/consolidation/runs/{rid}:preview", {}, format="json", **idem())
    assert preview.status_code == 200
    conforms_root("SimulationResult", preview.json())
    draft_report = c.get(f"/api/v1/consolidation/reports/balance?run_id={rid}")
    assert draft_report.status_code == 200 and draft_report.json()["parameters"]["posted"] is False
    sod = c.post(f"/api/v1/consolidation/runs/{rid}:post", {}, format="json", **idem())
    assert sod.status_code == 403 and sod.json()["code"] == "sod_violation"
    posted = api(world.poster).post(f"/api/v1/consolidation/runs/{rid}:post", {}, format="json", **idem())
    assert posted.status_code == 200 and posted.json()["status"] == "POSTED"
    conforms("consolidation", "ConsolidationRun", posted.json())
    assert (
        c.patch(
            f"/api/v1/consolidation/runs/{rid}", {"notes": "x"}, format="json", HTTP_IF_MATCH="*", **idem()
        ).status_code
        == 409
    )
    assert c.delete(f"/api/v1/consolidation/runs/{rid}", **idem()).status_code == 409
    for name in ("balance", "income-statement", "eliminations"):
        rep = c.get(f"/api/v1/consolidation/reports/{name}")  # latest posted run
        assert rep.status_code == 200
        conforms_root("ReportResult", rep.json())
        assert rep.json()["parameters"]["run_id"] == rid and rep.json()["synthetic"] is True
    bal = c.get("/api/v1/consolidation/reports/balance").json()
    assert bal["totals"]["difference"] == "0.0000"
    csv = c.get(f"/api/v1/consolidation/reports/eliminations?run_id={rid}&format=csv")
    assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv")
    assert csv.content.decode().splitlines()[0].startswith("Asiento,")
    assert c.get("/api/v1/consolidation/reports/balance?format=xml").status_code == 400
    runs = c.get("/api/v1/consolidation/runs?filter[status]=POSTED").json()["items"]
    assert [r["id"] for r in runs] == [rid]


def test_hidden_company_resources_are_404(api: Any, world: World) -> None:
    c = api()
    gid = _group(c, world).json()["id"]
    only_a = world.user([world.a])
    assert api(only_a).get(f"/api/v1/consolidation/groups/{gid}").status_code == 404
    assert api(only_a).get("/api/v1/consolidation/groups").json()["items"] == []
    assert c.get(f"/api/v1/consolidation/groups/{uuid.uuid4()}").status_code == 404


def test_feature_flag_off_is_403(api: Any, world: World) -> None:
    def off(u: Any) -> None:
        FeatureFlagOverride.objects.filter(tenant_id=world.ctx.tenant_id).delete()

    world.run(off)
    resp = _group(api(), world)
    assert resp.status_code == 403 and resp.json()["code"] == "feature_disabled"


def test_agreements_crud_and_reconciliation_report(api: Any, world: World) -> None:
    c = api()
    body = {
        "code": "AB",
        "name": "A → B (SINTÉTICO)",
        "company_a_id": str(world.a),
        "company_b_id": str(world.b),
        "accounts": {"expense": "EXPENSE_IC", "auto_mirror": True},
    }
    created = c.post("/api/v1/intercompany/agreements", body, format="json", **idem())
    assert created.status_code == 201, created.content
    conforms("intercompany", "IntercompanyAgreement", created.json())
    assert created.json()["accounts"]["ic_receivable"] == "IC_RECEIVABLE"
    aid = created.json()["id"]
    dup = c.post("/api/v1/intercompany/agreements", body, format="json", **idem())
    assert dup.status_code == 422
    same = c.post(
        "/api/v1/intercompany/agreements", {**body, "code": "AA", "company_b_id": str(world.a)}, format="json", **idem()
    )
    assert same.status_code == 422
    bad = c.post(
        "/api/v1/intercompany/agreements",
        {**body, "code": "AX", "accounts": {"markup_pct": 1.5}},
        format="json",
        **idem(),
    )
    assert bad.status_code == 422
    got = c.get(f"/api/v1/intercompany/agreements/{aid}")
    upd = c.patch(
        f"/api/v1/intercompany/agreements/{aid}",
        {"accounts": {"pricing_rule": "COST_PLUS", "markup_pct": "30"}},
        format="json",
        HTTP_IF_MATCH=got["ETag"],
        **idem(),
    )
    assert upd.status_code == 200 and upd.json()["accounts"]["pricing_rule"] == "COST_PLUS"
    # an A-only user sees the agreement (one of its companies) but not the reconciliation of the pair
    only_a = world.user([world.a])
    assert api(only_a).get(f"/api/v1/intercompany/agreements/{aid}").status_code == 200
    assert api(only_a).get("/api/v1/intercompany/reconciliation").json()["rows"] == []
    rec = c.get("/api/v1/intercompany/reconciliation?period=2026-11")
    assert rec.status_code == 200
    conforms_root("ReportResult", rec.json())
    assert rec.json()["rows"][0]["status"] == "MIRRORED"
    assert c.get("/api/v1/intercompany/reconciliation?period=2026-13").status_code == 400
    assert api(only_a).delete(f"/api/v1/intercompany/agreements/{aid}", **idem()).status_code == 403
    assert c.delete(f"/api/v1/intercompany/agreements/{aid}", **idem()).status_code == 204
    assert c.get(f"/api/v1/intercompany/agreements/{aid}").json()["status"] == "ARCHIVED"


def test_run_validation_errors(api: Any, world: World) -> None:
    c = api()
    gid = _group(c, world).json()["id"]
    bad_period = c.post(
        "/api/v1/consolidation/runs",
        {"group_id": gid, "period": "2026-1", "document_date": "2026-11-30"},
        format="json",
        **idem(),
    )
    assert bad_period.status_code == 422
    bad_cur = c.post(
        "/api/v1/consolidation/runs",
        {"group_id": gid, "period": "2026-11", "document_date": "2026-11-30", "currency": "USD"},
        format="json",
        **idem(),
    )
    assert bad_cur.status_code == 422
    unbalanced = {
        "group_id": gid,
        "period": "2026-11",
        "document_date": "2026-11-30",
        "lines": [{"line_no": 1, "account_code": "111005", "unit_price": {"amount": "5", "currency": "COP"}}],
    }
    assert c.post("/api/v1/consolidation/runs", unbalanced, format="json", **idem()).status_code == 422
    with tenancy.scope(world.ctx):
        assert not ConsolidationRun.objects.exists()


def test_permissions_are_catalogued() -> None:
    catalog = yaml.safe_load((CONTRACTS.parent / "permissions" / "catalog.yaml").read_text(encoding="utf-8"))
    codes = {c["code"] for c in catalog["codes"]}
    assert set(PERMISSIONS) <= codes
