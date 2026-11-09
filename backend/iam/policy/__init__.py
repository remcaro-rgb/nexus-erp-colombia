"""Single authorization path (ADR-004).

Use :func:`authorize`, :func:`require`, :func:`scope_queryset`, :func:`field_mask`
and :func:`secure_rows` (exports, reports, AI, MCP). They delegate to the
process-wide :class:`iam.policy.engine.PolicyEngine`.
"""

from __future__ import annotations

from typing import Any

from kernel.interfaces.policy import Decision, FieldMask
from kernel.tenancy import TenantContext


def _engine() -> Any:
    from iam.policy.engine import get_engine

    return get_engine()


def authorize(ctx: TenantContext, action: str, resource: Any = None) -> Decision:
    return _engine().authorize(ctx, action, resource)  # type: ignore[no-any-return]


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    _engine().require(ctx, action, resource)


def scope_queryset(ctx: TenantContext, action: str, queryset: Any) -> Any:
    return _engine().scope_queryset(ctx, action, queryset)


def field_mask(ctx: TenantContext, resource_type: str) -> FieldMask:
    return _engine().field_mask(ctx, resource_type)  # type: ignore[no-any-return]


def secure_rows(ctx: TenantContext, action: str, resource_type: str, rows: Any) -> list[dict[str, Any]]:
    return _engine().secure_rows(ctx, action, resource_type, rows)  # type: ignore[no-any-return]
