"""OIDC access-token verification (Keycloak realm ``erp-dev``, D-018/D-033).

* Issuer: ``ERP_OIDC_ISSUER`` (compose sets it; default ``<ERP_PUBLIC_ORIGIN>/auth/realms/erp-dev``).
* Keys: the realm JWKS fetched from ``ERP_OIDC_INTERNAL_ISSUER`` (or the issuer) and cached,
  or an in-process :class:`iam.oidc.local.LocalIssuer` registered for dev/tests.
* Required claims: ``iss``, ``sub``, ``exp``, ``aud`` ∋ ``erp-api``, ``azp`` (client id),
  ``tenant_id`` (user attribute mapper). Clock leeway 30 s.
* Client separation: internal API accepts only internal clients; the portal API accepts
  only ``erp-portal`` (a portal token is never an internal token and vice versa).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from iam.errors import Unauthenticated
from iam.oidc import jwt

AUDIENCE = "erp-api"
PORTAL_CLIENT = "erp-portal"
INTERNAL_CLIENTS = frozenset({"erp-web", "erp-mcp", "erp-smoke", "erp-api"})
LEEWAY_SECONDS = 30
JWKS_TTL_SECONDS = 300


class KeyProvider(Protocol):
    def keys(self) -> dict[str, jwt.RSAPublicKey]: ...


def default_issuer() -> str:
    configured = getattr(settings, "ERP_OIDC_ISSUER", None) or os.environ.get("ERP_OIDC_ISSUER")
    if configured:
        return str(configured).rstrip("/")
    return f"{settings.ERP_PUBLIC_ORIGIN}/auth/realms/erp-dev"


class JwksKeyProvider:
    """Fetches the realm JWKS (never inside a unit of work: authentication runs first)."""

    def __init__(self, jwks_url: str) -> None:
        if not jwks_url.startswith(("http://", "https://")):
            raise ValueError("JWKS URL must be http(s)")
        self.jwks_url = jwks_url
        self._keys: dict[str, jwt.RSAPublicKey] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    def keys(self) -> dict[str, jwt.RSAPublicKey]:
        with self._lock:
            if not self._keys or time.monotonic() - self._fetched > JWKS_TTL_SECONDS:
                req = urllib.request.Request(self.jwks_url, headers={"Accept": "application/json"})  # noqa: S310
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - realm JWKS, http(s) only
                    doc = json.loads(resp.read())
                keys = {}
                for jwk in doc.get("keys", []):
                    try:
                        key = jwt.RSAPublicKey.from_jwk(jwk)
                    except (jwt.JWTError, KeyError):
                        continue
                    keys[key.kid] = key
                self._keys, self._fetched = keys, time.monotonic()
            return dict(self._keys)


@dataclass(frozen=True, slots=True)
class Claims:
    subject: str
    tenant_id: uuid.UUID
    client_id: str
    scopes: frozenset[str]
    groups: frozenset[str]
    expires_at: int
    actor: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def is_portal(self) -> bool:
        return self.client_id == PORTAL_CLIENT


_provider: KeyProvider | None = None
_issuer_override: str | None = None
_lock = threading.Lock()


def set_key_provider(provider: KeyProvider | None, issuer: str | None = None) -> None:
    """Register the key source (dev local issuer / tests). ``None`` restores the JWKS default."""
    global _provider, _issuer_override
    with _lock:
        _provider, _issuer_override = provider, issuer


def _current() -> tuple[KeyProvider, str]:
    global _provider
    with _lock:
        issuer = _issuer_override or default_issuer()
        if _provider is None:
            internal = (
                getattr(settings, "ERP_OIDC_INTERNAL_ISSUER", None)
                or os.environ.get("ERP_OIDC_INTERNAL_ISSUER")
                or issuer
            )
            _provider = JwksKeyProvider(internal.rstrip("/") + "/protocol/openid-connect/certs")
        return _provider, issuer


def verify(token: str) -> Claims:
    provider, issuer = _current()
    try:
        keys = provider.keys()
        claims = jwt.decode(token, keys)
    except jwt.JWTError as exc:
        raise Unauthenticated("invalid token") from exc
    except OSError as exc:
        raise Unauthenticated("identity provider unavailable") from exc
    now = int(time.time())
    if claims.get("iss") != issuer:
        raise Unauthenticated("invalid token issuer")
    aud = claims.get("aud")
    audiences = {aud} if isinstance(aud, str) else set(aud or [])
    if AUDIENCE not in audiences:
        raise Unauthenticated("invalid token audience")
    exp, nbf = claims.get("exp"), claims.get("nbf", 0)
    if not isinstance(exp, int) or exp + LEEWAY_SECONDS < now:
        raise Unauthenticated("token expired")
    if not isinstance(nbf, int) or nbf - LEEWAY_SECONDS > now:
        raise Unauthenticated("token not yet valid")
    typ = claims.get("typ", "Bearer")
    if typ != "Bearer":
        raise Unauthenticated("not an access token")
    sub, azp = claims.get("sub"), claims.get("azp")
    if not isinstance(sub, str) or not sub or not isinstance(azp, str):
        raise Unauthenticated("invalid token subject")
    try:
        tenant_id = uuid.UUID(str(claims.get("tenant_id")))
    except ValueError as exc:
        raise Unauthenticated("token has no tenant") from exc
    scope = claims.get("scope", "")
    groups = claims.get("groups") or []
    act = claims.get("act")
    return Claims(
        subject=sub,
        tenant_id=tenant_id,
        client_id=azp,
        scopes=frozenset(str(scope).split()),
        groups=frozenset(str(g).lstrip("/") for g in groups if isinstance(g, str)),
        expires_at=exp,
        actor=str(act.get("sub")) if isinstance(act, dict) and act.get("sub") else None,
        raw=claims,
    )
