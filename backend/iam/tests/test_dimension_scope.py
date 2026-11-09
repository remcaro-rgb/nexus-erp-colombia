"""T-ACC-DIM-09: subtree grant on cost centers limits list / tree / search / export results."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from iam import dimensions as iam_dims
from iam import policy
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine
from iam.testing import probe
from iam.testing.fakes import ListableDimensions
from kernel import ids, services, tenancy
from kernel.interfaces.dimensions import DimensionNode, Dimensions

pytestmark = pytest.mark.django_db

DIM_GRANTS = ["ledger.dimension.read", "ledger.dimension.export", "iam.cost_center_scope.read", "iam.me.read"]


def _tree(world):
    alfa, beta = world.companies["ALFA"], world.companies["BETA"]
    n = {}

    def node(code, parent=None, kind="AGRUPADOR", company=alfa):
        n[code] = DimensionNode(
            ids.new_id(), company, "cost_center", code, f"Centro {code}", kind, n[parent].id if parent else None
        )

    node("ROOT")
    node("ADM", "ROOT")
    node("ADM-01", "ADM", "DETALLE")
    node("ADM-02", "ADM", "DETALLE")
    node("OPS", "ROOT")
    node("OPS-01", "OPS", "DETALLE")
    node("B-ROOT", company=beta)
    return n


@pytest.fixture
def dims(world):
    nodes = _tree(world)
    fake = ListableDimensions(list(nodes.values()))
    with services.override(Dimensions, fake):
        yield nodes


@pytest.fixture
def people(world, admin, dims):
    world.add_role("aux_dim", DIM_GRANTS)
    world.add_user("aux", ["ALFA"], subject="sub-aux")
    world.add_user("boss", ["ALFA"], subject="sub-boss")
    world.assign("aux", "aux_dim", "ALFA", scope={"cost_center_ids": [str(dims["ADM"].id)]})
    world.assign("boss", "aux_dim", "ALFA")
    return dims


def _codes(resp):
    assert resp.status_code == 200, resp.content
    return sorted(i["code"] for i in resp.json()["items"])


def test_T_ACC_DIM_09_subtree_grant_limits_list_tree_search_and_export(world, login, people):
    dims = people
    aux, boss = login("aux"), login("boss")

    # list
    assert _codes(aux.get("/api/v1/iam/cost-centers")) == ["ADM", "ADM-01", "ADM-02"]
    assert _codes(boss.get("/api/v1/iam/cost-centers")) == ["ADM", "ADM-01", "ADM-02", "OPS", "OPS-01", "ROOT"]
    # detail outside the subtree → 404
    assert aux.get(f"/api/v1/iam/cost-centers/{dims['OPS-01'].id}").status_code == 404
    assert aux.get(f"/api/v1/iam/cost-centers/{dims['ADM-01'].id}").status_code == 200
    assert boss.get(f"/api/v1/iam/cost-centers/{dims['B-ROOT'].id}").status_code == 404  # other company
    # search
    assert _codes(aux.get("/api/v1/iam/cost-centers", q="OPS")) == []
    assert _codes(boss.get("/api/v1/iam/cost-centers", q="OPS")) == ["OPS", "OPS-01"]
    assert _codes(aux.get("/api/v1/iam/cost-centers", q="02")) == ["ADM-02"]
    # tree: the granted node becomes the root; nothing outside it
    tree = aux.get("/api/v1/iam/cost-centers", view="tree").json()["items"]
    assert [t["code"] for t in tree] == ["ADM"]
    assert [c["code"] for c in tree[0]["children"]] == ["ADM-01", "ADM-02"]
    boss_tree = boss.get("/api/v1/iam/cost-centers", view="tree").json()["items"]
    assert [t["code"] for t in boss_tree] == ["ROOT"] and len(boss_tree[0]["children"]) == 2

    # rows carrying cost_center_id: list/search through scope_queryset, export through secure_rows
    with connection.cursor() as cur:
        cur.execute(probe.SCOPED_LINE_SQL)
    ctx_aux = world.ctx(["ALFA"], principal=world.users["aux"].id)
    ctx_boss = world.ctx(["ALFA"], principal=world.users["boss"].id)
    with tenancy.scope(world.ctx()):
        for code in ("ADM-01", "ADM-02", "OPS-01"):
            probe.ScopedLine.objects.create(
                tenant_id=world.tenant_id,
                company_id=world.companies["ALFA"],
                cost_center_id=dims[code].id,
                description=f"gasto {code}",
                amount=Decimal("10.0000"),
            )
        probe.ScopedLine.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.companies["ALFA"],
            cost_center_id=None,
            description="sin centro",
            amount=Decimal("1.0000"),
        )
    with tenancy.scope(ctx_aux):
        qs = policy.scope_queryset(ctx_aux, "ledger.dimension.read", probe.ScopedLine.objects.all())
        assert sorted(qs.values_list("description", flat=True)) == ["gasto ADM-01", "gasto ADM-02"]
        assert list(qs.filter(description__icontains="OPS")) == []
        rows = list(
            probe.ScopedLine.objects.values("id", "tenant_id", "company_id", "cost_center_id", "description", "amount")
        )
    with tenancy.scope(ctx_boss):
        qs = policy.scope_queryset(ctx_boss, "ledger.dimension.read", probe.ScopedLine.objects.all())
        assert qs.count() == 4

    field_registry.register(
        "ledger.dimension",
        {
            "id": "public",
            "tenant_id": "internal",
            "company_id": "internal",
            "cost_center_id": "internal",
            "description": "internal",
            "amount": "confidential",
        },
    )
    exported = policy.secure_rows(ctx_aux, "ledger.dimension.export", "ledger.dimension", rows)
    assert sorted(r["description"] for r in exported) == ["gasto ADM-01", "gasto ADM-02"]
    assert all("amount" not in r for r in exported)  # confidential > role max_classification (internal)
    assert len(policy.secure_rows(ctx_boss, "ledger.dimension.export", "ledger.dimension", rows)) == 4
    # authorize() on a single resource follows the same scope
    assert policy.authorize(
        ctx_aux, "ledger.dimension.read", {"company_id": world.companies["ALFA"], "cost_center_id": dims["ADM-01"].id}
    ).allowed
    assert not policy.authorize(
        ctx_aux, "ledger.dimension.read", {"company_id": world.companies["ALFA"], "cost_center_id": dims["OPS-01"].id}
    ).allowed


def test_T_ACC_DIM_09_role_scope_by_cost_center_after_g1_wiring(world, admin, login, dims):
    """Assignment via the API validates the node through kernel.interfaces.Dimensions; no service → fail closed."""
    world.add_role("aux_dim", DIM_GRANTS)
    world.add_user("carla", ["ALFA"], subject="sub-carla")
    admin_api = login(admin, "ALFA")
    role = world.roles["aux_dim"]
    r = admin_api.post(
        "/api/v1/iam/role-assignments",
        {
            "user_id": str(world.users["carla"].id),
            "role_id": str(role.id),
            "scope": {"cost_center_ids": [str(dims["B-ROOT"].id)]},
        },
    )
    assert r.status_code == 422  # node of another company
    r = admin_api.post(
        "/api/v1/iam/role-assignments",
        {
            "user_id": str(world.users["carla"].id),
            "role_id": str(role.id),
            "scope": {"cost_center_ids": [str(dims["OPS"].id)]},
        },
    )
    assert r.status_code == 201, r.content
    carla = login("carla")
    assert _codes(carla.get("/api/v1/iam/cost-centers")) == ["OPS", "OPS-01"]
    # revocation: deleting the assignment takes effect on the next request (policy_version bump)
    detail = admin_api.get(f"/api/v1/iam/role-assignments/{r.json()['id']}")
    assert detail.status_code == 200
    from iam.api.base import etag_for

    etag = etag_for("iam.role_assignment", r.json()["id"], r.json()["version"])
    assert admin_api.delete(f"/api/v1/iam/role-assignments/{r.json()['id']}", HTTP_IF_MATCH=etag).status_code == 204
    assert carla.get("/api/v1/iam/cost-centers").status_code == 403
    # without a Dimensions implementation a cost-center scope grants nothing (fail closed)
    world.assign("carla", "aux_dim", "ALFA", scope={"cost_center_ids": [str(dims["ADM"].id)]})
    ctx = world.ctx(["ALFA"], principal=world.users["carla"].id)
    assert iam_dims.visible_ids(ctx, "iam.cost_center_scope.read", world.companies["ALFA"]) == frozenset(
        {dims["ADM"].id, dims["ADM-01"].id, dims["ADM-02"].id}
    )
    services.unregister(Dimensions)
    from django.core.cache import cache

    cache.clear()
    assert iam_dims.visible_ids(ctx, "iam.cost_center_scope.read", world.companies["ALFA"]) == frozenset()
    assert (
        not get_engine()
        .authorize(
            ctx, "ledger.dimension.read", {"company_id": world.companies["ALFA"], "cost_center_id": dims["ADM-01"].id}
        )
        .allowed
    )
