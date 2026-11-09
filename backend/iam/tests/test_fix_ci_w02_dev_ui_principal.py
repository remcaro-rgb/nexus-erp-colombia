"""FIX-CI-W02 UI-3: ``manage.py dev_ui_principal`` (DEV-only synthetic UI-preview principal)."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from iam.api.views import catalog_codes
from iam.management.commands import dev_ui_principal as cmd
from iam.oidc.local import LocalIssuer
from kernel.testing.tenancy import bound, make_context

pytestmark = pytest.mark.django_db


def _run(settings: Any, subject: str, tenant: uuid.UUID, username: str = "ui-preview") -> str:
    settings.ERP_ENVIRONMENT = "dev"
    out = io.StringIO()
    call_command(
        "dev_ui_principal", "--subject", subject, "--username", username, "--tenant-id", str(tenant), stdout=out
    )
    return out.getvalue()


def _rows(tenant: uuid.UUID) -> dict[str, list[Any]]:
    from iam.models import Company, Membership, Role, RoleAssignment, Tenant, User

    with bound(make_context(tenant_id=tenant, company_ids=[cmd.company_id_for(tenant)])):
        return {
            "tenant": list(Tenant.objects.filter(id=tenant).values("name", "status", "version")),
            "company": list(
                Company.objects.filter(tenant_id=tenant).values("id", "nit", "dv", "legal_name", "version")
            ),
            "user": list(
                User.objects.filter(tenant_id=tenant).values("id", "idp_subject", "kind", "status", "version")
            ),
            "membership": list(
                Membership.objects.filter(tenant_id=tenant).values("company_id", "user_id", "is_default")
            ),
            "role": list(Role.objects.filter(tenant_id=tenant).values("key", "grants", "denies", "version")),
            "assignment": list(RoleAssignment.objects.filter(tenant_id=tenant).values("role__key", "active", "scope")),
        }


def test_FIX_CI_W02_dev_ui_principal_refuses_outside_dev(settings: Any) -> None:
    settings.ERP_ENVIRONMENT = "test"
    with pytest.raises(CommandError) as exc:
        call_command("dev_ui_principal", "--subject", "s", "--username", "u", "--tenant-id", str(uuid.uuid4()))
    assert exc.value.returncode == 2 and "DEV-only" in str(exc.value)


def test_FIX_CI_W02_dev_ui_principal_is_idempotent_and_binds_the_subject(settings: Any) -> None:
    tenant, subject = uuid.uuid4(), str(uuid.uuid4())
    first = _run(settings, subject, tenant)
    rows = _rows(tenant)
    second = _run(settings, subject, tenant)
    assert _rows(tenant) == rows  # nothing changed on the second run (versions included)
    assert first == second and first.count("\n") == 1
    company, user = rows["company"][0], rows["user"][0]
    assert first.strip() == (
        f"DEV-UI-PRINCIPAL OK tenant={tenant} company={company['id']} user={user['id']} "
        f"permissions={len(cmd.granted_codes(catalog_codes(), cmd.ROLE_GRANTS, cmd.ROLE_DENIES))}"
    )
    assert rows["tenant"][0]["name"] == "Vista previa UI (sintético)"
    assert company["nit"] == "900000099" and "sintética" in company["legal_name"]
    from localization_co.nit import validate

    assert validate(company["nit"], company["dv"]).valid
    assert user["idp_subject"] == subject and (user["kind"], user["status"]) == ("INTERNAL", "ACTIVE")
    assert rows["membership"] == [{"company_id": company["id"], "user_id": user["id"], "is_default": True}]
    assert rows["assignment"] == [{"role__key": "ui-preview-readonly", "active": True, "scope": {}}]


def test_FIX_CI_W02_dev_ui_principal_grants_only_read_codes(settings: Any) -> None:
    codes = catalog_codes()
    assert codes, "contracts/permissions/catalog.yaml must be readable in the repository"
    granted = cmd.granted_codes(codes, cmd.ROLE_GRANTS, cmd.ROLE_DENIES)
    assert granted and all(c.rsplit(".", 1)[1] == "read" for c in granted)
    assert set(granted) == {c for c in codes if c.endswith(".read")}
    for action in ("create", "update", "delete", "approve", "post", "void", "export", "admin"):
        assert not cmd.granted_codes(
            (f"ledger.journal_entry.{action}", f"iam.role.{action}"), cmd.ROLE_GRANTS, cmd.ROLE_DENIES
        )
    tenant = uuid.uuid4()
    out = _run(settings, str(uuid.uuid4()), tenant)
    assert out.strip().endswith(f"permissions={len(granted)}")
    role = _rows(tenant)["role"][0]
    assert (role["grants"], role["denies"]) == (cmd.ROLE_GRANTS, cmd.ROLE_DENIES)


def test_FIX_CI_W02_dev_ui_principal_request_gets_synthetic_tenant_context(settings: Any, issuer: LocalIssuer) -> None:
    from iam.policy.engine import get_engine
    from kernel import services
    from kernel.interfaces import PolicyService

    tenant, subject = uuid.uuid4(), str(uuid.uuid4())
    _run(settings, subject, tenant)
    client = APIClient()
    token = issuer.issue(subject, tenant, "erp-web")
    with services.override(PolicyService, get_engine()):  # the real IAM policy: normal checks, no bypass
        r = client.get("/api/v1/iam/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["tenant_id"] == str(tenant)
        assert str(cmd.company_id_for(tenant)) in str(body)
        perms = set(body.get("permissions") or [])
        assert perms and all(p.endswith(".read") for p in perms)
        # a write is denied by the normal checks (deny wins over the read grant)
        w = client.post(
            "/api/v1/iam/roles",
            {"key": "x", "name": "x", "grants": ["*.*.*"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert w.status_code == 403, w.content
