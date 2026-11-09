"""Synthetic IAM world for tests and demos (no real data; DEV only)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from kernel import ids
from kernel.tenancy import PrincipalType, TenantContext
from kernel.uow import unit_of_work

from iam.models import Company, Membership, Role, RoleAssignment, Tenant, User
from iam.policy.engine import PolicyEngine

ADMIN_GRANTS = ["iam.*.*", "ledger.dimension.*", "*.*.read", "*.*.export"]


@dataclass
class World:
    tenant_id: uuid.UUID
    companies: dict[str, uuid.UUID] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    roles: dict[str, Role] = field(default_factory=dict)
    seed_principal: uuid.UUID = field(default_factory=ids.new_id)

    def ctx(self, companies: list[str] | None = None, principal: uuid.UUID | None = None) -> TenantContext:
        names = companies or list(self.companies)
        return TenantContext(
            self.tenant_id,
            frozenset(self.companies[n] for n in names),
            principal or self.seed_principal,
            PrincipalType.USER,
        )

    def seed(self) -> Any:
        return unit_of_work(self.ctx())

    def add_company(self, name: str, nit: str) -> uuid.UUID:
        cid = ids.new_id()
        self.companies[name] = cid
        with self.seed():
            Company.objects.create(
                id=cid, tenant_id=self.tenant_id, code=name, nit=nit, dv="0", legal_name=f"{name} S.A.S. (sintética)"
            )
        return cid

    def add_role(self, key: str, grants: list[str], denies: list[str] | None = None, max_cls: str = "internal") -> Role:
        with self.seed():
            role = Role.objects.create(
                tenant_id=self.tenant_id, key=key, name=key, grants=grants, denies=denies or [], max_classification=max_cls
            )
            PolicyEngine.bump_version(self.tenant_id)
        self.roles[key] = role
        return role

    def add_user(self, name: str, companies: list[str], subject: str | None = None) -> User:
        with self.seed():
            user = User.objects.create(
                tenant_id=self.tenant_id, idp_subject=subject, email=f"{name}@example.invalid", display_name=name
            )
            for i, c in enumerate(companies):
                Membership.objects.create(
                    tenant_id=self.tenant_id, company_id=self.companies[c], user=user, is_default=i == 0
                )
        self.users[name] = user
        return user

    def assign(self, user: str, role: str, company: str, scope: dict[str, list[str]] | None = None) -> RoleAssignment:
        with self.seed():
            a = RoleAssignment.objects.create(
                tenant_id=self.tenant_id,
                company_id=self.companies[company],
                user=self.users[user],
                role=self.roles[role],
                scope=scope or {},
            )
            PolicyEngine.bump_version(self.tenant_id)
        return a


def make_world(companies: tuple[tuple[str, str], ...] = (("ALFA", "900000001"), ("BETA", "900000002"))) -> World:
    world = World(ids.new_id())
    with unit_of_work(TenantContext(world.tenant_id, frozenset(), world.seed_principal, PrincipalType.USER)):
        Tenant.objects.create(id=world.tenant_id, tenant_id=world.tenant_id, name="Tenant sintético")
    for name, nit in companies:
        world.add_company(name, nit)
    return world
