"""Helpers shared by the portal security tests."""

from __future__ import annotations

import uuid
from typing import Any

from iam.oidc.local import LocalIssuer
from iam.testing.world import World

from .conftest import Api

PASSWORD = "dev-only-" + uuid.uuid4().hex  # random per run, never printed


def invite(admin_api: Api, kind: str, party_id: uuid.UUID, email: str) -> dict[str, Any]:
    body: dict[str, Any] = {"email": email, "portal_kind": kind, "party": {"id": str(party_id)}}
    if kind == "employee":
        body["employee_id"] = str(party_id)
    r = admin_api.post("/api/v1/iam/external-accounts", body)
    assert r.status_code == 201, r.content
    return r.json()


def token_of(notifications: Any, account_email: str) -> str:
    del account_email
    _nid, request = notifications.sent[-1]
    assert request.template_code == "iam.portal_invitation"
    return str(request.data["invitation_token"])


def onboard(
    world: World, issuer: LocalIssuer, admin_api: Api, notifications: Any, kind: str, party_id: uuid.UUID, username: str
) -> tuple[Api, dict[str, Any]]:
    """Invite → realm user sets password → login (erp-portal) → accept; returns the portal API."""
    acc = invite(admin_api, kind, party_id, f"{username}@example.invalid")
    token = token_of(notifications, acc["email"])
    issuer.register(username, PASSWORD, world.tenant_id, groups=(f"portal-{kind}",))
    portal = Api(issuer.login(username, PASSWORD, "erp-portal"))
    r = portal.post("/api/portal/v1/invitations:accept", {"token": token})
    assert r.status_code == 200, r.content
    assert r.json()["external_status"] == "ACTIVE"
    return portal, acc
