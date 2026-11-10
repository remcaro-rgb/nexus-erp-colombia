"""FIX-UI-LOGIN-W03: /api/v1/ui/context + /api/v1/ui/modules through the REAL bearer path (in-process LocalIssuer,
no force_authenticate) and the registry navigation-code resolution (UL-2, UL-3, UL-4). Synthetic data only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from iam import ui as iam_ui
from iam.management.commands import dev_ui_principal as preview
from iam.models import RoleAssignment
from iam.oidc.local import LocalIssuer
from iam.policy import nav
from iam.policy.catalog import catalog_codes, module_registry
from iam.policy.engine import PolicyEngine
from iam.policy.patterns import role_allows
from iam.testing.world import World, make_world

from .conftest import Api

pytestmark = pytest.mark.django_db

HR_PATTERNS = ("hr.", "payroll.", "timekeeping.")


def _user(world: World, name: str, role: str, grants: list[str], denies: list[str] | None = None) -> None:
    if role not in world.roles:
        world.add_role(role, grants, denies)
    world.add_user(name, ["ALFA"], subject=str(uuid.uuid4()))
    world.assign(name, role, "ALFA")


def _module_ids(api: Api, **params: Any) -> list[str]:
    r = api.get("/api/v1/ui/modules", **params)
    assert r.status_code == 200, r.content
    return [m["id"] for m in r.json()["modules"]]


# ------------------------------------------------------------------------------------------------ UL-2 /ui/context


def test_FIX_UI_LOGIN_W03_ui_context_bearer_no_token_is_401(world: World) -> None:
    r = Api("").client.get("/api/v1/ui/context")
    assert r.status_code == 401
    assert r["Content-Type"].startswith("application/problem+json")


def test_FIX_UI_LOGIN_W03_ui_context_bearer_valid_token_gets_own_tenant(world: World, login: Any) -> None:
    _user(world, "ana", "preview", preview.ROLE_GRANTS, preview.ROLE_DENIES)
    other = make_world((("GAMMA", "900000003"),))  # another tenant: never visible
    r = login("ana").get("/api/v1/ui/context")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["tenant_id"] == str(world.tenant_id)
    assert body["company_id"] == str(world.companies["ALFA"])
    # only the principal's ACTIVE memberships, allowed fields only
    assert [c["id"] for c in body["companies"]] == [str(world.companies["ALFA"])]
    assert set(body["companies"][0]) == {"id", "legal_name", "nit", "code"}
    assert str(world.companies["BETA"]) not in str(body) and str(other.companies["GAMMA"]) not in str(body)
    assert body["roles"] == ["preview"]
    assert body["user"] == {"display_name": "ana", "email": "ana@example.invalid"}
    assert body["session_idle_minutes"] == 30 and body["feature_flags"] == {}
    perms = set(body["permissions"])
    assert "shell.access" in perms and "ledger.read" in perms and "ledger.journal.read" in perms
    assert not {"ledger.post", "admin.access", "ledger.journal.create"} & perms
    # same list as /iam/me
    me = login("ana").get("/api/v1/iam/me")
    assert me.status_code == 200 and set(me.json()["permissions"]) == perms


def test_FIX_UI_LOGIN_W03_ui_context_bearer_foreign_company_rejected(world: World, login: Any) -> None:
    _user(world, "ana", "preview", preview.ROLE_GRANTS, preview.ROLE_DENIES)
    other = make_world((("GAMMA", "900000003"),))
    api = login("ana")
    for foreign in (other.companies["GAMMA"], world.companies["BETA"]):
        r = api.get("/api/v1/ui/context", company_id=str(foreign))
        assert r.status_code in (404, 422), r.content
        assert str(foreign) not in r.content.decode()
        r = api.get("/api/v1/ui/modules", company_id=str(foreign))
        assert r.status_code in (404, 422), r.content


def test_FIX_UI_LOGIN_W03_ui_context_bearer_portal_token_is_401(world: World, issuer: LocalIssuer) -> None:
    _user(world, "ana", "preview", preview.ROLE_GRANTS, preview.ROLE_DENIES)
    subject = world.users["ana"].idp_subject
    assert subject
    token = issuer.issue(subject, world.tenant_id, "erp-portal")
    for path in ("/api/v1/ui/context", "/api/v1/ui/modules"):
        r = Api(token).get(path)
        assert r.status_code == 401, r.content


# ------------------------------------------------------------------------------------------------ UL-3 /ui/modules


def test_FIX_UI_LOGIN_W03_ui_modules_filtered_read_only_preview_role(world: World, login: Any) -> None:
    _user(world, "ana", "preview", preview.ROLE_GRANTS, preview.ROLE_DENIES)
    api = login("ana")
    r = api.get("/api/v1/ui/modules", company_id=str(world.companies["ALFA"]))
    assert r.status_code == 200, r.content
    body = r.json()
    ids = [m["id"] for m in body["modules"]]
    registry = module_registry()
    assert registry is not None
    assert body["groups"] == registry["groups"]
    assert ids[0] == "inicio" and {"contabilidad", "aprobaciones"} <= set(ids)
    assert "administracion" not in ids  # admin.access needs an IAM admin capability; this role only reads
    assert ids == [m["id"] for m in registry["modules"] if m["id"] in ids]  # registry order
    ledger = next(m for m in body["modules"] if m["id"] == "contabilidad")
    assert ledger["quick_action"] is None  # ledger.post denied: absent, not disabled
    subs = {s["id"] for s in ledger["submenus"]}
    assert {"resumen", "mayor", "comprobantes"} <= subs and "cierres" not in subs  # ledger.close denied
    for kpi in ledger["dashboard"]["kpis"]:
        assert kpi["permission"] in {"ledger.read"}  # ledger.kpi.read has no catalog code yet → absent
    # every remaining entry is one the principal holds
    perms = set(login("ana").get("/api/v1/iam/me").json()["permissions"])
    assert nav.registry_codes(body) <= perms


def test_FIX_UI_LOGIN_W03_ui_modules_filtered_role_matrix_and_revocation(world: World, login: Any) -> None:
    _user(world, "ledger", "ledger_only", ["ledger.*.read"])
    _user(world, "people", "hr_reader", ["hr.*.read", "payroll.*.read"])
    _user(world, "nobody", "no_grants", [])
    ledger_ids = _module_ids(login("ledger"))
    assert ledger_ids == ["inicio", "contabilidad"]
    assert "talento" not in ledger_ids  # payroll hidden for non-HR
    assert "talento" in _module_ids(login("people"))
    assert _module_ids(login("nobody")) == ["inicio"]  # the shell itself; nothing else
    # a revoked grant is effective on the next request
    with world.seed():
        RoleAssignment.objects.filter(tenant_id=world.tenant_id, user=world.users["ledger"]).update(active=False)
        PolicyEngine.bump_version(world.tenant_id)
    assert _module_ids(login("ledger")) == ["inicio"]


def test_FIX_UI_LOGIN_W03_ui_modules_filtered_missing_registry_is_503(
    world: World, login: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _user(world, "ana", "preview", preview.ROLE_GRANTS, preview.ROLE_DENIES)
    monkeypatch.setattr(iam_ui, "module_registry", lambda: None)
    r = login("ana").get("/api/v1/ui/modules")
    assert r.status_code == 503 and r.json()["code"] == "service_unavailable"


# ------------------------------------------------------------------------------------------------ UL-4 navigation codes


def test_FIX_UI_LOGIN_W03_nav_every_registry_code_is_resolvable() -> None:
    registry = module_registry()
    assert registry is not None
    codes = nav.registry_codes(registry)
    assert len(codes) > 40
    modules = {c.split(".")[0] for c in catalog_codes()}
    unresolved = sorted(c for c in codes if nav.rule_for(c, modules) is None)
    assert unresolved == [], unresolved
    # no dead alias rows: every table row is a registry code
    assert set(nav.NAV_ALIASES) <= codes, sorted(set(nav.NAV_ALIASES) - codes)
    # alias patterns only target catalog modules (or a wildcard module)
    for code, patterns in nav.NAV_ALIASES.items():
        for p in patterns:
            assert p.split(".")[0] in modules | {"*", "kernel"}, (code, p)


def test_FIX_UI_LOGIN_W03_nav_rules_deny_by_default() -> None:
    catalog = frozenset(
        {
            "ledger.journal.read",
            "ledger.journal.post",
            "inventory.label_template.read",
            "inventory.issue.read",
            "inventory.issue_concept.read",
        }
    )
    allowed = frozenset({"ledger.journal.read", "inventory.label_template.read", "inventory.issue_concept.read"})

    def r(code: str, shell_ok: bool = True) -> bool:
        return nav.resolve(code, allowed, catalog, shell_ok=shell_ok)

    assert r("shell.access") and not r("shell.access", shell_ok=False)
    assert r("ledger.read") and not r("ledger.post")  # <m>.<a>
    assert r("inventory.label.read")  # resource prefix
    assert not r("inventory.issue.read")  # exact catalog code exists and is not allowed: no prefix fallback
    assert not r("unknown.thing") and not r("ledger.access") and not r("") and not r("a.b.c.d")
    assert not r("hr.access")  # alias without a matching allowed code


def test_FIX_UI_LOGIN_W03_nav_later_modules_appear_with_their_catalog_codes() -> None:
    """Synthetic catalog fixture: a module becomes visible once its catalog codes exist and a role grants them."""
    registry = {
        "groups": ["Inicio", "Operaciones"],
        "modules": [
            {"id": "inicio", "group": "Inicio", "permission": "shell.access", "submenus": [], "quick_action": None},
            {
                "id": "inventario",
                "group": "Operaciones",
                "permission": "inventory.read",
                "submenus": [
                    {"id": "existencias", "permission": "inventory.read"},
                    {
                        "id": "movimientos",
                        "permission": "inventory.move",
                        "tabs": [{"id": "t", "permission": "inventory.move"}],
                    },
                ],
                "quick_action": {"id": "nuevo", "permission": "inventory.write"},
                "dashboard": {
                    "kpis": [
                        {"id": "k1", "permission": "inventory.cost.read"},
                        {"id": "k2", "permission": "inventory.read"},
                    ]
                },
            },
        ],
    }
    grants, denies = list(preview.ROLE_GRANTS), list(preview.ROLE_DENIES)

    def visible(catalog: tuple[str, ...]) -> dict[str, Any]:
        allowed = [c for c in catalog if role_allows(grants, denies, c)]
        perms = frozenset(nav.effective_codes(allowed, catalog, registry, shell_ok=True))
        return nav.filter_registry(registry, perms.__contains__)

    before = visible(("ledger.journal.read",))
    assert [m["id"] for m in before["modules"]] == ["inicio"]
    after = visible(("ledger.journal.read", "inventory.item.read", "inventory.move.read", "inventory.item.create"))
    inv = after["modules"][1]
    assert inv["id"] == "inventario"
    assert [s["id"] for s in inv["submenus"]] == ["existencias", "movimientos"]
    assert inv["quick_action"] is None  # create denied for the read-only role
    assert [k["id"] for k in inv["dashboard"]["kpis"]] == ["k2"]


def test_FIX_UI_LOGIN_W03_nav_catalog_role_templates_matrix() -> None:
    """TASK-MATRIX P04: for each catalog role template, /ui/modules shows exactly the modules it may access."""
    import yaml

    from iam.policy.catalog import CATALOG_PATH, repo_path

    registry = module_registry()
    assert registry is not None
    catalog = catalog_codes()
    roles = (yaml.safe_load(repo_path(CATALOG_PATH).read_text(encoding="utf-8")) or {}).get("roles", [])
    assert roles
    for role in roles:
        allowed = [c for c in catalog if role_allows(role.get("grants", []), role.get("denies", []), c)]
        perms = frozenset(nav.effective_codes(allowed, catalog, registry, shell_ok=True))
        ids = [m["id"] for m in nav.filter_registry(registry, perms.__contains__)["modules"]]
        assert ids[0] == "inicio", role["key"]
        hr = any(c.startswith(HR_PATTERNS) and c.endswith(".read") for c in allowed)
        assert ("talento" in ids) == hr, role["key"]
        reads_ledger = any(c.startswith("ledger.") and c.endswith(".read") for c in allowed)
        assert ("contabilidad" in ids) == reads_ledger, role["key"]
