"""HTTP contract behaviour of ``/api/v1/assets/…`` and ``/api/v1/kpis/activos`` (real PostgreSQL, forced RLS)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from assets.testing import context as test_context
from assets.tests.conftest import PERMISSIONS, World
from kernel import ids
from kernel.interfaces.policy import FieldMask
from kernel.testing.tenancy import make_context

pytestmark = pytest.mark.django_db
D = Decimal
BASE = "/api/v1/assets"


@pytest.fixture
def api(world: World, settings: Any) -> Any:
    settings.ROOT_URLCONF = "assets.testing.urls"
    settings.ERP_TENANT_CONTEXT_RESOLVER = "assets.testing.context.resolve_context"

    def client(company: uuid.UUID | None = None, ctx: Any = None) -> APIClient:
        c = APIClient()
        c.credentials(
            HTTP_X_COMPANY_ID=str(company or world.company), HTTP_X_TEST_CONTEXT=test_context.register(ctx or world.ctx)
        )
        return c

    return client


def idem() -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"test-{ids.new_id()}"}


def new_asset(c: APIClient, world: World, code: str = "LAP-API", cost: str = "4800000", **extra: Any) -> Any:
    body = {
        "code": code,
        "name": f"Portátil {code} (SINTÉTICO)",
        "category_id": str(world.category.id),
        "acquisition_cost": {"amount": cost, "currency": "COP"},
        "acquisition_date": "2026-03-10",
        **extra,
    }
    return c.post(f"{BASE}/assets", body, format="json", **idem())


def activate(c: APIClient, asset_id: str) -> Any:
    return c.post(
        f"{BASE}/assets/{asset_id}:activate",
        {"parameters": {"in_service_date": "2026-03-10", "counterpart_role": "BANK"}},
        format="json",
        **idem(),
    )


def test_unauthenticated_is_401(api: Any, world: World) -> None:
    c = APIClient()
    assert c.get(f"{BASE}/assets").status_code == 401


def test_deny_by_default_403_without_grant(api: Any, world: World) -> None:
    stranger = make_context(tenant_id=world.ctx.tenant_id, company_ids=[world.company])
    c = api(ctx=stranger)
    for path in (
        "assets",
        "categories",
        "books",
        "depreciation-runs",
        "revaluations",
        "impairments",
        "physical-counts",
        "reports/register",
        "reports/movement",
    ):
        r = c.get(f"{BASE}/{path}")
        assert r.status_code == 403, path
        assert r.json()["code"] == "policy_denied"
    assert new_asset(c, world).status_code == 403
    kpi = c.get("/api/v1/kpis/activos")
    assert kpi.status_code == 200 and kpi.json()["kpis"] == []  # every KPI absent, never zeros of others


def test_asset_crud_idempotency_and_if_match(api: Any, world: World) -> None:
    c = api()
    key = {"HTTP_IDEMPOTENCY_KEY": f"test-{ids.new_id()}"}
    body = {
        "code": "LAP-IDEM",
        "name": "Portátil (SINTÉTICO)",
        "category_id": str(world.category.id),
        "acquisition_cost": {"amount": "1500000", "currency": "COP"},
        "acquisition_date": "2026-03-10",
    }
    first = c.post(f"{BASE}/assets", body, format="json", **key)
    assert first.status_code == 201, first.json()
    assert first["Location"].endswith(first.json()["id"])
    replay = c.post(f"{BASE}/assets", body, format="json", **key)
    assert replay.status_code == 201 and replay["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    assert c.post(f"{BASE}/assets", body, format="json").status_code == 400  # Idempotency-Key required
    asset = first.json()
    assert asset["status"] == "ACTIVE" and asset["acquisition_cost"] == {"amount": "1500000.0000", "currency": "COP"}
    got = c.get(f"{BASE}/assets/{asset['id']}")
    etag = got["ETag"]
    assert c.patch(f"{BASE}/assets/{asset['id']}", {"name": "x"}, format="json", **idem()).status_code == 428
    assert (
        c.patch(
            f"{BASE}/assets/{asset['id']}", {"name": "x"}, format="json", HTTP_IF_MATCH='W/"stale"', **idem()
        ).status_code
        == 412
    )
    ok = c.patch(
        f"{BASE}/assets/{asset['id']}", {"name": "Portátil renombrado"}, format="json", HTTP_IF_MATCH=etag, **idem()
    )
    assert ok.status_code == 200 and ok.json()["name"] == "Portátil renombrado"
    bad = c.post(f"{BASE}/assets", {**body, "code": "X", "bogus": 1}, format="json", **idem())
    assert bad.status_code == 422
    listing = c.get(f"{BASE}/assets", {"filter[code][eq]": "LAP-IDEM", "sort": "-code", "limit": 5})
    assert [i["code"] for i in listing.json()["items"]] == ["LAP-IDEM"]
    assert c.get(f"{BASE}/assets", {"filter[secret][eq]": "1"}).status_code == 400
    deleted = c.delete(f"{BASE}/assets/{asset['id']}", HTTP_IF_MATCH=ok["ETag"], **idem())
    assert deleted.status_code == 204


def test_custodian_is_masked_without_field_grant(api: Any, world: World, asset_services: dict[str, Any]) -> None:
    c = api()
    custodian = str(ids.new_id())
    r = new_asset(c, world, custodian_employee_id=custodian)
    assert r.status_code == 403 and r.json()["code"] == "field_forbidden"  # not writable without the field grant
    policy = asset_services["policy"]
    original = policy.field_mask
    policy.field_mask = lambda ctx, res: FieldMask(
        frozenset({"custodian_employee_id"}), frozenset({"custodian_employee_id"})
    )
    r = new_asset(c, world, custodian_employee_id=custodian)
    assert r.status_code == 201 and r.json()["custodian_employee_id"] == custodian
    policy.field_mask = original
    got = c.get(f"{BASE}/assets/{r.json()['id']}").json()
    assert "custodian_employee_id" not in got
    listing = c.get(f"{BASE}/assets").json()["items"]
    assert all("custodian_employee_id" not in i for i in listing)
    report = c.get(f"{BASE}/reports/register").json()
    assert custodian not in str(report)


def test_other_company_header_is_refused_and_rows_do_not_leak(api: Any, world: World) -> None:
    c = api()
    created = new_asset(c, world)
    assert created.status_code == 201
    foreign = api(company=uuid.uuid4())
    assert foreign.get(f"{BASE}/assets").status_code in (403, 404, 422)
    other = api(company=world.other_company)
    assert other.get(f"{BASE}/assets").json()["items"] == []
    assert other.get(f"{BASE}/assets/{created.json()['id']}").status_code == 404
    assert world.other_company and other.get(f"{BASE}/reports/register").json()["rows"] == []


def test_depreciation_run_api_preview_post_and_reports(api: Any, world: World) -> None:
    c = api()
    asset = new_asset(c, world).json()
    assert activate(c, asset["id"]).status_code == 200
    period = world.period("2026-04")
    run = c.post(
        f"{BASE}/depreciation-runs",
        {"document_date": "2026-04-30", "period_id": str(period.id), "book_id": str(world.niif.id)},
        format="json",
        **idem(),
    )
    assert run.status_code == 201, run.json()
    rid = run.json()["id"]
    dup = c.post(
        f"{BASE}/depreciation-runs",
        {"document_date": "2026-04-30", "period_id": str(period.id), "book_id": str(world.niif.id)},
        format="json",
        **idem(),
    )
    assert dup.status_code == 409 and dup.json()["code"] == "depreciation_run_exists"
    preview = c.post(f"{BASE}/depreciation-runs/{rid}:preview", {}, format="json")
    assert preview.status_code == 200
    sim = preview.json()
    assert sim["persisted"] is False
    lines = sim["journal_entries"][0]["lines"]
    assert {ln["account"] for ln in lines} == {"516015", "159215"}
    assert world.entries("assets.depreciation_run") == []  # preview persisted nothing
    posted = c.post(f"{BASE}/depreciation-runs/{rid}:post", {}, format="json", **idem())
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["status"] == "POSTED" and body["total"]["amount"] == "133333.3300"
    assert body["lines"][0]["item_id"] == asset["id"]
    again = c.post(f"{BASE}/depreciation-runs/{rid}:post", {}, format="json", **idem())
    assert again.status_code == 200 and len(world.entries("assets.depreciation_run")) == 1
    reg = c.get(f"{BASE}/reports/register", {"book_id": str(world.niif.id)}).json()
    assert reg["rows"][0]["net_book_value"] == "4666666.6700" and reg["synthetic"] is True
    sched = c.get(f"{BASE}/reports/depreciation-schedule", {"asset_id": asset["id"], "book_id": str(world.niif.id)})
    rows = sched.json()["rows"]
    assert rows[0] == {
        "period": "2026-04",
        "amount": "133333.3300",
        "accumulated": "133333.3300",
        "net_book_value": "4666666.6700",
        "posted": True,
    }
    assert len(rows) == 36 and rows[-1]["amount"] == "133333.4500"
    mov = c.get(
        f"{BASE}/reports/movement", {"period_from": "2026-01", "period_to": "2026-12", "book_id": str(world.niif.id)}
    ).json()
    assert mov["rows"] == [
        {
            "category": "COMPUTO",
            "opening": "0.0000",
            "additions": "4800000.0000",
            "revaluations": "0.0000",
            "impairments": "0.0000",
            "disposals": "0.0000",
            "depreciation": "-133333.3300",
            "transfers": "0.0000",
            "closing": "4666666.6700",
        }
    ]
    assert c.get(f"{BASE}/reports/movement", {"period_from": "2026-13"}).status_code == 400


def test_dispose_action_requires_reason_and_void_permission(
    api: Any, world: World, asset_services: dict[str, Any]
) -> None:
    c = api()
    asset = new_asset(c, world).json()
    activate(c, asset["id"])
    r = c.post(f"{BASE}/assets/{asset['id']}:dispose", {"parameters": {"kind": "SCRAP"}}, format="json", **idem())
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    ok = c.post(
        f"{BASE}/assets/{asset['id']}:dispose",
        {"reason": "Daño total", "effective_date": "2026-04-15", "parameters": {"kind": "SCRAP"}},
        format="json",
        **idem(),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "ARCHIVED"
    again = c.post(
        f"{BASE}/assets/{asset['id']}:dispose",
        {"reason": "x", "parameters": {"kind": "SCRAP"}},
        format="json",
        **idem(),
    )
    assert again.status_code == 409 and again.json()["code"] == "asset_already_disposed"
    limited = make_context(tenant_id=world.ctx.tenant_id, company_ids=[world.company])
    for perm in PERMISSIONS:
        if perm != "assets.asset.void":
            asset_services["policy"].grant(limited.principal_id, perm)
    second = new_asset(api(ctx=limited), world, code="LAP-2").json()
    activate(api(ctx=limited), second["id"])
    denied = api(ctx=limited).post(
        f"{BASE}/assets/{second['id']}:dispose",
        {"reason": "x", "parameters": {"kind": "SCRAP"}},
        format="json",
        **idem(),
    )
    assert denied.status_code == 403


def test_categories_books_counts_valuations_and_bulk(api: Any, world: World) -> None:
    c = api()
    cat = c.post(
        f"{BASE}/categories",
        {"code": "VEH", "name": "Vehículos (SINTÉTICO)", "useful_life_months": 60, "method": "STRAIGHT_LINE"},
        format="json",
        **idem(),
    )
    assert cat.status_code == 201 and cat.json()["account_role"] == "FIXED_ASSET_COST"
    assert (
        c.post(
            f"{BASE}/categories",
            {"code": "BAD", "name": "x", "useful_life_months": 0, "method": "X"},
            format="json",
            **idem(),
        ).status_code
        == 422
    )
    books = c.get(f"{BASE}/books").json()["items"]
    assert sorted(b["framework"] for b in books) == ["FISCAL", "NIIF"]
    bulk = c.post(
        f"{BASE}/assets:bulk",
        {
            "items": [
                {
                    "code": "B-1",
                    "name": "Silla (SINTÉTICO)",
                    "category_id": cat.json()["id"],
                    "acquisition_cost": {"amount": "900000", "currency": "COP"},
                    "acquisition_date": "2026-05-01",
                },
                {
                    "code": "B-2",
                    "name": "Mesa",
                    "category_id": str(uuid.uuid4()),
                    "acquisition_cost": {"amount": "1", "currency": "COP"},
                    "acquisition_date": "2026-05-01",
                },
            ]
        },
        format="json",
        **idem(),
    )
    assert bulk.status_code == 200
    assert [r["status"] for r in bulk.json()["results"]] == ["skipped", "failed"]
    assert c.get(f"{BASE}/assets", {"filter[code][eq]": "B-1"}).json()["items"] == []  # all or nothing
    asset = new_asset(c, world, code="LAP-V").json()
    activate(c, asset["id"])
    imp = c.post(
        f"{BASE}/impairments",
        {
            "asset_id": asset["id"],
            "document_date": "2026-04-30",
            "lines": [{"line_no": 1, "amount": {"amount": "4000000", "currency": "COP"}}],
        },
        format="json",
        **idem(),
    )
    assert imp.status_code == 201
    sim = c.post(f"{BASE}/impairments/{imp.json()['id']}:simulate", {}, format="json")
    assert sim.status_code == 200 and sim.json()["persisted"] is False
    post = c.post(f"{BASE}/impairments/{imp.json()['id']}:post", {}, format="json", **idem())
    assert post.status_code == 200 and post.json()["total"]["amount"] == "800000.0000"
    etag = c.get(f"{BASE}/impairments/{imp.json()['id']}")["ETag"]
    locked = c.patch(
        f"{BASE}/impairments/{imp.json()['id']}", {"notes": "x"}, format="json", HTTP_IF_MATCH=etag, **idem()
    )
    assert locked.status_code == 409 and locked.json()["code"] == "document_immutable"
    cnt = c.post(
        f"{BASE}/physical-counts",
        {"document_date": "2026-11-09", "lines": [{"line_no": 1, "description": "LAP-V"}]},
        format="json",
        **idem(),
    )
    assert cnt.status_code == 201
    sub = c.post(f"{BASE}/physical-counts/{cnt.json()['id']}:submit", {}, format="json", **idem())
    assert sub.status_code == 200 and sub.json()["status"] == "SUBMITTED"
    same = c.post(f"{BASE}/physical-counts/{cnt.json()['id']}:approve", {}, format="json", **idem())
    assert same.status_code == 403 and same.json()["code"] == "sod_violation"


def test_kpis_and_panel(api: Any, world: World, asset_services: dict[str, Any]) -> None:
    c = api()
    empty = c.get("/api/v1/kpis/activos", {"period": "2026-04"}).json()
    by_id = {k["id"]: k for k in empty["kpis"]}
    assert by_id["costo_historico"]["state"] == "no_data" and by_id["costo_historico"]["value"] == "0.00"
    asset = new_asset(c, world).json()
    activate(c, asset["id"])
    period = world.period("2026-04")
    pending = {k["id"]: k for k in c.get("/api/v1/kpis/activos", {"period_id": str(period.id)}).json()["kpis"]}
    assert pending["sin_depreciar"]["value"] == "1"
    world.depreciate("2026-04", world.niif)
    world.depreciate("2026-04", world.fiscal)
    kpis = {k["id"]: k for k in c.get("/api/v1/kpis/activos", {"period_id": str(period.id)}).json()["kpis"]}
    assert kpis["costo_historico"]["value"] == "4800000.00" and kpis["costo_historico"]["previous"] == "4800000.00"
    assert kpis["depreciacion_periodo"]["value"] == "133333.33"
    assert kpis["depreciacion_periodo"]["by_book"] == {"FISCAL": "80000.00", "NIIF": "133333.33"}
    assert kpis["valor_neto"]["value"] == "4666666.67" and kpis["valor_neto"]["delta"] == "-133333.33"
    assert kpis["sin_depreciar"]["value"] == "0"
    panel = c.get("/api/v1/kpis/activos/panels/depreciacion_mensual", {"period": "2026-04"}).json()
    assert panel["state"] == "ok" and panel["points"][-1] == {
        "label": "2026-04",
        "values": {"FISCAL": "80000.00", "NIIF": "133333.33"},
    }
    assert c.get("/api/v1/kpis/activos/panels/otro").status_code == 404
    # a principal with only the register permission does not get the depreciation-run KPI
    reader = make_context(tenant_id=world.ctx.tenant_id, company_ids=[world.company])
    asset_services["policy"].grant(reader.principal_id, "assets.asset_register.read")
    ids_only = [k["id"] for k in api(ctx=reader).get("/api/v1/kpis/activos").json()["kpis"]]
    assert ids_only == ["costo_historico", "depreciacion_periodo", "valor_neto"]
    # company switch: the other company's KPIs never carry company A values
    other = {
        k["id"]: k
        for k in api(company=world.other_company).get("/api/v1/kpis/activos", {"period": "2026-04"}).json()["kpis"]
    }
    assert other["costo_historico"]["value"] == "0.00"
