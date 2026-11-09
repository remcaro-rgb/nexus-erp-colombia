"""Tenancy context and transaction-local binding (ADR-003).

``bind(ctx)`` runs ``set_config(name, value, true)`` (= ``SET LOCAL``) for
``app.tenant_id``, ``app.company_ids``, ``app.principal_id``,
``app.principal_type``, ``app.on_behalf_of`` and ``app.channel``. Values die at
COMMIT/ROLLBACK, so a pooled connection never leaks a binding to the next
transaction. RLS policies (P04) read them with
``current_setting('app.tenant_id', true)``; an unbound query returns no rows.
"""

from __future__ import annotations

import contextlib
import contextvars
import enum
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from django.db import connections, transaction

from kernel.db import PLATFORM_JOB_SETTING
from kernel.errors import TenancyNotBound, ValidationFailed

_log = structlog.get_logger("kernel.tenancy")


class PrincipalType(enum.StrEnum):
    USER = "user"
    SERVICE = "service"
    AI_AGENT = "ai_agent"
    MCP_CLIENT = "mcp_client"


class Channel(enum.StrEnum):
    UI = "ui"
    API = "api"
    AI = "ai"
    MCP = "mcp"
    IMPORT = "import"
    JOB = "job"


SETTING_NAMES = (
    "app.tenant_id",
    "app.company_ids",
    "app.principal_id",
    "app.principal_type",
    "app.on_behalf_of",
    "app.channel",
)


