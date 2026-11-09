"""Who holds a role / who is whose manager (the org data lives in IAM P04 and HR P13).

``ApproverDirectory`` is resolved through ``kernel.services``; until P04/P13
register theirs, :class:`StaticDirectory` (explicit, deny-by-default) is used
(CCR P07-2).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Protocol, runtime_checkable

from kernel import services
from kernel.tenancy import TenantContext


@runtime_checkable
class ApproverDirectory(Protocol):
    def members_of_role(self, ctx: TenantContext, company_id: uuid.UUID, role: str) -> set[uuid.UUID]: ...

    def manager_of(self, ctx: TenantContext, company_id: uuid.UUID, principal_id: uuid.UUID) -> uuid.UUID | None: ...


class StaticDirectory:
    def __init__(self) -> None:
        self.roles: dict[tuple[uuid.UUID, str], set[uuid.UUID]] = defaultdict(set)
        self.managers: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}

    def add_role(self, company_id: uuid.UUID, role: str, principal_id: uuid.UUID) -> None:
        self.roles[(company_id, role)].add(principal_id)

    def set_manager(self, company_id: uuid.UUID, principal_id: uuid.UUID, manager_id: uuid.UUID) -> None:
        self.managers[(company_id, principal_id)] = manager_id

    def members_of_role(self, ctx: TenantContext, company_id: uuid.UUID, role: str) -> set[uuid.UUID]:
        return set(self.roles.get((company_id, role), set()))

    def manager_of(self, ctx: TenantContext, company_id: uuid.UUID, principal_id: uuid.UUID) -> uuid.UUID | None:
        return self.managers.get((company_id, principal_id))


_default = StaticDirectory()


def directory() -> ApproverDirectory:
    return services.optional(ApproverDirectory) or _default
