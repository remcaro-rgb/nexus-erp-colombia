"""Policy and audit helpers shared by the intercompany and consolidation services.

Both resources are **tenant level** but describe companies. A principal sees/changes one only when the
registered ``PolicyService`` grants the action for the companies involved (``resource = {tenant_id,
company_id}`` per company) and those companies are in its context. Deny by default: no ``PolicyService`` → denied.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from consolidation.errors import PolicyDenied
from kernel import flags, services
from kernel.errors import FeatureDisabled
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

#: [A] flag of the consolidation feature (P16 prompt). Intercompany automation is [P] and not flagged.
CONSOLIDATION_FLAG = flags.register(
    "consolidation", kind="A", description="Consolidación de grupos empresariales (P16 [A]); off by default"
).code


def policy() -> PolicyService | None:
    return services.optional(PolicyService)


def allowed(ctx: TenantContext, action: str, companies: Iterable[uuid.UUID] = ()) -> bool:
    svc = policy()
    if svc is None or not svc.authorize(ctx, action).allowed:
        return False
    for company in companies:
        if company not in ctx.company_ids:
            return False
        if not svc.authorize(ctx, action, {"tenant_id": ctx.tenant_id, "company_id": company}).allowed:
            return False
    return True


def require(ctx: TenantContext, action: str, companies: Iterable[uuid.UUID] = ()) -> None:
    if not allowed(ctx, action, list(companies)):
        raise PolicyDenied(f"missing permission {action}")


def require_feature(ctx: TenantContext, companies: Iterable[uuid.UUID] = ()) -> None:
    """Consolidation [A] must be enabled for the tenant (or every member company)."""
    comps = list(companies)
    if comps:
        if all(flags.is_enabled(CONSOLIDATION_FLAG, ctx.tenant_id, c) for c in comps):
            return
    elif flags.is_enabled(CONSOLIDATION_FLAG, ctx.tenant_id):
        return
    raise FeatureDisabled("la consolidación ([A]) no está habilitada para este tenant")


def audit(
    uow: UnitOfWork,
    action: str,
    resource: str,
    rid: uuid.UUID | None,
    company: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, resource, rid, company, changes or {}, reason[:2000]))
