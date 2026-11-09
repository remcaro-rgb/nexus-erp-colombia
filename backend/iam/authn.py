"""DRF authentication: OIDC bearer tokens → :class:`kernel.tenancy.TenantContext`.

* :class:`InternalAuthentication` — internal API (``/api/v1/*``). Accepts only tokens
  of internal clients for INTERNAL users; portal tokens are rejected (T-PORTAL-CORE-03).
  The active company comes from ``X-Company-Id`` (must be one of the user's
  memberships, else ``company_not_allowed``) or the default membership.
* :class:`PortalAuthentication` — portal API (``/api/portal/v1/*``). Accepts only
  ``erp-portal`` tokens of EXTERNAL users linked to an ACTIVE external account. The
  context is built from the account (one tenant, one company, one party/employee),
  never from the request (D-033).

Identity lookups run under a tenant-only binding with the fixed resolver principal
:data:`RESOLVER_PRINCIPAL` (RLS still applies: the tenant comes from the verified token).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from iam.errors import CompanyNotAllowed, Unauthenticated
from iam.oidc import verifier
from iam.oidc.verifier import Claims
from kernel import clock, tenancy
from kernel.tenancy import Channel, PrincipalType, TenantContext

RESOLVER_PRINCIPAL = uuid.UUID("00000000-0000-7000-8000-00000000a001")


@dataclass(frozen=True, slots=True)
class Principal:
    """``request.user`` for authenticated IAM requests."""

    user_id: uuid.UUID
    kind: str  # INTERNAL | EXTERNAL
    claims: Claims
    external_account_id: uuid.UUID | None = None

    @property
    def is_authenticated(self) -> bool:
        return True


def bearer_token(request: Any) -> str | None:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise Unauthenticated("bearer token required")
    return value.strip()


def resolver_context(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(tenant_id, frozenset(), RESOLVER_PRINCIPAL, PrincipalType.SERVICE, channel=Channel.API)


def _company_header(request: Any) -> uuid.UUID | None:
    raw = request.META.get("HTTP_X_COMPANY_ID")
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise CompanyNotAllowed("company not available for the principal") from exc


class InternalAuthentication(BaseAuthentication):
    www_authenticate_realm = "erp-api"

    def authenticate_header(self, request: Request) -> str:
        return f'Bearer realm="{self.www_authenticate_realm}"'

    def authenticate(self, request: Request) -> tuple[Principal, Claims] | None:
        raw_bearer = bearer_token(request)
        if raw_bearer is None:
            return None
        claims = verifier.verify(raw_bearer)
        if claims.is_portal or claims.client_id not in verifier.INTERNAL_CLIENTS or "portal" in claims.scopes:
            raise Unauthenticated("portal tokens are not accepted by the internal API")
        from iam.models import Company, Membership, User

        with tenancy.scope(resolver_context(claims.tenant_id)):
            user = User.objects.filter(tenant_id=claims.tenant_id, idp_subject=claims.subject).first()
            if user is None or user.kind != User.Kind.INTERNAL or user.status != User.Status.ACTIVE:
                raise Unauthenticated("unknown or inactive user")
            memberships = list(
                Membership.objects.filter(tenant_id=claims.tenant_id, user_id=user.id).values_list(
                    "company_id", "is_default"
                )
            )
            active = set(
                Company.objects.filter(
                    tenant_id=claims.tenant_id,
                    id__in=[m[0] for m in memberships],
                    status=Company.Status.ACTIVE,
                ).values_list("id", flat=True)
            )
        allowed = [m for m in memberships if m[0] in active]
        requested = _company_header(request)
        if requested is not None:
            if requested not in {m[0] for m in allowed}:
                raise CompanyNotAllowed("company not available for the principal")
            company = requested
        else:
            defaults = sorted((not d, str(c)) for c, d in allowed)
            if not defaults:
                raise CompanyNotAllowed("the user has no active company")
            company = uuid.UUID(defaults[0][1])
        principal_type, principal_id, on_behalf = PrincipalType.USER, user.id, None
        if claims.actor:
            principal_type = PrincipalType.MCP_CLIENT if claims.client_id == "erp-mcp" else PrincipalType.AI_AGENT
            principal_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{claims.tenant_id}/agent/{claims.actor}")
            on_behalf = user.id
        channel = {PrincipalType.MCP_CLIENT: Channel.MCP, PrincipalType.AI_AGENT: Channel.AI}.get(
            principal_type, Channel.UI if claims.client_id == "erp-web" else Channel.API
        )
        ctx = TenantContext(claims.tenant_id, frozenset({company}), principal_id, principal_type, on_behalf, channel)
        request._request.erp_context = ctx  # type: ignore[attr-defined]
        request.erp_context = ctx  # type: ignore[attr-defined]
        return Principal(user.id, user.kind, claims), claims


class PortalAuthentication(BaseAuthentication):
    """Portal tokens only. ``allow_unlinked`` serves the invitation-accept endpoint."""

    allow_unlinked = False

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="erp-portal"'

    def authenticate(self, request: Request) -> tuple[Principal, Claims] | None:
        raw_bearer = bearer_token(request)
        if raw_bearer is None:
            return None
        claims = verifier.verify(raw_bearer)
        if not claims.is_portal:
            raise Unauthenticated("only portal tokens are accepted by the portal API")
        from iam.models import ExternalAccount, User

        with tenancy.scope(resolver_context(claims.tenant_id)):
            user = User.objects.filter(tenant_id=claims.tenant_id, idp_subject=claims.subject).first()
            account = (
                ExternalAccount.objects.filter(tenant_id=claims.tenant_id, user_id=user.id).first() if user else None
            )
            if user is None or account is None:
                if self.allow_unlinked:
                    request.erp_context = None  # type: ignore[attr-defined]
                    return Principal(RESOLVER_PRINCIPAL, "UNLINKED", claims), claims
                raise Unauthenticated("no portal account for this identity")
            if (
                user.kind != User.Kind.EXTERNAL
                or user.status != User.Status.ACTIVE
                or account.external_status != ExternalAccount.ExternalStatus.ACTIVE
            ):
                raise Unauthenticated("portal account is not active")
            iat = claims.raw.get("iat")
            if isinstance(iat, int) and (account.last_login_at is None or account.last_login_at.timestamp() < iat):
                ExternalAccount.objects.filter(pk=account.pk).update(last_login_at=clock.now())
        ctx = TenantContext(claims.tenant_id, frozenset({account.company_id}), user.id, PrincipalType.USER)
        request._request.erp_context = ctx  # type: ignore[attr-defined]
        request.erp_context = ctx  # type: ignore[attr-defined]
        return Principal(user.id, user.kind, claims, account.id), claims


class PortalAcceptAuthentication(PortalAuthentication):
    allow_unlinked = True
