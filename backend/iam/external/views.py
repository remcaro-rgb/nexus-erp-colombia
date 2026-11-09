"""External accounts (internal admin API) and portal identity endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response

from kernel import tenancy
from kernel.errors import NotFound
from kernel.uow import UnitOfWork, run_in_unit_of_work

from iam.api.base import PrincipalThrottle, SecuredView, check_if_match
from iam.authn import PortalAcceptAuthentication
from iam.errors import IdempotencyKeyRequired, Unauthenticated
from iam.external import services
from iam.external.portal import PortalView, own_account_id
from iam.models import Company, ExternalAccount
from iam.policy import fields as field_registry
from iam.policy.engine import get_engine

RESOURCE = "iam.external_account"
field_registry.register(
    RESOURCE,
    {
        "id": "public",
        "tenant_id": "internal",
        "company_id": "internal",
        "status": "internal",
        "email": "restricted-pii",
        "portal_kind": "internal",
        "party": "confidential",
        "employee_id": "restricted-pii",
        "external_status": "internal",
        "invitation_expires_at": "internal",
        "last_login_at": "internal",
        "version": "internal",
        "audit": "internal",
    },
)
SESSION_IDLE_MINUTES = 30  # D-033
PORTAL_MENUS = {
    "supplier": ["inicio", "cotizaciones", "ordenes", "envios", "facturas", "pagos", "perfil"],
    "customer": ["inicio", "cotizaciones", "pedidos", "facturas", "pagos", "solicitudes", "perfil"],
    "employee": ["inicio", "desprendibles", "certificados", "vacaciones", "datos", "perfil"],
}


def account_dict(acc: ExternalAccount) -> dict[str, Any]:
    latest = acc.invitations.order_by("-created_at").first()
    return {
        "id": acc.id,
        "tenant_id": acc.tenant_id,
        "company_id": acc.company_id,
        "status": "ARCHIVED" if acc.external_status == services.S.REVOKED else "ACTIVE",
        "email": acc.email,
        "portal_kind": acc.portal_kind,
        "party": {"id": str(acc.party_id or acc.employee_id), "kind": acc.party_kind},
        "employee_id": acc.employee_id,
        "external_status": acc.external_status,
        "invitation_expires_at": latest.expires_at.isoformat() if latest else None,
        "last_login_at": acc.last_login_at.isoformat() if acc.last_login_at else None,
        "version": acc.version,
        "audit": {
            "created_at": acc.created_at.isoformat(),
            "created_by": str(acc.created_by) if acc.created_by else None,
            "updated_at": acc.updated_at.isoformat(),
            "updated_by": str(acc.updated_by) if acc.updated_by else None,
            "channel": "portal",
        },
    }


class _AccountView(SecuredView):
    resource_type = RESOURCE

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return account_dict(obj)

    def _company(self) -> uuid.UUID:
        return sorted(self.ctx.company_ids)[0]


class ExternalAccountsView(_AccountView):
    permissions = {"GET": "iam.external_account.read", "POST": "iam.external_account.create"}

    def get(self, request: Request) -> Response:
        qs = get_engine().scope_queryset(self.ctx, "iam.external_account.read", ExternalAccount.objects.all())
        return self.paginate(
            request,
            qs,
            search_fields=("email",),
            filter_fields={"portal_kind": "portal_kind", "external_status": "external_status", "email": "email"},
            sort_fields=("email", "portal_kind", "external_status"),
        )

    def post(self, request: Request) -> Response:
        self.check_writable(request.data, ("email", "portal_kind", "party", "employee_id"))
        company = self._company()

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            get_engine().require(self.ctx, "iam.external_account.create", {"company_id": company})
            acc = services.invite(uow, company, request.data)
            return self.serialize(acc), 201

        return self.mutate(request, run, scope="iam.external_account.create")


class ExternalAccountDetailView(_AccountView):
    permissions = {"GET": "iam.external_account.read"}

    def get(self, request: Request, pk: str) -> Response:
        with tenancy.scope(self.ctx):
            acc = self.get_scoped(ExternalAccount.objects.all(), pk, "iam.external_account.read")
            return Response(self.serialize(acc))


class ExternalAccountActionView(_AccountView):
    permissions = {"POST": "iam.external_account.update"}
    action_name: ClassVar[str] = ""

    def post(self, request: Request, pk: str) -> Response:
        body = request.data if isinstance(request.data, Mapping) else {}
        reason = body.get("reason")

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            action = self.permissions["POST"]
            acc = self.get_scoped(ExternalAccount.objects.select_for_update(), pk, action)
            check_if_match(request, RESOURCE, acc, required=False)
            if self.action_name == "suspend":
                services.suspend(uow, acc, reason)
            elif self.action_name == "reactivate":
                services.reactivate(uow, acc)
            elif self.action_name == "revoke":
                services.revoke(uow, acc, reason)
            else:
                services.resend(uow, acc)
            return self.serialize(acc), 200

        return self.mutate(request, run, scope=f"iam.external_account.{self.action_name}")


class SuspendView(ExternalAccountActionView):
    action_name = "suspend"


class ReactivateView(ExternalAccountActionView):
    action_name = "reactivate"


class RevokeView(ExternalAccountActionView):
    permissions = {"POST": "iam.external_account.void"}
    action_name = "revoke"


class ResendView(ExternalAccountActionView):
    action_name = "resend"


# ------------------------------------------------------------------------------ portal


class PortalMeView(PortalView):
    permissions = {"GET": "portal.me.read"}
    portal_fields = ("id", "tenant_id", "external_account_id", "portal_kind", "display_name", "email", "version")

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            acc = ExternalAccount.objects.select_related("user").get(pk=own_account_id(self))
        return Response(
            self.serialize(
                {
                    "id": acc.user_id,
                    "tenant_id": acc.tenant_id,
                    "external_account_id": acc.id,
                    "portal_kind": acc.portal_kind,
                    "display_name": acc.user.display_name or acc.email,
                    "email": acc.email,
                    "version": acc.version,
                }
            )
        )


class PortalContextView(PortalView):
    permissions = {"GET": "portal.context.read"}
    portal_fields = ("id", "tenant_id", "company_name", "party", "menu", "session_idle_minutes", "version")

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            acc = ExternalAccount.objects.get(pk=own_account_id(self))
            company = Company.objects.filter(tenant_id=acc.tenant_id, id=acc.company_id).first()
        return Response(
            self.serialize(
                {
                    "id": acc.id,
                    "tenant_id": acc.tenant_id,
                    "company_name": company.legal_name if company else "",
                    "party": {"id": str(acc.party_id or acc.employee_id), "kind": acc.party_kind},
                    "menu": {"kind": acc.portal_kind, "items": PORTAL_MENUS[acc.portal_kind]},
                    "session_idle_minutes": SESSION_IDLE_MINUTES,
                    "version": acc.version,
                }
            )
        )


class PortalAcceptInvitationView(SecuredView):
    """``POST /api/portal/v1/invitations:accept`` (CCR P04-2: not yet in the frozen contract).

    Authenticated with an ``erp-portal`` token whose subject is not linked yet; links the
    subject to the invited account and activates it.
    """

    authentication_classes: ClassVar[list[Any]] = [PortalAcceptAuthentication]
    permission_classes: ClassVar[list[Any]] = []
    throttle_classes: ClassVar[list[Any]] = [PrincipalThrottle]

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        # authentication + throttling only: there is no tenant context before the link exists
        super(SecuredView, self).initial(request, *args, **kwargs)
        if request.auth is None:
            raise Unauthenticated("portal token required")

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        return super(SecuredView, self).finalize_response(request, response, *args, **kwargs)

    def post(self, request: Request) -> Response:
        claims = request.auth
        if claims is None:
            raise Unauthenticated("portal token required")
        if getattr(request.user, "kind", "") != "UNLINKED":
            raise NotFound("invitation not found")
        key = request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not key:
            raise IdempotencyKeyRequired("Idempotency-Key header required")
        token = str((request.data or {}).get("token") or "")
        ctx = services.acceptance_context(token, claims)

        def run(uow: UnitOfWork) -> dict[str, Any]:
            acc = services.accept(uow, token, claims)
            body = {"external_account_id": str(acc.id), "external_status": acc.external_status}
            uow.set_response(body, 200)
            return body

        result = run_in_unit_of_work(run, ctx, idempotency_key=key, request={"token_hash": services._hash(token)})
        body = result if isinstance(result, dict) else result.body
        return Response(body, status=200)

