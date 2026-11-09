"""PolicyService (owner P04, ADR-004): the single authorization path.

Contract: deny by default; same checks for UI, API, exports, reports, AI and
MCP; decisions are cached per principal keyed by ``policy_version``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kernel.tenancy import TenantContext


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str = ""
    obligations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FieldMask:
    readable: frozenset[str]
    writable: frozenset[str]


@runtime_checkable
class PolicyService(Protocol):
    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        """``action`` uses the catalog grammar ``<module>.<resource>.<action>``."""
        ...

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        """Raise ``PermissionDenied`` unless allowed."""
        ...

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        """Apply row filters (company, branch, warehouse, cost center, owner…)."""
        ...

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        """Readable/writable fields for the principal."""
        ...

    def policy_version(self, ctx: TenantContext) -> int:
        """Tenant policy version (bumped on any role/policy change)."""
        ...
