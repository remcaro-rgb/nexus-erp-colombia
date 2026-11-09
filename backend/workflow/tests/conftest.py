"""Fixtures of the workflow tests.

Until the integrator adds ``"workflow"`` to ``INSTALLED_APPS`` (CCR P07-1), the
app is installed here, at collection time, before pytest-django creates the
test database, so the workflow migrations run against the real PostgreSQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from django.apps import apps
from django.conf import settings

if "workflow" not in settings.INSTALLED_APPS:
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "workflow"]
    apps.set_installed_apps(settings.INSTALLED_APPS)

from kernel import ids, services  # noqa: E402
from kernel.interfaces.policy import Decision, FieldMask, PolicyService  # noqa: E402
from kernel.tenancy import TenantContext  # noqa: E402
from kernel.testing.tenancy import make_context  # noqa: E402


class GrantPolicy:
    """Deny-by-default fake of P04: grants per principal, optional field masks and SoD denials."""

    def __init__(self) -> None:
        self.grants: set[tuple[uuid.UUID, str]] = set()
        self.readable: dict[tuple[uuid.UUID, str], frozenset[str]] = {}
        self.sod_denied: set[uuid.UUID] = set()
        self.version = 1

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.update((principal, a) for a in actions)
        self.version += 1

    def revoke(self, principal: uuid.UUID, *actions: str) -> None:
        for a in actions:
            self.grants.discard((principal, a))
        self.version += 1

    def authorize(self, ctx: TenantContext, action: str, resource: object = None) -> Decision:
        if ctx.principal_id in self.sod_denied and action.endswith(".approve"):
            return Decision(False, "sod: conflicting duties")
        principal = ctx.principal_id
        allowed = (principal, action) in self.grants or (principal, "*") in self.grants
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: object = None) -> None:
        from kernel.errors import PermissionDenied

        d = self.authorize(ctx, action, resource)
        if not d.allowed:
            raise PermissionDenied(f"missing permission {action}")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: object) -> object:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()  # type: ignore[attr-defined]
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))  # type: ignore[attr-defined]

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        fields = self.readable.get((ctx.principal_id, resource_type), frozenset())
        return FieldMask(fields, frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


@pytest.fixture
def grants() -> Iterator[GrantPolicy]:
    p = GrantPolicy()
    with services.override(PolicyService, p):
        yield p


@pytest.fixture
def company() -> uuid.UUID:
    return ids.new_id()


@pytest.fixture
def tenant() -> uuid.UUID:
    return ids.new_id()


@pytest.fixture
def user_factory(tenant: uuid.UUID, company: uuid.UUID, grants: GrantPolicy):  # type: ignore[no-untyped-def]
    def make(*actions: str, **kw: object) -> TenantContext:
        ctx = make_context(tenant_id=tenant, company_ids=[company], **kw)  # type: ignore[arg-type]
        grants.grant(ctx.principal_id, *actions)
        return ctx

    return make
