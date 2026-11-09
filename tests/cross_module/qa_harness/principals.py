"""The P22 principal matrix: 2 tenants x 3 companies x 7 principal kinds (prompt P22 §4).

Permissions are evaluated from the frozen role templates in ``contracts/permissions/catalog.yaml``
(fnmatch patterns over ``<module>.<resource>.<action>``; deny wins). Delegated principals:

* ``ai_delegated``  = agente_ia template acting for the salesperson: grants are the intersection of
  the delegating user's effective grants and the template's ``delegation_ceiling``, minus the
  template's denies; classification ceiling = min(user, template).
* ``mcp_limited``   = MCP client acting for the admin with explicit OAuth scopes (``scopes``):
  effective = user's grants ∩ scopes.

All identifiers are deterministic UUIDs derived from a namespace (stable across runs, synthetic).
"""

from __future__ import annotations

import enum
import fnmatch
import functools
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .openapi import CLASS_RANK, CLASSIFICATION_ORDER
from .paths import permission_catalog

NAMESPACE = uuid.UUID("6f2c7a4e-2d1b-5c3e-9a8f-0b1d2e3f4a5b")


def synthetic_id(*parts: str) -> uuid.UUID:
    """Deterministic synthetic UUID (uuid5) for harness fixtures."""
    return uuid.uuid5(NAMESPACE, "/".join(parts))


class Kind(enum.StrEnum):
    ADMIN = "admin"
    SALESPERSON = "salesperson"
    WAREHOUSE = "warehouse"
    PAYROLL = "payroll"
    AUDITOR = "auditor"
    AI_DELEGATED = "ai_delegated"
    MCP_LIMITED = "mcp_limited"


ROLE_OF_KIND: dict[Kind, str] = {
    Kind.ADMIN: "administrador",
    Kind.SALESPERSON: "vendedor",
    Kind.WAREHOUSE: "jefe_bodega",
    Kind.PAYROLL: "analista_nomina",
    Kind.AUDITOR: "auditor",
    Kind.AI_DELEGATED: "agente_ia",
    Kind.MCP_LIMITED: "administrador",
}

MCP_SCOPES: tuple[str, ...] = ("inventory.*.read", "sales.party.read", "sales.invoice.read")

# Row scopes (resource keys ``<module>.<resource>``) restricted beyond company membership.
OWNER_SCOPED_RESOURCES = frozenset({"sales.party", "sales.quote", "sales.order", "crm.lead", "crm.opportunity"})
WAREHOUSE_SCOPED_MODULES = frozenset({"inventory"})


@dataclass(frozen=True, slots=True)
class Role:
    key: str
    grants: tuple[str, ...]
    denies: tuple[str, ...]
    max_classification: str
    delegation_ceiling: tuple[str, ...] = ()
    delegated_only: bool = False


@functools.cache
def roles() -> dict[str, Role]:
    out: dict[str, Role] = {}
    for r in permission_catalog()["roles"]:
        out[r["key"]] = Role(
            key=r["key"],
            grants=tuple(r.get("grants") or ()),
            denies=tuple(r.get("denies") or ()),
            max_classification=r.get("max_classification", "internal"),
            delegation_ceiling=tuple(r.get("delegation_ceiling") or ()),
            delegated_only=bool(r.get("delegated_only", False)),
        )
    return out


def _match(code: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(code, p) for p in patterns)


def role_allows(role: Role, code: str) -> bool:
    return _match(code, role.grants) and not _match(code, role.denies)


def min_class(a: str, b: str) -> str:
    return a if CLASS_RANK[a] <= CLASS_RANK[b] else b


@dataclass(frozen=True, slots=True)
class Tenant:
    key: str
    id: uuid.UUID
    company_ids: tuple[uuid.UUID, uuid.UUID, uuid.UUID]

    def warehouse(self, company_index: int, n: int = 1) -> uuid.UUID:
        return synthetic_id(self.key, f"company{company_index}", f"warehouse{n}")


