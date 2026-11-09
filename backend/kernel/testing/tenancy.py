"""Test helpers for tenancy binding and RLS behaviour."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from kernel import ids
from kernel.tenancy import Channel, PrincipalType, TenantContext


def make_context(
    tenant_id: uuid.UUID | None = None,
    company_ids: Iterable[uuid.UUID] | None = None,
    principal_id: uuid.UUID | None = None,
    principal_type: PrincipalType = PrincipalType.USER,
    channel: Channel = Channel.API,
    on_behalf_of: uuid.UUID | None = None,
) -> TenantContext:
    """Synthetic context with fresh UUIDv7 ids (one company unless given)."""
    return TenantContext(
        tenant_id=tenant_id or ids.new_id(),
        company_ids=frozenset(company_ids if company_ids is not None else [ids.new_id()]),
        principal_id=principal_id or ids.new_id(),
        principal_type=principal_type,
        channel=channel,
        on_behalf_of=on_behalf_of,
    )


def first_company(ctx: TenantContext) -> uuid.UUID:
    return sorted(ctx.company_ids)[0]
