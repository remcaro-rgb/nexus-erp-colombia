"""Local OIDC issuer for DEV/tests (stands in for the Keycloak realm in-process).

It mints RS256 access tokens with exactly the claims the ``erp-dev`` realm
template produces (``iss``, ``sub``, ``aud``, ``azp``, ``tenant_id``, ``groups``,
``scope``), and checks passwords it was given at registration time. Passwords
live only in memory (hashed with Django's hasher); nothing is persisted, printed
or logged. Never used against a real identity provider.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.hashers import check_password, make_password

from iam.errors import Unauthenticated
from iam.oidc import jwt
from iam.oidc.verifier import AUDIENCE, INTERNAL_CLIENTS, PORTAL_CLIENT

_ACCESS_TOKEN_SECONDS = 600  # realm accessTokenLifespan


@dataclass
class _Account:
    subject: str
    password_hash: str
    tenant_id: uuid.UUID
    groups: tuple[str, ...]
    attributes: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class LocalIssuer:
    def __init__(self, issuer: str = "http://127.0.0.1:8080/auth/realms/erp-dev") -> None:
        self.issuer = issuer
        self._key = jwt.generate_key()
        self._accounts: dict[str, _Account] = {}

    # KeyProvider
    def keys(self) -> dict[str, jwt.RSAPublicKey]:
        return {self._key.kid: self._key.public()}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self._key.public().to_jwk()]}

    def register(
        self, username: str, password: str, tenant_id: uuid.UUID, groups: tuple[str, ...] = (), **attributes: Any
    ) -> str:
        """Create (or reset) a realm user; returns its subject (``sub``)."""
        existing = self._accounts.get(username)
        subject = existing.subject if existing else str(uuid.uuid4())
        self._accounts[username] = _Account(subject, make_password(password), tenant_id, tuple(groups), attributes)
        return subject

    def disable(self, username: str) -> None:
        self._accounts[username].enabled = False

    def login(self, username: str, password: str, client_id: str) -> str:
        acc = self._accounts.get(username)
        if acc is None or not acc.enabled or not check_password(password, acc.password_hash):
            raise Unauthenticated("invalid credentials")
        if client_id != PORTAL_CLIENT and client_id not in INTERNAL_CLIENTS:
            raise Unauthenticated("unknown client")
        portal_groups = {g for g in acc.groups if g.startswith("portal-")}
        if client_id == PORTAL_CLIENT and not portal_groups:
            raise Unauthenticated("client not allowed for this user")
        if client_id != PORTAL_CLIENT and portal_groups:
            raise Unauthenticated("client not allowed for this user")
        return self.issue(acc.subject, acc.tenant_id, client_id, groups=acc.groups)

    def issue(
        self,
        subject: str,
        tenant_id: uuid.UUID,
        client_id: str,
        *,
        groups: tuple[str, ...] = (),
        lifetime: int = _ACCESS_TOKEN_SECONDS,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "aud": [AUDIENCE],
            "azp": client_id,
            "typ": "Bearer",
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "jti": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "groups": list(groups),
            "scope": "openid portal" if client_id == PORTAL_CLIENT else "openid erp.api",
            **(extra or {}),
        }
        return jwt.encode(claims, self._key)
