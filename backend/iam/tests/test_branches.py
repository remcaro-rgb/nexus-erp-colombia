"""F-ACC-BRANCH: T-ACC-BRANCH-01 (CRUD, uniqueness, head office), T-ACC-BRANCH-02 (links, archive)."""

from __future__ import annotations

import uuid

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from iam.branches import db as branch_db
from iam.branches import services as branch_services
from iam.models import Branch
from kernel import ids, tenancy

pytestmark = pytest.mark.django_db


def _url(world, company: str) -> str:
    return f"/api/v1/iam/companies/{world.companies[company]}/branches"


def _create(api, world, company, code, head=False, **extra):
    return api.post(_url(world, company), {"code": code, "name": f"Sucursal {code}", "is_head_office": head, **extra})


def test_T_ACC_BRANCH_01_crud_code_unique_per_company_and_one_head_office(world, admin, login, audit):
    alfa = login(admin, "ALFA")
    beta = login(admin, "BETA")

    # first branch must be the head office
    r = _create(alfa, world, "ALFA", "BOG")
    assert r.status_code == 422 and r.json()["code"] == "branch_head_office_required"
    r = _create(alfa, world, "ALFA", "BOG", head=True, municipality_code="11001", phone="+57 601 0000000")
    assert r.status_code == 201, r.content
    bog = r.json()
    assert (
        bog["is_head_office"] is True
        and bog["status"] == "ACTIVE"
        and bog["company_id"] == str(world.companies["ALFA"])
    )
    assert r["Location"] == f"/api/v1/iam/branches/{bog['id']}"

    # code unique per company, case-insensitive
    r = _create(alfa, world, "ALFA", "bog")
    assert r.status_code == 422 and r.json()["code"] == "branch_code_duplicate"
    # same code in another company is allowed
    r = _create(beta, world, "BETA", "BOG", head=True)
    assert r.status_code == 201, r.content
    beta_bog = r.json()

    # second branch; then promote it: the previous head office is demoted (exactly one)
    med = _create(alfa, world, "ALFA", "MED").json()
    assert med["is_head_office"] is False
    detail = alfa.get(f"/api/v1/iam/branches/{med['id']}")
    assert detail.status_code == 200
    r = alfa.patch(
        f"/api/v1/iam/branches/{med['id']}", {"is_head_office": True, "name": "Medellín"}, HTTP_IF_MATCH=detail["ETag"]
    )
    assert r.status_code == 200, r.content
    assert r.json()["is_head_office"] is True and r.json()["name"] == "Medellín"
    with tenancy.scope(world.ctx(["ALFA"])):
        heads = list(
            Branch.objects.filter(company_id=world.companies["ALFA"], is_head_office=True).values_list(
                "code", flat=True
            )
        )
    assert heads == ["MED"]

    # un-flagging the only head office is refused
    detail = alfa.get(f"/api/v1/iam/branches/{med['id']}")
    r = alfa.patch(f"/api/v1/iam/branches/{med['id']}", {"is_head_office": False}, HTTP_IF_MATCH=detail["ETag"])
    assert r.status_code == 422 and r.json()["code"] == "branch_head_office_required"

    # PATCH needs If-Match; a stale ETag is refused
    r = alfa.patch(f"/api/v1/iam/branches/{bog['id']}", {"name": "x"})
    assert r.status_code == 428
    r = alfa.patch(f"/api/v1/iam/branches/{bog['id']}", {"name": "x"}, HTTP_IF_MATCH='W/"stale"')
    assert r.status_code == 412
    # code change to a taken code
    detail = alfa.get(f"/api/v1/iam/branches/{bog['id']}")
    r = alfa.patch(f"/api/v1/iam/branches/{bog['id']}", {"code": "MED"}, HTTP_IF_MATCH=detail["ETag"])
    assert r.status_code == 422 and r.json()["code"] == "branch_code_duplicate"

    # list / search / filter / sort, scoped to the active company
    r = alfa.get("/api/v1/iam/branches", sort="code")
    assert [b["code"] for b in r.json()["items"]] == ["BOG", "MED"]
    assert alfa.get("/api/v1/iam/branches", q="medell").json()["items"][0]["code"] == "MED"
    assert (
        alfa.get("/api/v1/iam/branches", **{"filter[is_head_office][eq]": "true"}).json()["items"][0]["code"] == "MED"
    )
    assert alfa.get("/api/v1/iam/branches", **{"filter[nope][eq]": "1"}).status_code == 400
    assert [b["code"] for b in beta.get("/api/v1/iam/branches").json()["items"]] == ["BOG"]
    # other company's branch → 404 (no enumeration)
    assert alfa.get(f"/api/v1/iam/branches/{beta_bog['id']}").status_code == 404
    assert alfa.get(f"/api/v1/iam/companies/{world.companies['BETA']}/branches").status_code == 404

    # archive / unarchive (archiving the head office while others are active is refused)
    r = alfa.post(f"/api/v1/iam/branches/{med['id']}:archive")
    assert r.status_code == 422 and r.json()["code"] == "branch_head_office_required"
    r = alfa.post(f"/api/v1/iam/branches/{bog['id']}:archive")
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    r = alfa.post(f"/api/v1/iam/branches/{bog['id']}:unarchive")
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE" and r.json()["is_head_office"] is False

    # Idempotency-Key is required and replays the stored response
    r = alfa.post(_url(world, "ALFA"), {"code": "CAL", "name": "Cali"}, idem=False)
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"
    key = str(uuid.uuid4())
    first = alfa.post(_url(world, "ALFA"), {"code": "CAL", "name": "Cali"}, idem=False, HTTP_IDEMPOTENCY_KEY=key)
    again = alfa.post(_url(world, "ALFA"), {"code": "CAL", "name": "Cali"}, idem=False, HTTP_IDEMPOTENCY_KEY=key)
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]

    # unknown fields and invalid DANE code are rejected
    assert _create(alfa, world, "ALFA", "X1", foo=1).status_code == 422
    assert _create(alfa, world, "ALFA", "X2", municipality_code="ABC").status_code == 422

    # every change audited; events emitted through the outbox
    assert any(e.action == "iam.branch.created" for _i, _t, e, _h in audit.records)
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM kernel_outbox WHERE event_type = 'iam.branch.changed.v1'")
        assert cur.fetchone()[0] >= 6