def _as_uuid(value: Any, name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationFailed(f"{name} must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Who acts, for which tenant and companies, through which channel."""

    tenant_id: uuid.UUID
    company_ids: frozenset[uuid.UUID]
    principal_id: uuid.UUID
    principal_type: PrincipalType = PrincipalType.USER
    on_behalf_of: uuid.UUID | None = None
    channel: Channel = Channel.API
    correlation_id: uuid.UUID | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _as_uuid(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "principal_id", _as_uuid(self.principal_id, "principal_id"))
        object.__setattr__(
            self,
            "company_ids",
            frozenset(_as_uuid(c, "company_ids") for c in self.company_ids),
        )
        object.__setattr__(self, "principal_type", PrincipalType(self.principal_type))
        object.__setattr__(self, "channel", Channel(self.channel))
        if self.on_behalf_of is not None:
            object.__setattr__(self, "on_behalf_of", _as_uuid(self.on_behalf_of, "on_behalf_of"))
        if self.principal_type in (PrincipalType.AI_AGENT, PrincipalType.MCP_CLIENT) and (self.on_behalf_of is None):
            raise ValidationFailed("AI agent and MCP principals must act on behalf of a user")
        if self.correlation_id is not None:
            object.__setattr__(self, "correlation_id", _as_uuid(self.correlation_id, "correlation_id"))

    def can_access_company(self, company_id: uuid.UUID) -> bool:
        return company_id in self.company_ids

    def require_company(self, company_id: uuid.UUID) -> uuid.UUID:
        cid = _as_uuid(company_id, "company_id")
        if cid not in self.company_ids:
            # Same message for missing and foreign companies: no existence oracle.
            raise ValidationFailed("company not available in the current context")
        return cid

    def settings_values(self) -> dict[str, str]:
        return {
            "app.tenant_id": str(self.tenant_id),
            "app.company_ids": "{" + ",".join(sorted(str(c) for c in self.company_ids)) + "}",
            "app.principal_id": str(self.principal_id),
            "app.principal_type": self.principal_type.value,
            "app.on_behalf_of": str(self.on_behalf_of) if self.on_behalf_of else "",
            "app.channel": self.channel.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "company_ids": sorted(str(c) for c in self.company_ids),
            "principal_id": str(self.principal_id),
            "principal_type": self.principal_type.value,
            "on_behalf_of": str(self.on_behalf_of) if self.on_behalf_of else None,
            "channel": self.channel.value,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TenantContext:
        return cls(
            tenant_id=data["tenant_id"],
            company_ids=frozenset(data.get("company_ids") or ()),
            principal_id=data["principal_id"],
            principal_type=PrincipalType(data.get("principal_type", "user")),
            on_behalf_of=data.get("on_behalf_of"),
            channel=Channel(data.get("channel", "api")),
            correlation_id=data.get("correlation_id"),
        )


_current: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar("erp_tenant_context", default=None)


def current() -> TenantContext | None:
    return _current.get()


def require_current() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise TenancyNotBound("no tenant context for this request/task")
    return ctx


@contextlib.contextmanager
def activate(ctx: TenantContext | None) -> Iterator[TenantContext | None]:
    """Make ``ctx`` the current context (contextvar) for the enclosed block."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def bind(ctx: TenantContext, using: str = "default") -> None:
    """``SET LOCAL`` the context on the current transaction of ``using``.

    Must be called inside ``transaction.atomic`` (values are transaction-local).
    """
    conn = connections[using]
    if not conn.in_atomic_block:
        raise TenancyNotBound("bind() must run inside a transaction (SET LOCAL semantics)")
    values = ctx.settings_values()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT " + ", ".join("set_config(%s, %s, true)" for _ in values),
            [x for pair in values.items() for x in pair],
        )


#: Cross-tenant platform jobs that have their own RLS policy (``kernel.db.platform_rls_policy_sql``).
#: ``outbox``: the outbox relay and the dead-letter worker, on kernel_outbox / kernel_dead_letter only.
PLATFORM_JOBS = frozenset({"outbox"})


def bind_platform(job: str, using: str = "default") -> None:
    """``SET LOCAL app.platform_job = job`` for a cross-tenant platform job (ADR-003).

    Only the tables whose migration installed a ``<table>_platform_<job>`` policy become
    visible across tenants (kernel_outbox, kernel_dead_letter for ``outbox``); every other
    tenant table still needs :func:`bind`. Transaction-local like :func:`bind`; every use is
    logged (ADR-003: platform access is audited).
    """
    if job not in PLATFORM_JOBS:
        raise ValidationFailed(f"unknown platform job: {job!r}")
    conn = connections[using]
    if not conn.in_atomic_block:
        raise TenancyNotBound("bind_platform() must run inside a transaction (SET LOCAL semantics)")
    with conn.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, true)", [PLATFORM_JOB_SETTING, job])
    _log.debug("tenancy.platform_bind", job=job)


def _require_atomic(using: str, name: str) -> Any:
    conn = connections[using]
    if not conn.in_atomic_block:
        raise TenancyNotBound(f"{name}() must run inside a transaction (transaction-level advisory lock)")
    return conn


def advisory_xact_lock(using: str, key: int) -> None:
    """``pg_advisory_xact_lock(key)``: wait for the transaction-level lock ``key`` (released at COMMIT/ROLLBACK).

    Kept here because this module owns the tenancy/locking raw SQL (ADR-004 allow-list).
    """
    conn = _require_atomic(using, "advisory_xact_lock")
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [int(key)])


def try_advisory_xact_lock(using: str, key: int) -> bool:
    """``pg_try_advisory_xact_lock(key)``: take the transaction-level lock without waiting; ``True`` if taken."""
    conn = _require_atomic(using, "try_advisory_xact_lock")
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", [int(key)])
        row = cur.fetchone()
    return bool(row[0]) if row else False


#: ``(relrowsecurity, relforcerowsecurity, has_policy)`` of one table (RLS self-checks and tests).
RLS_STATUS_QUERY = (
    "SELECT c.relrowsecurity, c.relforcerowsecurity, EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) "
    "FROM pg_class c WHERE c.oid = to_regclass(%s)"
)


#: ADR-003: the application role must be neither SUPERUSER nor BYPASSRLS, otherwise RLS is not enforced.
#: Read by kernel.boot on every new connection; kept here because this module owns RLS raw SQL (ADR-004).
DB_ROLE_QUERY = "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"


def fetch_db_role(cursor: Any) -> Any:
    """``(rolname, rolsuper, rolbypassrls)`` of the current role on ``cursor`` (``None`` if unknown)."""
    cursor.execute(DB_ROLE_QUERY)
    return cursor.fetchone()


def fetch_connection_db_role(conn: Any) -> Any:
    """Same as :func:`fetch_db_role` on a fresh cursor of ``conn`` (a Django connection wrapper)."""
    with conn.cursor() as cur:
        return fetch_db_role(cur)


def bound_settings(using: str = "default") -> dict[str, str]:
    """Current values of the ``app.*`` settings (empty string when unbound)."""
    with connections[using].cursor() as cur:
        cur.execute(
            "SELECT " + ", ".join("coalesce(current_setting(%s, true), '')" for _ in SETTING_NAMES),
            list(SETTING_NAMES),
        )
        row = cur.fetchone()
    return dict(zip(SETTING_NAMES, row or ("",) * len(SETTING_NAMES), strict=True))


@contextlib.contextmanager
def scope(ctx: TenantContext | None = None, using: str = "default") -> Iterator[TenantContext]:
    """Read scope: open a transaction (or join the current one) and bind ``ctx``."""
    effective = ctx or require_current()
    with activate(effective), transaction.atomic(using=using):
        bind(effective, using)
        yield effective


ContextResolver = Callable[[Any], TenantContext | None]


def _resolver() -> ContextResolver | None:
    from django.conf import settings
    from django.utils.module_loading import import_string

    path = getattr(settings, "ERP_TENANT_CONTEXT_RESOLVER", None)
    return import_string(path) if path else None


class TenancyMiddleware:
    """Activates the request's :class:`TenantContext` for the request lifetime.

    The context comes from ``request.erp_context`` (set by the IAM
    authentication layer, P04) or the ``ERP_TENANT_CONTEXT_RESOLVER`` callable.
    It is never derived from client headers alone. Without a context nothing is
    bound, so RLS-protected tables return no rows.
    """

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        ctx = getattr(request, "erp_context", None)
        if ctx is None:
            resolver = _resolver()
            ctx = resolver(request) if resolver else None
        with activate(ctx):
            return self.get_response(request)
