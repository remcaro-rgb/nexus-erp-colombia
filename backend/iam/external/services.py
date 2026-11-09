"""External (portal) accounts — F-PORTAL-CORE, D-018, D-033.

Lifecycle: INVITED → ACTIVE (accept) → SUSPENDED ↔ ACTIVE → REVOKED (terminal);
an unused invitation expires after 72 h (EXPIRED; ``:resend`` issues a new token and
supersedes the old one). An external account links exactly one party (supplier /
customer) or one employee in exactly one company of one tenant. It is backed by an
``iam_user`` row of kind EXTERNAL, which can never receive a role assignment
(service check + DB trigger, ``external_role_forbidden``).

Invitation tokens: ``nxi.<tenant hex>.<random>``; only the SHA-256 is stored. The
token travels only inside the notification request to the local mail capture
(P07 → Mailpit in DEV); it is never returned by the API, logged or put in an event.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
import uuid
from collections.abc import Mapping
from typing import Any

from iam.errors import InvalidStateTransition, InvitationExpired, Unauthenticated
from iam.models import ExternalAccount, PortalInvitation, User
from iam.oidc.verifier import Claims
from iam.policy.engine import PolicyEngine
from kernel import clock, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.notifications import NotificationRequest, NotificationService
from kernel.tenancy import PrincipalType, TenantContext
from kernel.uow import UnitOfWork

INVITATION_TTL = dt.timedelta(hours=72)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[A-Za-z]{2,}$")
PARTY_KIND = {"supplier": "vendor", "customer": "customer", "employee": "employee"}
S = ExternalAccount.ExternalStatus


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(tenant_id: uuid.UUID) -> str:
    return f"nxi.{tenant_id.hex}.{secrets.token_urlsafe(32)}"


def tenant_of_token(token: str) -> uuid.UUID:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or parts[0] != "nxi":
        raise NotFound("invitation not found")
    try:
        return uuid.UUID(hex=parts[1])
    except ValueError as exc:
        raise NotFound("invitation not found") from exc


def _audit(uow: UnitOfWork, action: str, acc: ExternalAccount, changes: dict[str, Any] | None = None) -> None:
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(uow, AuditEntry(action, "iam.external_account", acc.id, acc.company_id, changes or {}))


def _emit(uow: UnitOfWork, acc: ExternalAccount) -> None:
    uow.emit(
        "iam.external_account.status_changed.v1",
        {"external_account_id": str(acc.id), "portal_kind": acc.portal_kind, "to_status": acc.external_status},
        company_id=acc.company_id,
    )


def _issue_invitation(uow: UnitOfWork, acc: ExternalAccount) -> None:
    now = clock.now()
    PortalInvitation.objects.filter(account=acc, used_at__isnull=True, superseded_at__isnull=True).update(
        superseded_at=now
    )
    token = new_token(acc.tenant_id)
    PortalInvitation.objects.create(
        tenant_id=acc.tenant_id, account=acc, token_hash=_hash(token), expires_at=now + INVITATION_TTL
    )
    services.get(NotificationService).notify(
        uow,
        NotificationRequest(
            recipient_id=acc.user_id,
            template_code="iam.portal_invitation",
            data={"invitation_token": token, "portal_kind": acc.portal_kind, "expires_hours": 72},
            channels=("email",),
            company_id=acc.company_id,
        ),
    )


def invite(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> ExternalAccount:
    ctx = uow.ctx
    email = str(data.get("email") or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise ValidationFailed("invalid email", field="email")
    kind = data.get("portal_kind")
    if kind not in PARTY_KIND:
        raise ValidationFailed("portal_kind must be supplier, customer or employee", field="portal_kind")
    party = data.get("party")
    if not isinstance(party, Mapping) or "id" not in party:
        raise ValidationFailed("party is required", field="party")
    try:
        party_id = uuid.UUID(str(party["id"]))
        employee_raw = data.get("employee_id")
        employee_id = uuid.UUID(str(employee_raw)) if employee_raw else None
    except ValueError as exc:
        raise ValidationFailed("party.id / employee_id must be UUIDs", field="party") from exc
    party_kind = party.get("kind") or PARTY_KIND[kind]
    if party_kind != PARTY_KIND[kind]:
        raise ValidationFailed("party.kind does not match portal_kind", code="portal_kind_mismatch")
    if kind == "employee":
        employee_id = employee_id or party_id
        party_id = None  # type: ignore[assignment]
    elif employee_id is not None:
        raise ValidationFailed("employee_id only applies to the employee portal", field="employee_id")
    if (
        User.objects.filter(tenant_id=ctx.tenant_id, kind=User.Kind.EXTERNAL, email=email)
        .exclude(status=User.Status.REVOKED)
        .exists()
    ):
        raise ValidationFailed("an external account with this email already exists", field="email")
    user = User.objects.create(
        tenant_id=ctx.tenant_id,
        kind=User.Kind.EXTERNAL,
        email=email,
        display_name=email,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    acc = ExternalAccount.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        user=user,
        portal_kind=kind,
        party_id=party_id,
        party_kind=party_kind,
        employee_id=employee_id,
        email=email,
        invited_by=ctx.principal_id,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _issue_invitation(uow, acc)
    PolicyEngine.bump_version(ctx.tenant_id)
    _audit(uow, "iam.external_account.invited", acc, {"portal_kind": kind})
    _emit(uow, acc)
    return acc


def _set_status(uow: UnitOfWork, acc: ExternalAccount, status: str, user_status: str | None = None) -> None:
    acc.external_status = status
    acc.save_versioned(uow.ctx.principal_id, ["external_status"])
    if user_status is not None:
        User.objects.filter(pk=acc.user_id).update(status=user_status, updated_at=clock.now())
    PolicyEngine.bump_version(acc.tenant_id)
    _emit(uow, acc)


def suspend(uow: UnitOfWork, acc: ExternalAccount, reason: str | None) -> ExternalAccount:
    if acc.external_status != S.ACTIVE:
        raise InvalidStateTransition(f"cannot suspend an account in {acc.external_status}")
    _set_status(uow, acc, S.SUSPENDED, User.Status.SUSPENDED)
    _audit(uow, "iam.external_account.suspended", acc, {"reason": reason or ""})
    return acc


def reactivate(uow: UnitOfWork, acc: ExternalAccount) -> ExternalAccount:
    if acc.external_status != S.SUSPENDED:
        raise InvalidStateTransition(f"cannot reactivate an account in {acc.external_status}")
    _set_status(uow, acc, S.ACTIVE, User.Status.ACTIVE)
    _audit(uow, "iam.external_account.reactivated", acc)
    return acc


def revoke(uow: UnitOfWork, acc: ExternalAccount, reason: str | None) -> ExternalAccount:
    if acc.external_status == S.REVOKED:
        raise InvalidStateTransition("the account is already revoked")
    if not reason or not str(reason).strip():
        raise ValidationFailed("reason is required", code="reason_required")
    PortalInvitation.objects.filter(account=acc, used_at__isnull=True, superseded_at__isnull=True).update(
        superseded_at=clock.now()
    )
    _set_status(uow, acc, S.REVOKED, User.Status.REVOKED)
    _audit(uow, "iam.external_account.revoked", acc, {"reason": str(reason)[:2000]})
    return acc


def resend(uow: UnitOfWork, acc: ExternalAccount) -> ExternalAccount:
    if acc.external_status not in (S.INVITED, S.EXPIRED):
        raise InvalidStateTransition(f"cannot resend an invitation for an account in {acc.external_status}")
    _issue_invitation(uow, acc)
    if acc.external_status != S.INVITED:
        _set_status(uow, acc, S.INVITED)
    _audit(uow, "iam.external_account.invitation_resent", acc)
    return acc


def expire_due(uow: UnitOfWork) -> int:
    """Mark INVITED accounts whose latest invitation passed 72 h as EXPIRED (scheduler job)."""
    now = clock.now()
    count = 0
    for acc in ExternalAccount.objects.filter(tenant_id=uow.ctx.tenant_id, external_status=S.INVITED):
        live = PortalInvitation.objects.filter(
            account=acc, used_at__isnull=True, superseded_at__isnull=True, expires_at__gt=now
        ).exists()
        if not live:
            _set_status(uow, acc, S.EXPIRED)
            count += 1
    return count


def acceptance_context(token: str, claims: Claims) -> TenantContext:
    """Context for the accept unit of work, derived from the token, never from the body."""
    from iam.authn import resolver_context
    from kernel import tenancy

    tenant_id = tenant_of_token(token)
    if tenant_id != claims.tenant_id:
        raise NotFound("invitation not found")
    with tenancy.scope(resolver_context(tenant_id)):
        inv = PortalInvitation.objects.select_related("account").filter(token_hash=_hash(token)).first()
        if inv is None:
            raise NotFound("invitation not found")
        acc = inv.account
    return TenantContext(tenant_id, frozenset({acc.company_id}), acc.user_id, PrincipalType.USER)


def accept(uow: UnitOfWork, token: str, claims: Claims) -> ExternalAccount:
    now = clock.now()
    inv = (
        PortalInvitation.objects.select_for_update()
        .select_related("account")
        .filter(tenant_id=uow.ctx.tenant_id, token_hash=_hash(token))
        .first()
    )
    if inv is None or inv.superseded_at is not None:
        raise NotFound("invitation not found")
    acc = ExternalAccount.objects.select_for_update().get(pk=inv.account_id)
    if inv.used_at is not None or acc.external_status not in (S.INVITED, S.EXPIRED):
        raise InvalidStateTransition("the invitation was already used or the account is not invited")
    if inv.expires_at <= now or acc.external_status == S.EXPIRED:
        if acc.external_status == S.INVITED:
            _set_status(uow, acc, S.EXPIRED)
        raise InvitationExpired("the invitation expired; ask for a new one")
    if User.objects.filter(tenant_id=acc.tenant_id, idp_subject=claims.subject).exists():
        raise Unauthenticated("this identity is already linked to another account")
    inv.used_at = now
    inv.save(update_fields=["used_at"])
    User.objects.filter(pk=acc.user_id).update(idp_subject=claims.subject, updated_at=now)
    acc.last_login_at = now
    acc.save(update_fields=["last_login_at"])
    _set_status(uow, acc, S.ACTIVE)
    _audit(uow, "iam.external_account.accepted", acc)
    return acc
