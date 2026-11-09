"""P04 security suite (T-P04-01/02/03/13/14/15/16 + row policies): RLS, BOLA/BFLA, field security, revocation."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection, transaction

from iam import rls
from iam.models import Branch, FieldPolicy, RowPolicy
from iam.oidc import jwt
from iam.policy import rules
from iam.policy.engine import PolicyEngine, get_engine
from iam.policy.fields import apply_mask
from iam.testing.world import make_world
from kernel import tenancy
from kernel.tenancy import PrincipalType, TenantContext

pytestmark = pytest.mark.django_db


def _branch(world, company, code, head=True):
    with tenancy.scope(world.ctx()):
        return Branch.objects.create(
            tenant_id=world.tenant_id, company_id=world.companies[company], code=code, name=code, is_head_office=head
        )


def test_T_P04_01_rls_enabled_and_forced_on_every_iam_tenant_table():
    assert rls.tables_without_rls("iam_") == []
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cur.fetchone() == (False, False)  # the app role is subject to RLS


def test_T_P04_02_cross_tenant_rows_blocked_by_the_database_even_without_app_filters(world, monkeypatch):
    other = make_world((("GAMMA", "900000003"),))
    _branch(world, "ALFA", "A1")
    _branch(other, "GAMMA", "G1")
    # the application filter is removed: scope_queryset becomes a no-op
    monkeypatch.setattr(PolicyEngine, "scope_queryset", lambda self, ctx, action, qs: qs)
    with tenancy.scope(world.ctx()):
        assert list(
            get_engine()
            .scope_queryset(world.ctx(), "iam.branch.read", Branch.objects.all())
            .values_list("code", flat=True)
        ) == ["A1"]
        with connection.cursor() as cur:
            cur.execute("SELECT code FROM iam_branch")
            assert [r[0] for r in cur.fetchall()] == ["A1"]
            # writes into another tenant are rejected by WITH CHECK
            with pytest.raises(Exception, match="row-level security"), transaction.atomic(), connection.cursor() as c2:
                c2.execute(
                    "INSERT INTO iam_branch (id, tenant_id, company_id, code, name, address, is_head_office, status,"
                    " valid_from, created_at, updated_at, version) VALUES (%s, %s, %s, 'X', 'X', '', false, 'ACTIVE',"
                    " current_date, now(), now(), 1)",
                    [str(uuid.uuid4()), str(other.tenant_id), str(other.companies["GAMMA"])],
                )
    # company scoping inside one tenant: ALFA-only context cannot see BETA rows
    _branch(world, "BETA", "B1")
    with tenancy.scope(world.ctx(["ALFA"])):
        assert list(Branch.objects.values_list("code", flat=True)) == ["A1"]


def test_T_P04_03_unbound_session_sees_zero_rows(world):
    _branch(world, "ALFA", "A1")
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '', true), set_config('app.company_ids', '', true)")
        for table in ("iam_branch", "iam_company", "iam_user", "iam_role", "iam_tenant", "iam_external_account"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table


def test_T_P04_13_14_15_field_masks_filters_bola_and_bfla(world, admin, login):
    world.add_role("lector", ["iam.branch.read"], max_cls="internal")
    world.add_user("lia", ["ALFA"], subject="sub-lia")
    world.assign("lia", "lector", "ALFA")
    admin_api = login(admin, "ALFA")
    url = f"/api/v1/iam/companies/{world.companies['ALFA']}/branches"
    created = admin_api.post(url, {"code": "BOG", "name": "Bogotá", "is_head_office": True, "phone": "+57 601 1234567"})
    assert created.status_code == 201
    lia = login("lia", "ALFA")

    # field policy: phone masked (last4) for the reader role, address hidden
    with world.seed():
        FieldPolicy.objects.create(
            tenant_id=world.tenant_id,
            role=world.roles["lector"],
            resource="iam.branch",
            field="phone",
            readable=True,
            mask="last4",
        )
        FieldPolicy.objects.create(
            tenant_id=world.tenant_id,
            role=world.roles["lector"],
            resource="iam.branch",
            field="address",
            readable=False,
        )
        PolicyEngine.bump_version(world.tenant_id)
    item = lia.get("/api/v1/iam/branches").json()["items"][0]
    assert item["phone"].endswith("4567") and item["phone"].startswith("*") and "address" not in item
    assert admin_api.get("/api/v1/iam/branches").json()["items"][0]["phone"] == "+57 601 1234567"
    # T-P04-13: filter/sort on a masked or hidden field is rejected (no inference)
    r = lia.get("/api/v1/iam/branches", sort="code")
    assert r.status_code == 200
    with world.seed():
        FieldPolicy.objects.create(
            tenant_id=world.tenant_id,
            role=world.roles["lector"],
            resource="iam.branch",
            field="name",
            readable=True,
            mask="full",
        )
        PolicyEngine.bump_version(world.tenant_id)
    assert lia.get("/api/v1/iam/branches", sort="name").json()["code"] == "filter_not_allowed"
    assert lia.get("/api/v1/iam/branches", **{"filter[name][eq]": "Bogotá"}).json()["code"] == "filter_not_allowed"
    assert lia.get("/api/v1/iam/branches", q="Bogo").json()["items"] == []  # masked field is not searchable

    # T-P04-15 BFLA: action endpoints without the permission → 403
    bid = created.json()["id"]
    for method, path, body in (
        ("post", url, {"code": "X", "name": "X"}),
        ("post", f"/api/v1/iam/branches/{bid}:archive", {}),
        (
            "post",
            "/api/v1/iam/external-accounts",
            {"email": "a@example.invalid", "portal_kind": "supplier", "party": {"id": str(uuid.uuid4())}},
        ),
        ("post", "/api/v1/iam/role-assignments", {"user_id": str(uuid.uuid4()), "role_id": str(uuid.uuid4())}),
    ):
        r = getattr(lia, method)(path, body)
        assert r.status_code == 403 and r.json()["code"] == "policy_denied", (path, r.content)
    # T-P04-14 BOLA: foreign-tenant / foreign-company ids → 404, never 403
    other = make_world((("GAMMA", "900000003"),))
    foreign = _branch(other, "GAMMA", "G1")
    beta = _branch(world, "BETA", "B1")
    for pk in (foreign.id, beta.id, uuid.uuid4(), "not-a-uuid"):
        assert admin_api.get(f"/api/v1/iam/branches/{pk}").status_code == 404
        assert admin_api.post(f"/api/v1/iam/branches/{pk}:archive").status_code == 404
    # X-Company-Id outside the memberships → company_not_allowed
    r = login("lia", "BETA").get("/api/v1/iam/branches")
    assert r.status_code == 403 and r.json()["code"] == "company_not_allowed"


def test_T_P04_16_revocation_takes_effect_on_the_next_request(world, admin, login):
    world.add_role("lector", ["iam.branch.read"])
    world.add_user("lia", ["ALFA"], subject="sub-lia")
    a = world.assign("lia", "lector", "ALFA")
    lia = login("lia", "ALFA")
    assert lia.get("/api/v1/iam/branches").status_code == 200  # cached allow now present
    with world.seed():
        type(a).objects.filter(pk=a.pk).update(active=False)
        PolicyEngine.bump_version(world.tenant_id)
    assert lia.get("/api/v1/iam/branches").status_code == 403


def test_T_P04_05_row_policy_own_records_and_deny_overrides(world):
    world.add_role("vendedor", ["iam.branch.read"])
    world.add_user("vic", ["ALFA"], subject="sub-vic")
    world.assign("vic", "vendedor", "ALFA")
    mine, other = _branch(world, "ALFA", "MIA"), _branch(world, "ALFA", "OTRA", head=False)
    with world.seed():
        Branch.objects.filter(pk=mine.pk).update(created_by=world.users["vic"].id)
        RowPolicy.objects.create(
            tenant_id=world.tenant_id,
            role=world.roles["vendedor"],
            resource="iam.branch",
            rule={
                "resource": "iam.branch",
                "effect": "allow",
                "actions": ["read"],
                "where": {"all": [{"attr": "created_by", "op": "eq", "value": "$principal.user_id"}]},
            },
        )
        PolicyEngine.bump_version(world.tenant_id)
    ctx = TenantContext(
        world.tenant_id, frozenset({world.companies["ALFA"]}), world.users["vic"].id, PrincipalType.USER
    )
    with tenancy.scope(ctx):
        qs = get_engine().scope_queryset(ctx, "iam.branch.read", Branch.objects.all())
        assert list(qs.values_list("code", flat=True)) == ["MIA"]
        mine = Branch.objects.get(pk=mine.pk)
        assert get_engine().authorize(ctx, "iam.branch.read", mine).allowed
        assert not get_engine().authorize(ctx, "iam.branch.read", other).allowed
    with world.seed():
        RowPolicy.objects.create(
            tenant_id=world.tenant_id,
            role=None,
            resource="iam.branch",
            rule={
                "resource": "iam.branch",
                "effect": "deny",
                "actions": ["read"],
                "where": {"all": [{"attr": "code", "op": "eq", "value": "MIA"}]},
            },
        )
        PolicyEngine.bump_version(world.tenant_id)
    with tenancy.scope(ctx):
        assert get_engine().scope_queryset(ctx, "iam.branch.read", Branch.objects.all()).count() == 0
        assert not get_engine().authorize(ctx, "iam.branch.read", mine).allowed
    # delegated (AI/MCP) principals never exceed the user and are read-only
    agent = TenantContext(
        world.tenant_id, ctx.company_ids, uuid.uuid4(), PrincipalType.AI_AGENT, on_behalf_of=world.users["vic"].id
    )
    assert not get_engine().authorize(agent, "iam.branch.update").allowed
    assert not get_engine().authorize(agent, "iam.role.read").allowed
    # malformed rules are refused
    with pytest.raises(Exception):
        rules.validate({"effect": "maybe", "actions": ["read"]})
    with pytest.raises(Exception):
        rules.validate({"effect": "allow", "actions": ["read"], "where": {"attr": "x;drop", "op": "eq"}})


def test_masks_and_jwt_rejections():
    assert apply_mask("last4", "1234567890") == "******7890"
    assert apply_mask("range", "3500000.00") == "3M–4M"
    assert apply_mask("hash", "x").startswith("sha256:") and apply_mask("full", "x") == "****"
    key = jwt.generate_key()
    keys = {key.kid: key.public()}
    token = jwt.encode({"sub": "s"}, key)
    head, body, sig = token.split(".")
    for bad in (
        jwt.b64url_encode(b'{"alg":"none","kid":"%s"}' % key.kid.encode()) + "." + body + ".",
        jwt.b64url_encode(b'{"alg":"HS256","kid":"%s"}' % key.kid.encode()) + "." + body + "." + sig,
        head + "." + jwt.b64url_encode(b'{"sub":"admin"}') + "." + sig,
        "garbage",
    ):
        with pytest.raises(jwt.JWTError):
            jwt.decode(bad, keys)
    assert jwt.decode(token, keys) == {"sub": "s"}
    with pytest.raises(jwt.JWTError):
        jwt.RSAPublicKey.from_jwk({**key.public().to_jwk(), "n": jwt.b64url_encode(b"\x01" * 64)})
