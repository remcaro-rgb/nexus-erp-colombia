"""Test helpers for tenancy binding and RLS behaviour."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterable, Iterator

from django.db import transaction

from kernel import ids, tenancy
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


@contextlib.contextmanager
def bound(ctx: TenantContext, using: str = "default") -> Iterator[TenantContext]:
    """Test-side transaction bound to ``ctx`` (``SET LOCAL``): direct ORM / SQL reads and
    writes of RLS tables see exactly what that tenant would (ADR-003)."""
    with transaction.atomic(using=using), tenancy.activate(ctx):
        tenancy.bind(ctx, using)
        yield ctx


@contextlib.contextmanager
def platform(job: str = "outbox", using: str = "default") -> Iterator[None]:
    """Test-side transaction with the cross-tenant platform binding (``kernel.tenancy.bind_platform``)."""
    with transaction.atomic(using=using):
        tenancy.bind_platform(job, using)
        yield
