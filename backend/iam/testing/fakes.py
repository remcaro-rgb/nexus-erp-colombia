"""Test doubles published by IAM for other modules' unit tests (P04 "day 1" fake).

* :class:`AllowAllRecordingPolicy` — allows everything and records every check, for
  unit tests of other modules only. Cross-module and security tests MUST use the real
  :class:`iam.policy.engine.PolicyEngine`.
* :class:`ListableDimensions` — ``kernel.testing.fakes.FakeDimensions`` plus
  ``list_nodes`` (CCR P04-3) for cost-center scope tests until P05 is wired.
"""

from __future__ import annotations

import uuid
from typing import Any

from kernel.interfaces.dimensions import DimensionNode
from kernel.interfaces.policy import Decision, FieldMask
from kernel.tenancy import TenantContext
from kernel.testing.fakes import FakeDimensions


class AllowAllRecordingPolicy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID, str]] = []

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        self.calls.append(("authorize", ctx.principal_id, action))
        return Decision(True, "allow_all_fake")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        self.calls.append(("require", ctx.principal_id, action))

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        self.calls.append(("scope_queryset", ctx.principal_id, action))
        return queryset.filter(tenant_id=ctx.tenant_id)

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        self.calls.append(("field_mask", ctx.principal_id, resource_type))
        return FieldMask(frozenset({"*"}), frozenset({"*"}))

    def policy_version(self, ctx: TenantContext) -> int:
        return 1


class ListableDimensions(FakeDimensions):
    def list_nodes(self, company_id: uuid.UUID, dimension: str) -> list[DimensionNode]:
        return [n for n in self.nodes.values() if n.company_id == company_id and n.dimension == dimension]
