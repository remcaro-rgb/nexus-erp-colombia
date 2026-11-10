"""T-ACC-DIM-04 (API + DB composite FK) and API contract behaviour of the dimension masters."""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, connection
from rest_framework.test import APIClient

from kernel import ids
from kernel.testing.tenancy import make_context
from ledger.models import JournalEntry, JournalLine
from ledger.tests.conftest import line

pytestmark = pytest.mark.django_db

ALL = [
    "ledger.dimension.read",
    "ledger.dimension.create",
    "ledger.dimension.update",
    "ledger.dimension.delete",
    "ledger.dimension.export",
    "ledger.dimension_rule.read",
    "ledger.dimension_rule.update",
    "ledger.entry.read",
    "ledger.entry.post",
    "ledger.entry.void",
    "ledger.period.read",
    "ledger.account.read",
]


class _User:
    is_authenticated = True

    def __init__(self, pid: uuid.UUID) -> None:
        self.pk = pid


@pytest.fixture
def api(world, settings, ledger_services):
    settings.ROOT_URLCONF = "ledger.testing.urls"
    for perm in ALL:
        ledger_services["policy"].grant(world.ctx.principal_id, perm)

    def client(company=None, ctx=None):
        c = APIClient()
        c.force_authenticate(user=_User((ctx or world.ctx).principal_id), token=ctx or world.ctx)
        c.credentials(HTTP_X_COMPANY_ID=str(company or world.company))
        return c

    return client


def idem() -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"test-{ids.new_id()}"}


def create(c, slug, code, node_type="DETALLE", parent=None):
    r = c.post(
        f"/api/v1/ledger/dimensions/{slug}",
        {
            "code": code,
            "description": f"{code} (SINTÉTICO)",
            "node_type": node_type,
            "parent_id": parent,
            "valid_from": "2026-01-01",
        },
        format="json",
        **idem(),
    )
    return r


def test_T_ACC_DIM_04_cross_company_reference_rejected_by_api(api, world):
    a, b = api(world.company), api(world.other_company)
    group_b = create(b, "cost-centers", "GB", "AGRUPADOR").json()
    leaf_b = create(b, "cost-centers", "GB-1", "DETALLE", group_b["id"]).json()

    # Parent of another company.
    r = create(a, "cost-centers", "A-1", "DETALLE", group_b["id"])
    assert r.status_code == 422 and r.json()["code"] == "dimension_cross_company"
    # Reading/patching/moving another company's node through company A's scope is not possible.
    assert a.get(f"/api/v1/ledger/dimensions/cost-centers/{leaf_b['id']}").status_code == 404
    group_a = create(a, "cost-centers", "GA", "AGRUPADOR").json()
    r = a.post(
        f"/api/v1/ledger/dimensions/cost-centers/{group_a['id']}:move",
        {"parameters": {"parent_id": group_b["id"]}},
        format="json",
        **idem(),
    )
    assert r.status_code == 422 and r.json()["code"] == "dimension_cross_company"

    # Journal line slot referencing company B's leaf: rejected by post and by simulate (same error).
    body = {
        "posting_date": "2026-11-09",
        "lines": [
            {"account": "519595", "debit": "1000", "dimensions": {"cost_center_id": leaf_b["id"]}},
            {"account": "110505", "credit": "1000"},
        ],
    }
    posted = a.post("/api/v1/ledger/entries", body, format="json", **idem())
    simulated = a.post("/api/v1/ledger/journal-entries:simulate", {"parameters": body}, format="json", **idem())
    assert posted.status_code == simulated.status_code == 422
    assert posted.json()["code"] == simulated.json()["code"] == "dimension_cross_company"
    assert posted.json()["detail"] == simulated.json()["detail"]

    # A company outside the principal's context cannot even be selected.
    foreign = make_context(tenant_id=world.ctx.tenant_id)
    r = api(next(iter(foreign.company_ids))).get("/api/v1/ledger/dimensions/cost-centers")
    assert r.status_code == 422

    # Another tenant never sees the nodes.
    stranger = make_context()
    r = api(next(iter(stranger.company_ids)), ctx=stranger).get("/api/v1/ledger/dimensions/cost-centers")
    assert r.status_code in (403, 422) or r.json()["items"] == []