def test_T_ACC_BRANCH_01_db_enforces_uniqueness_and_exactly_one_head_office(world):
    ctx = world.ctx(["ALFA"])
    company = world.companies["ALFA"]
    with tenancy.scope(ctx):
        Branch.objects.create(
            tenant_id=world.tenant_id, company_id=company, code="BOG", name="Bogotá", is_head_office=True
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Branch.objects.create(tenant_id=world.tenant_id, company_id=company, code="Bog", name="dup")
        with pytest.raises(IntegrityError), transaction.atomic():
            Branch.objects.create(
                tenant_id=world.tenant_id, company_id=company, code="MED", name="2nd head", is_head_office=True
            )
        # deferred constraint trigger: a company with active branches but no head office cannot commit
        with pytest.raises(DatabaseError, match="exactly one active head office"), transaction.atomic():
            Branch.objects.filter(company_id=company).update(is_head_office=False)
            with connection.cursor() as cur:
                cur.execute("SET CONSTRAINTS iam_branch_head_office_trg IMMEDIATE")
        # company_id is immutable
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            Branch.objects.filter(company_id=company).update(company_id=world.companies["BETA"])


@pytest.fixture
def warehouse_table(world):
    """A stand-in for P09's warehouse table, wired exactly as P09 must wire it."""
    forward, _reverse = branch_db.branch_fk_sql("p04probe_warehouse")
    with connection.cursor() as cur:
        cur.execute(
            "CREATE TABLE p04probe_warehouse (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL,"
            " branch_id uuid NOT NULL, code varchar(40) NOT NULL, status varchar(16) NOT NULL DEFAULT 'ACTIVE')"
        )
        cur.execute(forward)
    branch_services.register_reference(
        branch_services.BranchReference(
            branch_services.WAREHOUSE_REFERENCE, "p04probe_warehouse", active_sql="status = 'ACTIVE'"
        )
    )
    branch_services.register_reference(
        branch_services.BranchReference(branch_services.POS_REFERENCE, "p04probe_warehouse", active_sql="FALSE")
    )
    yield "p04probe_warehouse"
    branch_services.unregister_reference(branch_services.WAREHOUSE_REFERENCE)
    branch_services.unregister_reference(branch_services.POS_REFERENCE)


def _insert_wh(world, company, branch_id, code="WH1"):
    wid = ids.new_id()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO p04probe_warehouse (id, tenant_id, company_id, branch_id, code) VALUES (%s, %s, %s, %s, %s)",
            [str(wid), str(world.tenant_id), str(world.companies[company]), str(branch_id), code],
        )
    return wid


