"""IAM implementation of :class:`kernel.interfaces.ui.UiShellProvider` (FIX-UI-LOGIN-W03 UL-2/UL-3/UL-4).

Registered at app ready (``iam.apps``). Everything is computed server-side for the request's principal:

* :func:`effective_permissions` — effective catalog codes ∪ resolved registry navigation codes
  (:mod:`iam.policy.nav`); the same list ``/api/v1/iam/me`` returns.
* ``context_extras`` — companies of the principal's ACTIVE memberships (``id, legal_name, nit, code`` only),
  role keys, permissions, ``user {display_name, email}``, ``session_idle_minutes``, ``feature_flags``.
* ``modules`` — the shipped registry filtered by those permissions (absent, not disabled); a missing registry
  fails closed with 503 ``service_unavailable`` (never an empty or fake registry).
"""

from __future__ import annotations

import uuid
from typing import Any

from iam.policy import nav
from iam.policy.catalog import catalog_codes, module_registry
from iam.policy.engine import get_engine
from kernel import tenancy
from kernel.errors import ServiceUnavailable
from kernel.tenancy import TenantContext

#: Realm ``erp-dev`` ``ssoSessionIdleTimeout`` (1800 s).
SESSION_IDLE_MINUTES = 30


def effective_permissions(ctx: TenantContext) -> list[str]:
    eff = get_engine().effective(ctx)
    catalog = catalog_codes()
    allowed = eff.permissions(catalog)
    shell_ok = eff.principal_kind in ("internal", "delegated") and bool(ctx.company_ids)
    registry = module_registry() or {}
    return nav.effective_codes(allowed, catalog, registry, shell_ok=shell_ok)


class IamUiShellProvider:
    def context_extras(self, ctx: TenantContext, company_id: uuid.UUID | None) -> dict[str, Any]:
        from iam.models import Company, Membership, User

        eff = get_engine().effective(ctx)
        user_id = ctx.on_behalf_of or ctx.principal_id
        with tenancy.scope(ctx):
            user = User.objects.filter(tenant_id=ctx.tenant_id, id=user_id).first()
            member_of = Membership.objects.filter(tenant_id=ctx.tenant_id, user_id=user_id).values_list(
                "company_id", flat=True
            )
            companies = list(
                Company.objects.filter(
                    tenant_id=ctx.tenant_id, id__in=list(member_of), status=Company.Status.ACTIVE
                ).order_by("legal_name", "id")
            )
        return {
            "companies": [
                {
                    "id": str(c.id),
                    "legal_name": c.legal_name,
                    "nit": f"{c.nit}-{c.dv}" if c.dv else c.nit,
                    "code": c.code,
                }
                for c in companies
            ],
            "roles": sorted({g.role_key for g in eff.grants}),
            "permissions": effective_permissions(ctx),
            "user": {"display_name": user.display_name or user.email, "email": user.email} if user else None,
            "session_idle_minutes": SESSION_IDLE_MINUTES,
            "feature_flags": {},
        }

    def modules(self, ctx: TenantContext, company_id: uuid.UUID | None) -> dict[str, Any]:
        registry = module_registry()
        if registry is None:
            raise ServiceUnavailable("the UI module registry is not shipped with this build")
        allowed = frozenset(effective_permissions(ctx))
        return nav.filter_registry(registry, allowed.__contains__)