@dataclass(frozen=True, slots=True)
class Principal:
    key: str
    kind: Kind
    tenant: Tenant
    id: uuid.UUID
    role: Role
    company_ids: frozenset[uuid.UUID]
    max_classification: str
    on_behalf_of: Principal | None = None
    scopes: tuple[str, ...] = ()
    owner_scoped: bool = False
    warehouse_ids: frozenset[uuid.UUID] | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def principal_type(self) -> str:
        if self.kind is Kind.AI_DELEGATED:
            return "ai_agent"
        if self.kind is Kind.MCP_LIMITED:
            return "mcp_client"
        return "user"

    @property
    def channel(self) -> str:
        return {"ai_agent": "ai", "mcp_client": "mcp"}.get(self.principal_type, "api")

    def allows(self, code: str) -> bool:
        """Effective permission (deny-by-default)."""
        if not code:
            return False
        if self.kind is Kind.AI_DELEGATED:
            assert self.on_behalf_of is not None
            return (
                self.on_behalf_of.allows(code)
                and _match(code, self.role.delegation_ceiling)
                and not _match(code, self.role.denies)
            )
        if self.kind is Kind.MCP_LIMITED:
            assert self.on_behalf_of is not None
            return self.on_behalf_of.allows(code) and _match(code, self.scopes)
        return role_allows(self.role, code)

    def tenant_context_kwargs(self) -> dict[str, Any]:
        """Arguments for ``kernel.tenancy.TenantContext`` (kept import-free)."""
        return {
            "tenant_id": self.tenant.id,
            "company_ids": frozenset(self.company_ids),
            "principal_id": self.id,
            "principal_type": self.principal_type,
            "on_behalf_of": self.on_behalf_of.id if self.on_behalf_of else None,
            "channel": self.channel,
        }


def make_tenant(key: str) -> Tenant:
    return Tenant(
        key=key,
        id=synthetic_id(key),
        company_ids=(synthetic_id(key, "company1"), synthetic_id(key, "company2"), synthetic_id(key, "company3")),
    )


def principals_for(tenant: Tenant) -> list[Principal]:
    r = roles()
    all_c = frozenset(tenant.company_ids)
    c1 = tenant.company_ids[0]

    def mk(kind: Kind, companies: frozenset[uuid.UUID], **kw: Any) -> Principal:
        role = r[ROLE_OF_KIND[kind]]
        return Principal(
            key=f"{tenant.key}:{kind.value}",
            kind=kind,
            tenant=tenant,
            id=synthetic_id(tenant.key, "principal", kind.value),
            role=role,
            company_ids=companies,
            max_classification=kw.pop("max_classification", role.max_classification),
            **kw,
        )

    admin = mk(Kind.ADMIN, all_c)
    sales = mk(Kind.SALESPERSON, frozenset(tenant.company_ids[:2]), owner_scoped=True)
    wh = mk(Kind.WAREHOUSE, frozenset({c1}), warehouse_ids=frozenset({tenant.warehouse(1)}))
    payroll = mk(Kind.PAYROLL, frozenset({c1}))
    auditor = mk(Kind.AUDITOR, all_c)
    ai = mk(
        Kind.AI_DELEGATED,
        sales.company_ids,
        on_behalf_of=sales,
        owner_scoped=True,
        max_classification=min_class(sales.max_classification, r["agente_ia"].max_classification),
    )
    mcp = mk(
        Kind.MCP_LIMITED,
        frozenset({c1}),
        on_behalf_of=admin,
        scopes=MCP_SCOPES,
        max_classification=min_class(admin.max_classification, "internal"),
    )
    return [admin, sales, wh, payroll, auditor, ai, mcp]


@dataclass(frozen=True, slots=True)
class Matrix:
    tenants: tuple[Tenant, Tenant]
    principals: tuple[Principal, ...]

    def of(self, tenant_key: str, kind: Kind) -> Principal:
        for p in self.principals:
            if p.tenant.key == tenant_key and p.kind is kind:
                return p
        raise KeyError((tenant_key, kind))

    def other_tenant(self, tenant: Tenant) -> Tenant:
        return self.tenants[1] if tenant == self.tenants[0] else self.tenants[0]


@functools.cache
def matrix() -> Matrix:
    t1, t2 = make_tenant("T1"), make_tenant("T2")
    return Matrix(tenants=(t1, t2), principals=tuple(principals_for(t1) + principals_for(t2)))


__all__ = [
    "CLASSIFICATION_ORDER",
    "MCP_SCOPES",
    "Kind",
    "Matrix",
    "Principal",
    "Role",
    "Tenant",
    "matrix",
    "role_allows",
    "roles",
    "synthetic_id",
]
