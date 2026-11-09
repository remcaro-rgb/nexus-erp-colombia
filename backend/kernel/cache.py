"""Context-scoped cache keys (UI task matrix P01).

Keys always include tenant, company, period and a permission hash plus a
per-principal generation, so a role or company switch can never serve another
context's data. ``on_context_switch``/``on_logout`` bump the generation.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from django.core.cache import cache

from kernel.tenancy import TenantContext

KEY_PREFIX = "erp:v1"


def permission_hash(roles: Iterable[str], policy_version: int | str = 0) -> str:
    """Stable hash of the principal's effective roles/permissions + policy version."""
    material = "|".join(sorted(set(roles))) + f"#{policy_version}"
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def _generation_key(ctx: TenantContext) -> str:
    return f"{KEY_PREFIX}:gen:{ctx.tenant_id}:{ctx.principal_id}"


def generation(ctx: TenantContext) -> int:
    value = cache.get(_generation_key(ctx))
    return int(value) if value is not None else 0


def ui_cache_key(
    namespace: str,
    ctx: TenantContext,
    *,
    company_id: uuid.UUID,
    period_id: uuid.UUID | str | None,
    perm_hash: str,
    extra: str = "",
) -> str:
    ctx.require_company(company_id)
    parts = [
        namespace,
        str(ctx.tenant_id),
        str(company_id),
        str(period_id or "-"),
        perm_hash,
        str(ctx.principal_id),
        str(generation(ctx)),
        extra,
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return f"{KEY_PREFIX}:{namespace}:{ctx.tenant_id}:{digest}"


def invalidate_principal(ctx: TenantContext) -> int:
    key = _generation_key(ctx)
    try:
        return int(cache.incr(key))
    except ValueError:
        cache.set(key, 1, timeout=None)
        return 1


def on_context_switch(ctx: TenantContext) -> int:
    """Call when the user switches company/period/role."""
    return invalidate_principal(ctx)


def on_logout(ctx: TenantContext) -> int:
    return invalidate_principal(ctx)