def test_T_ACC_DIM_04_cross_company_reference_rejected_by_db_composite_fk(world):
    leaf_b = world.node("aux_abierto", "B-1", "DETALLE", company=world.other_company)
    leaf_a = world.node("aux_abierto", "A-1", "DETALLE")
    group_b = world.node("aux_abierto", "GB", "AGRUPADOR", company=world.other_company)
    result = world.post([line("EXPENSE", "DEBIT", "10", aux_abierto_id=leaf_a), line("CASH", "CREDIT", "10")])
    with world.scope():
        entry = JournalEntry.objects.get(id=result.entry_id)
        template = JournalLine.objects.filter(entry=entry).first()
    cols = (
        "id, tenant_id, company_id, entry_id, book_id, period_id, posting_date, line_no, account_id, debit, credit, "
        "amount_currency, currency, fx_rate, party_kind, tax_code, dims_hash, is_rounding, memo, reconcile_key, "
        "aux_abierto_id"
    )
    values = "%s,%s,%s,%s,%s,%s,%s,99,%s,0,0,0,'COP',1,'','','x',false,'','',%s"
    sql = f"INSERT INTO ledger_journal_line ({cols}) VALUES ({values})"

    def insert(node_id):
        with world.scope(), connection.cursor() as cur:
            cur.execute(
                sql,
                [
                    ids.new_id(),
                    world.ctx.tenant_id,
                    world.company,
                    entry.id,
                    entry.book_id,
                    entry.period_id,
                    entry.posting_date,
                    template.account_id,
                    node_id,
                ],
            )

    # Company A line → company B node: the composite FK (company_id, dimension, id) refuses it.
    with pytest.raises(IntegrityError) as exc:
        insert(leaf_b.id)
    assert "ledger_line_aux_fk" in str(exc.value)
    # Parent of another company refused by the node's own composite FK.
    with pytest.raises(IntegrityError) as exc, world.scope(), connection.cursor() as cur:
        cur.execute("UPDATE ledger_dimension_node SET parent_id = %s WHERE id = %s", [group_b.id, leaf_a.id])
    assert "ledger_dim_parent_fk" in str(exc.value)
    # A node of another dimension in the aux slot is refused as well.
    cc = world.node("cost_center", "CC", "DETALLE")
    with pytest.raises(IntegrityError):
        insert(cc.id)


def test_api_crud_tree_archive_move_usage_and_idempotency(api, world):
    c = api()
    key = idem()
    first = c.post(
        "/api/v1/ledger/dimensions/item-contable",
        {"code": "PRE", "description": "Presupuesto", "node_type": "AGRUPADOR"},
        format="json",
        **key,
    )
    replay = c.post(
        "/api/v1/ledger/dimensions/item-contable",
        {"code": "PRE", "description": "Presupuesto", "node_type": "AGRUPADOR"},
        format="json",
        **key,
    )
    assert first.status_code == replay.status_code == 201
    assert replay["Idempotent-Replayed"] == "true" and replay.json()["id"] == first.json()["id"]
    assert (
        c.post(
            "/api/v1/ledger/dimensions/item-contable",
            {"code": "X", "description": "x", "node_type": "DETALLE"},
            format="json",
        ).json()["code"]
        == "idempotency_key_required"
    )
    group = first.json()
    leaf = create(c, "item-contable", "PRE-01", "DETALLE", group["id"]).json()
    other = create(c, "item-contable", "OTRO", "AGRUPADOR").json()

    tree = c.get("/api/v1/ledger/dimensions/item-contable/tree").json()
    assert [n["code"] for n in tree] == ["OTRO", "PRE"] and tree[1]["children"][0]["code"] == "PRE-01"
    listing = c.get("/api/v1/ledger/dimensions/item-contable?q=pre-0").json()
    assert [n["code"] for n in listing["items"]] == ["PRE-01"]

    # PATCH needs If-Match.
    r = c.patch(
        f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}", {"description": "Nueva"}, format="json", **idem()
    )
    assert r.status_code == 428
    etag = c.get(f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}")["ETag"]
    r = c.patch(
        f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}",
        {"description": "Nueva"},
        format="json",
        HTTP_IF_MATCH=etag,
        **idem(),
    )
    assert r.status_code == 200 and r.json()["description"] == "Nueva"

    r = c.post(
        f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}:move",
        {"parameters": {"parent_id": other["id"]}},
        format="json",
        **idem(),
    )
    assert r.status_code == 200 and r.json()["path"] == "OTRO/PRE-01"
    r = c.post(
        f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}:archive", {"reason": "fin"}, format="json", **idem()
    )
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    assert c.get(f"/api/v1/ledger/dimensions/item-contable/{leaf['id']}/usage").json()["references"] == 0
    csv_text = c.get("/api/v1/ledger/dimensions/item-contable:export").content.decode()
    assert "PRE-01" in csv_text and "OTRO" in csv_text

    # Deny by default: a principal without grants gets 403 everywhere.
    nobody = make_context(tenant_id=world.ctx.tenant_id, company_ids=[world.company])
    r = api(ctx=nobody).get("/api/v1/ledger/dimensions/item-contable")
    assert r.status_code == 403 and r.json()["code"] == "policy_denied"
    # Unauthenticated → 401/403 problem.
    assert APIClient().get("/api/v1/ledger/dimensions/item-contable").status_code in (401, 403)