def test_T_ACC_BRANCH_02_cross_company_links_rejected_and_archive_blocked_while_referenced(
    world, admin, login, warehouse_table
):
    alfa, beta = login(admin, "ALFA"), login(admin, "BETA")
    a_head = _create(alfa, world, "ALFA", "BOG", head=True).json()
    a_med = _create(alfa, world, "ALFA", "MED").json()
    b_head = _create(beta, world, "BETA", "BOG", head=True).json()

    # DB composite FK: a warehouse of BETA cannot point to an ALFA branch
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert_wh(world, "BETA", a_med["id"], "BAD")
    wh_alfa = _insert_wh(world, "ALFA", a_med["id"], "WH-MED")
    wh_beta = _insert_wh(world, "BETA", b_head["id"], "WH-B")

    # API: default_warehouse_id of another company is rejected; same company accepted
    detail = alfa.get(f"/api/v1/iam/branches/{a_med['id']}")
    r = alfa.patch(
        f"/api/v1/iam/branches/{a_med['id']}", {"default_warehouse_id": str(wh_beta)}, HTTP_IF_MATCH=detail["ETag"]
    )
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company"
    r = alfa.patch(
        f"/api/v1/iam/branches/{a_med['id']}", {"default_warehouse_id": str(wh_alfa)}, HTTP_IF_MATCH=detail["ETag"]
    )
    assert r.status_code == 200 and r.json()["default_warehouse_id"] == str(wh_alfa)
    # create with a foreign warehouse is rejected too
    r = _create(alfa, world, "ALFA", "CAL", default_warehouse_id=str(wh_beta))
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company"

    # service used by P09/P12 APIs: link to another company's branch is rejected
    with tenancy.scope(world.ctx()):
        with pytest.raises(Exception) as exc:
            branch_services.validate_branch_link(world.ctx(["ALFA"]), world.companies["ALFA"], uuid.UUID(b_head["id"]))
        assert getattr(exc.value, "code", "") == "branch_cross_company"
        assert (
            branch_services.validate_branch_link(
                world.ctx(["ALFA"]), world.companies["ALFA"], uuid.UUID(a_med["id"])
            ).code
            == "MED"
        )

    # archive blocked while an ACTIVE warehouse references the branch, with the dependency list
    r = alfa.post(f"/api/v1/iam/branches/{a_med['id']}:archive")
    assert r.status_code == 409 and r.json()["code"] == "branch_in_use"
    deps = r.json()["errors"]["dependencies"]
    assert deps == [{"type": "inventory.warehouse", "id": str(wh_alfa), "label": "WH-MED"}]
    with connection.cursor() as cur:
        cur.execute("UPDATE p04probe_warehouse SET status = 'ARCHIVED' WHERE id = %s", [str(wh_alfa)])
    r = alfa.post(f"/api/v1/iam/branches/{a_med['id']}:archive")
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    # posting guard refuses archived / other-company branches
    with tenancy.scope(world.ctx()):
        for company, branch, code in (
            ("ALFA", a_med["id"], "BRANCH_ARCHIVED"),
            ("ALFA", b_head["id"], "BRANCH_COMPANY_MISMATCH"),
        ):
            with pytest.raises(Exception) as exc:
                branch_services.assert_postable_branch(world.ctx(), world.companies[company], uuid.UUID(branch))
            assert exc.value.code == code
        branch_services.assert_postable_branch(world.ctx(), world.companies["ALFA"], uuid.UUID(a_head["id"]))
