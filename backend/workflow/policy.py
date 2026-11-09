"""Access to the P04 ``PolicyService`` (fails closed when it is not registered)."""

from __future__ import annotations

from typing import Any

from kernel import services
from kernel.interfaces.policy import FieldMask, PolicyService
from kernel.tenancy import TenantContext


def policy() -> PolicyService:
    return services.get(PolicyService)


def mask_fields(ctx: TenantContext, resource_type: str, data: dict[str, Any], always: set[str] | None = None) -> dict[str, Any]:
    """Drop fields the principal may not read (``field_mask``); ``always`` are kept (ids, status)."""
    mask: FieldMask = policy().field_mask(ctx, resource_type)
    keep = set(always or ()) | set(mask.readable)
    return {k: v for k, v in data.items() if k in keep}
