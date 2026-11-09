"""Fixtures for IAM tests: synthetic world, local OIDC issuer, API client helpers."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from kernel import services
from kernel.interfaces.audit import AuditService
from kernel.interfaces.notifications import NotificationService
from kernel.testing.fakes import FakeAuditService, FakeNotificationService

from iam.oidc import verifier
from iam.oidc.local import LocalIssuer
from iam.testing.world import ADMIN_GRANTS, World, make_world

ISSUER = "http://127.0.0.1:8080/auth/realms/erp-dev"


@pytest.fixture(scope="session")
def _issuer_session() -> LocalIssuer:
    return LocalIssuer(ISSUER)


@pytest.fixture
def issuer(_issuer_session: LocalIssuer) -> Iterator[LocalIssuer]:
    verifier.set_key_provider(_issuer_session, ISSUER)
    try:
        yield _issuer_session
    finally:
        verifier.set_key_provider(None)


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def notifications() -> Iterator[FakeNotificationService]:
    fake = FakeNotificationService()
    with services.override(NotificationService, fake):
        yield fake


@pytest.fixture
def audit() -> Iterator[FakeAuditService]:
    fake = FakeAuditService()
    with services.override(AuditService, fake):
        yield fake


@pytest.fixture
def world(db: Any) -> World:
    return make_world()


class Api:
    """Thin wrapper over DRF's APIClient that adds bearer/company/idempotency headers."""

    def __init__(self, token: str, company: uuid.UUID | None = None) -> None:
        self.client = APIClient()
        self.token = token
        self.company = company

    def _headers(self, idem: bool, extra: dict[str, str]) -> dict[str, str]:
        h = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        if self.company:
            h["HTTP_X_COMPANY_ID"] = str(self.company)
        if idem:
            h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
        h.update(extra)
        return h

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(url, params, **self._headers(False, {}))

    def post(self, url: str, data: Any = None, idem: bool = True, **extra: str) -> Any:
        return self.client.post(url, data or {}, format="json", **self._headers(idem, extra))

    def patch(self, url: str, data: Any, **extra: str) -> Any:
        return self.client.patch(url, data, format="json", **self._headers(True, extra))

    def delete(self, url: str, **extra: str) -> Any:
        return self.client.delete(url, **self._headers(True, extra))


@pytest.fixture
def login(issuer: LocalIssuer, world: World) -> Any:
    """``login("ana", company="ALFA")`` → :class:`Api` with a fresh internal token."""

    def _login(user: str, company: str | None = None, client_id: str = "erp-web") -> Api:
        subject = world.users[user].idp_subject
        assert subject, "user has no IdP subject"
        token = issuer.issue(subject, world.tenant_id, client_id)
        return Api(token, world.companies[company] if company else None)

    return _login


@pytest.fixture
def admin(world: World, issuer: LocalIssuer) -> str:
    """User ``admin`` with an IAM admin role in both companies; returns the user name."""
    world.add_role("iam_admin", ADMIN_GRANTS, max_cls="restricted-pii")
    world.add_user("admin", ["ALFA", "BETA"], subject=str(uuid.uuid4()))
    world.assign("admin", "iam_admin", "ALFA")
    world.assign("admin", "iam_admin", "BETA")
    return "admin"
