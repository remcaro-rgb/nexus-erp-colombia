"""F-PORTAL-CORE: T-PORTAL-CORE-01 (lifecycle), -02 (IDOR suite), -03 (internal/external separation)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import URLPattern, URLResolver, clear_url_caches, get_resolver, path

from kernel import clock, ids, tenancy
from kernel.tenancy import PrincipalType, TenantContext

from iam.external import portal as portal_base
from iam.models import ExternalAccount, RoleAssignment, User
from iam.policy.engine import get_engine

from .conftest import Api
from .portal_support import PASSWORD, invite, onboard, token_of

pytestmark = pytest.mark.django_db


def test_T_PORTAL_CORE_01_invite_accept_login_me_and_revoked_denied(world, admin, login, issuer, notifications, audit):
    admin_api = login(admin, "ALFA")
    party = ids.new_id()
    acc = invite(admin_api, "supplier", party, "proveedor.a@example.invalid")
    assert acc["external_status"] == "INVITED" and acc["portal_kind"] == "supplier"
    assert acc["party"] == {"id": str(party), "kind": "vendor"}
    assert "token" not in str(acc) and "nxi." not in str(acc)  # the token only travels to the mail capture
    token = token_of(notifications, acc["email"])
    request = notifications.sent[-1][1]
    assert request.channels == ("email",) and request.company_id == world.companies["ALFA"]

    # before accepting: the realm user exists, but no portal account is linked yet
    issuer.register("prov-a", PASSWORD, world.tenant_id, groups=("portal-supplier",))
    portal = Api(issuer.login("prov-a", PASSWORD, "erp-portal"))
    assert portal.get("/api/portal/v1/me").status_code == 401
    # accept needs a valid invitation token
    assert portal.post("/api/portal/v1/invitations:accept", {"token": "nxi.bad.token"}).status_code == 404
    assert portal.post("/api/portal/v1/invitations:accept", {"token": token}, idem=False).status_code == 400
    r = portal.post("/api/portal/v1/invitations:accept", {"token": token})
    assert r.status_code == 200 and r.json() == {"external_account_id": acc["id"], "external_status": "ACTIVE"}
    # the invitation cannot be used twice
    assert portal.post("/api/portal/v1/invitations:accept", {"token": token}).status_code in (404, 409)

    # login (local OIDC, erp-portal client) → /me returns only the linked party/company
    portal = Api(issuer.login("prov-a", PASSWORD, "erp-portal"))
    me = portal.get("/api/portal/v1/me")
    assert me.status_code == 200, me.content
    assert me.json() == {
        "id": me.json()["id"],
        "tenant_id": str(world.tenant_id),
        "external_account_id": acc["id"],
        "portal_kind": "supplier",
        "display_name": "proveedor.a@example.invalid",
        "email": "proveedor.a@example.invalid",
        "version": me.json()["version"],
    }
    ctx = portal.get("/api/portal/v1/context").json()
    assert ctx["party"] == {"id": str(party), "kind": "vendor"}
    assert ctx["company_name"].startswith("ALFA") and ctx["session_idle_minutes"] == 30
    assert ctx["menu"]["kind"] == "supplier"
    assert str(world.companies["BETA"]) not in str(ctx) and str(world.companies["BETA"]) not in str(me.json())
    detail = admin_api.get(f"/api/v1/iam/external-accounts/{acc['id']}").json()
    assert detail["external_status"] == "ACTIVE" and detail["last_login_at"]

    # suspend → denied; reactivate → allowed; revoke → denied for good
    r = admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:suspend", {"reason": "revisión"})
    assert r.status_code == 200 and r.json()["external_status"] == "SUSPENDED"
    assert portal.get("/api/portal/v1/me").status_code == 401
    assert admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:reactivate").json()["external_status"] == "ACTIVE"
    assert portal.get("/api/portal/v1/me").status_code == 200
    r = admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:revoke", {})
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:revoke", {"reason": "fin de relación"})
    assert r.status_code == 200 and r.json()["external_status"] == "REVOKED"
    assert portal.get("/api/portal/v1/me").status_code == 401
    assert portal.get("/api/portal/v1/context").status_code == 401
    fresh = Api(issuer.login("prov-a", PASSWORD, "erp-portal"))  # even a brand-new token is denied
    assert fresh.get("/api/portal/v1/me").status_code == 401
    r = admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:reactivate")
    assert r.status_code == 409 and r.json()["code"] == "invalid_state_transition"
    assert {e.action for _i, _t, e, _h in audit.records} >= {
        "iam.external_account.invited",
        "iam.external_account.accepted",
        "iam.external_account.revoked",
    }
    with connection.cursor() as cur:
        cur.execute(
            "SELECT envelope->'payload'->>'to_status' FROM kernel_outbox"
            " WHERE event_type = 'iam.external_account.status_changed.v1' ORDER BY created_at"
        )
        assert [r[0] for r in cur.fetchall()] == ["INVITED", "ACTIVE", "SUSPENDED", "ACTIVE", "REVOKED"]


def test_T_PORTAL_CORE_01_invitation_expires_after_72h_and_resend_issues_new_token(
    world, admin, login, issuer, notifications
):
    admin_api = login(admin, "ALFA")
    acc = invite(admin_api, "customer", ids.new_id(), "cliente@example.invalid")
    old = token_of(notifications, acc["email"])
    issuer.register("cli", PASSWORD, world.tenant_id, groups=("portal-customer",))
    portal = Api(issuer.login("cli", PASSWORD, "erp-portal"))
    later = clock.now() + dt.timedelta(hours=72, minutes=1)
    with clock.frozen(later):
        r = portal.post("/api/portal/v1/invitations:accept", {"token": old})
    assert r.status_code == 410 and r.json()["code"] == "invitation_expired"
    r = admin_api.post(f"/api/v1/iam/external-accounts/{acc['id']}:resend")
    assert r.status_code == 200
    new = token_of(notifications, acc["email"])
    assert new != old
    assert portal.post("/api/portal/v1/invitations:accept", {"token": old}).status_code == 404  # superseded
    assert portal.post("/api/portal/v1/invitations:accept", {"token": new}).status_code == 200
    # an internal (erp-web) token cannot accept an invitation
    assert Api(login(admin, "ALFA").token).post("/api/portal/v1/invitations:accept", {"token": new}).status_code == 401


# --------------------------------------------------------------------------- IDOR suite


@pytest.fixture
def probe(world):
    from iam.testing import probe as probe_mod

    with connection.cursor() as cur:
        cur.execute(probe_mod.CREATE_SQL)
    from erp import urls as root_urls

    extra = [
        path("api/portal/v1/supplier/probe-docs", probe_mod.ProbeSupplierDocs.as_view()),
        path("api/portal/v1/supplier/probe-docs/<str:pk>", probe_mod.ProbeSupplierDocs.as_view()),
        path("api/portal/v1/customer/probe-docs", probe_mod.ProbeCustomerDocs.as_view()),
        path("api/portal/v1/customer/probe-docs/<str:pk>", probe_mod.ProbeCustomerDocs.as_view()),
    ]
    root_urls.urlpatterns.extend(extra)
    clear_url_caches()
    yield probe_mod
    for p in extra:
        root_urls.urlpatterns.remove(p)
    clear_url_caches()


def _portal_routes() -> list[str]:
    """Every route under /api/portal/v1/ in the URL conf (auto-covers future portal endpoints)."""
    out: list[str] = []

    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                route = prefix + str(p.pattern)
                if route.startswith("api/portal/v1/"):
                    out.append(route)

    walk(get_resolver().url_patterns)
    return sorted(set(out))


def test_T_PORTAL_CORE_02_idor_suite_party_a_cannot_read_party_b(world, admin, login, issuer, notifications, probe):
    admin_api = login(admin, "ALFA")
    party_a, party_b, party_c = ids.new_id(), ids.new_id(), ids.new_id()
    api_a, acc_a = onboard(world, issuer, admin_api, notifications, "supplier", party_a, "prov-a")
    api_b, acc_b = onboard(world, issuer, admin_api, notifications, "supplier", party_b, "prov-b")
    api_c, acc_c = onboard(world, issuer, admin_api, notifications, "customer", party_c, "cli-c")

    docs: dict[str, list[uuid.UUID]] = {"A": [], "B": [], "C": []}
    with tenancy.scope(world.ctx()):
        for owner, party, n in (("A", party_a, 3), ("B", party_b, 2), ("C", party_c, 1)):
            for i in range(n):
                d = probe.PortalProbeDoc.objects.create(
                    tenant_id=world.tenant_id,
                    company_id=world.companies["ALFA"],
                    party_id=party,
                    number=f"{owner}-{i}",
                    amount=Decimal("100.0000"),
                    internal_cost=Decimal("61.2500"),
                )
                docs[owner].append(d.id)
        # a document of party A's id but in another company must stay invisible too
        foreign = probe.PortalProbeDoc.objects.create(
            tenant_id=world.tenant_id, company_id=world.companies["BETA"], party_id=party_a, number="A-BETA",
            amount=Decimal("1"), internal_cost=Decimal("1"),
        )

    b_secrets = {str(party_b), acc_b["id"], acc_b["email"], *map(str, docs["B"]), str(foreign.id)}

    # list: only own documents, internal cost never returned
    items = api_a.get("/api/portal/v1/supplier/probe-docs").json()["items"]
    assert sorted(i["number"] for i in items) == ["A-0", "A-1", "A-2"]
    assert all("internal_cost" not in i for i in items)
    # search cannot reach other parties' documents
    assert api_a.get("/api/portal/v1/supplier/probe-docs", q="B-").json()["items"] == []

    routes = _portal_routes()
    assert {"api/portal/v1/me", "api/portal/v1/context", "api/portal/v1/supplier/probe-docs/<str:pk>"} <= set(routes)
    checked = 0
    for route in routes:
        if "invitations:accept" in route:
            # identity linking: an already linked token can never take over another invitation
            r = api_a.post("/" + route, {"token": "nxi." + world.tenant_id.hex + ".x", "party_id": str(party_b)})
            assert r.status_code == 404
            continue
        targets = [str(i) for i in docs["B"]] + [str(foreign.id), acc_b["id"], str(party_b)] if "<" in route else [None]
        for target in targets:
            url = "/" + (route.split("<")[0] + target if target else route)
            # 1. URL id of party B → never B's data (404 for detail routes)
            r = api_a.get(url)
            if target:
                assert r.status_code in (403, 404), (url, r.status_code)
            body = r.content.decode().replace(url, "")  # `instance` echoes the caller's own URL
            assert not any(s in body for s in b_secrets), (url, body)
            # 2. ids in the query string → refused
            for name, value in (("party_id", party_b), ("company_id", world.companies["BETA"]),
                                ("external_account_id", acc_b["id"])):
                r = api_a.get(url, **{name: str(value)})
                other_kind = "/customer/" in url or "/employee/" in url
                expected = {"external_account_scope_violation"} | ({"portal_kind_mismatch"} if other_kind else set())
                assert r.status_code == 403 and r.json()["code"] in expected, (url, name, r.json()["code"])
            # 3. ids in the body → refused before any handler logic
            r = api_a.post(url, {"party": {"id": str(party_b)}, "company_id": str(world.companies["BETA"])})
            assert r.status_code in (403, 405), (url, r.status_code)
            if r.status_code == 403:
                assert r.json()["code"] in ("external_account_scope_violation", "portal_kind_mismatch", "policy_denied")
            checked += 1
    assert checked >= 8
    # own ids in the query are fine; another portal kind's endpoints are refused
    assert api_a.get("/api/portal/v1/supplier/probe-docs", party_id=str(party_a)).status_code == 200
    r = api_c.get("/api/portal/v1/supplier/probe-docs")
    assert r.status_code == 403 and r.json()["code"] == "portal_kind_mismatch"
    assert [i["number"] for i in api_c.get("/api/portal/v1/customer/probe-docs").json()["items"]] == ["C-0"]
    assert api_b.get(f"/api/portal/v1/supplier/probe-docs/{docs['A'][0]}").status_code == 404
    assert api_b.get(f"/api/portal/v1/supplier/probe-docs/{docs['B'][0]}").status_code == 200

    # repository layer: scope_queryset applies the token scope even without any view
    me_a = TenantContext(world.tenant_id, frozenset({world.companies["ALFA"]}), uuid.UUID(api_a.get("/api/portal/v1/me").json()["id"]))
    with tenancy.scope(me_a):
        qs = get_engine().scope_queryset(me_a, "portal.supplier_invoice.read", probe.PortalProbeDoc.objects.all())
        assert sorted(qs.values_list("number", flat=True)) == ["A-0", "A-1", "A-2"]
        assert get_engine().scope_queryset(me_a, "sales.invoice.read", probe.PortalProbeDoc.objects.all()).count() == 0
        assert not get_engine().authorize(me_a, "portal.supplier_invoice.read", {"company_id": world.companies["ALFA"], "party_id": party_b}).allowed
        assert get_engine().authorize(me_a, "portal.supplier_invoice.read", {"company_id": world.companies["ALFA"], "party_id": party_a}).allowed
    assert issubclass(probe.ProbeSupplierDocs, portal_base.PortalView) and probe.ProbeSupplierDocs in portal_base.REGISTRY


# --------------------------------------------------------------------------- separation


def test_T_PORTAL_CORE_03_external_users_get_no_internal_roles_and_internal_api_rejects_portal_tokens(
    world, admin, login, issuer, notifications
):
    admin_api = login(admin, "ALFA")
    portal, acc = onboard(world, issuer, admin_api, notifications, "supplier", ids.new_id(), "prov-x")
    with tenancy.scope(world.ctx()):
        ext_user = ExternalAccount.objects.get(pk=acc["id"]).user
    role = world.add_role("contador", ["ledger.*.*", "iam.branch.read"])

    # internal role assignment to the external user (by user id or by account id) is refused
    for target in (ext_user.id, acc["id"]):
        r = admin_api.post("/api/v1/iam/role-assignments", {"user_id": str(target), "role_id": str(role.id)})
        assert r.status_code == 403 and r.json()["code"] == "external_role_forbidden", r.content
    # …also at the database level (trigger), even bypassing the service
    with tenancy.scope(world.ctx(["ALFA"])), pytest.raises(DatabaseError, match="internal users"), transaction.atomic():
        RoleAssignment.objects.create(
            tenant_id=world.tenant_id, company_id=world.companies["ALFA"], user=ext_user, role=role
        )
    # an internal user can receive it
    world.add_user("ana", ["ALFA"], subject=str(uuid.uuid4()))
    r = admin_api.post("/api/v1/iam/role-assignments", {"user_id": str(world.users["ana"].id), "role_id": str(role.id)})
    assert r.status_code == 201, r.content

    # the internal API rejects portal tokens on every internal endpoint
    for url in ("/api/v1/iam/me", "/api/v1/iam/branches", "/api/v1/iam/external-accounts", "/api/v1/iam/role-assignments"):
        r = portal.get(url)
        assert r.status_code == 401 and r.json()["code"] == "unauthenticated", url
    # a token for the external subject minted for an internal client is still refused (user kind EXTERNAL)
    forged = Api(issuer.issue(ext_user.idp_subject, world.tenant_id, "erp-web"))
    assert forged.get("/api/v1/iam/me").status_code == 401
    # internal tokens are refused by the portal API, and portal tokens are not valid for other tenants
    assert login(admin, "ALFA").get("/api/portal/v1/me").status_code == 401
    other_tenant = Api(issuer.issue(ext_user.idp_subject, uuid.uuid4(), "erp-portal"))
    assert other_tenant.get("/api/portal/v1/me").status_code == 401
    # the realm login itself keeps portal users away from internal clients
    with pytest.raises(Exception, match="client not allowed"):
        issuer.login("prov-x", PASSWORD, "erp-web")
    # no policy path grants an internal permission to the external principal
    with tenancy.scope(world.ctx()):
        ctx = TenantContext(world.tenant_id, frozenset({world.companies["ALFA"]}), ext_user.id, PrincipalType.USER)
        for code in ("iam.branch.read", "ledger.journal.read", "purchasing.order.read", "portal.customer_invoice.read"):
            assert not get_engine().authorize(ctx, code).allowed, code
        assert get_engine().field_mask(ctx, "iam.branch").readable == frozenset()
        assert User.objects.get(pk=ext_user.id).kind == User.Kind.EXTERNAL
