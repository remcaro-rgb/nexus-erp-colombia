"""Synthetic multi-module world for the dataio tests (real PostgreSQL; all data SYNTHETIC, NOT LEGALLY VERIFIED).

Two companies (ALFA, BETA) of one tenant, the real ledger/inventory/IAM/treasury/sales/payroll/tax/purchasing
services, and a pattern-grant policy (deny by default) so tests can give a principal exactly the codes a role
would hold. Fixture rows (items, warehouses, parties, formulas) are seeded directly — the imports under test
always go through ``dataio.service``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.documents import DocumentRenderer
from kernel.interfaces.notifications import NotificationService
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import Inventory, StockService
from kernel.interfaces.workflow import WorkflowService
from kernel.errors import PermissionDenied
from kernel.tenancy import TenantContext
from kernel.testing.fakes import FakeAuditService, FakeDocumentRenderer, FakeNotificationService, FakeWorkflowService
from kernel.uow import run_in_unit_of_work

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)
ALL = ["*.*.*"]


class GrantPolicy:
    """Deny by default; grants are fnmatch patterns per principal (``inventory.*.*``)."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, list[str]] = {}
        self.version = 1

    def grant(self, principal: uuid.UUID, patterns: Iterable[str]) -> None:
        self.grants.setdefault(principal, []).extend(patterns)
        self.version += 1

    def revoke(self, principal: uuid.UUID, pattern: str) -> None:
        self.grants[principal] = [p for p in self.grants.get(principal, []) if p != pattern]
        self.version += 1

    def _ok(self, ctx: TenantContext, action: str) -> bool:
        pats = self.grants.get(ctx.principal_id, [])
        if any(p.startswith("!") and fnmatchcase(action, p[1:]) for p in pats):
            return False
        return any(not p.startswith("!") and fnmatchcase(action, p) for p in pats)

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        ok = self._ok(ctx, action)
        return Decision(ok, "granted" if ok else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self._ok(ctx, action):
            raise PermissionDenied(f"missing permission {action}", code="policy_denied")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self._ok(ctx, action):
            return queryset.none()
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        return FieldMask(frozenset(), frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


@dataclass
class DataWorld:
    tenant_id: uuid.UUID
    companies: dict[str, uuid.UUID]
    policy: GrantPolicy
    audit: FakeAuditService
    users: dict[str, uuid.UUID] = field(default_factory=dict)

    def ctx(self, user: str = "admin", companies: Iterable[str] = ("ALFA", "BETA")) -> TenantContext:
        if user not in self.users:
            self.users[user] = ids.new_id()
        return TenantContext(
            tenant_id=self.tenant_id,
            company_ids=frozenset(self.companies[c] for c in companies),
            principal_id=self.users[user],
            principal_type=tenancy.PrincipalType.USER,
        )

    def user(self, name: str, grants: Iterable[str], companies: Iterable[str] = ("ALFA", "BETA")) -> TenantContext:
        ctx = self.ctx(name, companies)
        self.policy.grant(ctx.principal_id, grants)
        return ctx

    def run(self, fn: Any, ctx: TenantContext | None = None) -> Any:
        return run_in_unit_of_work(fn, ctx or self.ctx())

    @contextlib.contextmanager
    def scope(self, ctx: TenantContext | None = None) -> Iterator[None]:
        with tenancy.scope(ctx or self.ctx()):
            yield


@contextlib.contextmanager
def dataio_services() -> Iterator[dict[str, Any]]:
    from inventory import registry as inv_registry
    from inventory.barcodes import registry as bc_registry
    from inventory.issues import registry as is_registry
    from inventory.stock.service import SERVICE
    from inventory.transformations import registry as tr_registry
    from django.core.cache import cache
    from ledger.registry import implementations as ledger_impls

    cache.clear()
    impls = ledger_impls()
    audit = FakeAuditService()
    policy = GrantPolicy()
    stack = [
        services.override(PostingService, impls[PostingService]),
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(AuditService, audit),
        services.override(PolicyService, policy),
        services.override(Inventory, SERVICE),
        services.override(StockService, SERVICE),
        services.override(NotificationService, FakeNotificationService()),
        services.override(WorkflowService, FakeWorkflowService()),
        services.override(DocumentRenderer, FakeDocumentRenderer()),
    ]
    for cm in stack:
        cm.__enter__()
    inv_registry.register_branch_reference()
    inv_registry.register_fields()
    bc_registry.register_fields()
    is_registry.register_fields()
    tr_registry.register_fields()
    try:
        with clock.frozen(NOW):
            yield {"audit": audit, "policy": policy}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)
        cache.clear()


ACCOUNTS = (
    ("110505", "Caja (SINTÉTICO)", "asset", "debit", "CASH"),
    ("143505", "Inventario (SINTÉTICO)", "asset", "debit", "INVENTORY"),
    ("519595", "Gastos diversos (SINTÉTICO)", "expense", "debit", "EXPENSE"),
    ("311595", "Saldos iniciales (SINTÉTICO)", "equity", "credit", "OPENING_BALANCE_EQUITY"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", "ROUNDING"),
)


def make_data_world(svc: dict[str, Any]) -> DataWorld:
    from iam.testing.world import make_world

    base = make_world()
    w = DataWorld(base.tenant_id, dict(base.companies), svc["policy"], svc["audit"])
    w.user("admin", ALL)
    for name in ("ALFA", "BETA"):
        seed_company(w, name)
    return w


def seed_company(w: DataWorld, name: str) -> None:
    from iam.models import Branch
    from inventory import masters
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination
    from sales.models import Party
    from workflow.models import Formula

    cid = w.companies[name]
    ctx = w.ctx()

    def fn(u: Any) -> None:
        setup.setup_company(u, cid)
        periods.ensure_year(u, cid, 2026)
        for code, label, typ, nature, role in ACCOUNTS:
            acc = Account.objects.create(tenant_id=w.tenant_id, company_id=cid, code=code, name=label, type=typ, nature=nature)
            AccountDetermination.objects.create(tenant_id=w.tenant_id, company_id=cid, role_code=role, account=acc)
        branch = Branch.objects.create(tenant_id=w.tenant_id, company_id=cid, code="BOG", name="Bogotá", is_head_office=True, valid_from=dt.date(2026, 1, 1))
        masters.create_uom(u, cid, {"code": "UND", "name": "Unidad", "precision": 0})
        masters.create_uom(u, cid, {"code": "KG", "name": "Kilogramo", "precision": 3})
        masters.create_warehouse(u, cid, {"code": "PRINCIPAL", "name": "Bodega principal", "branch_id": str(branch.id)})
        for code, uom in (("ARROZ", "UND"), ("HARINA", "KG"), ("LEVADURA", "KG"), ("PAN", "UND"), ("RES", "KG"), ("LOMO", "KG"), ("MOLIDA", "KG")):
            masters.create_item(u, cid, {"code": code, "name": f"{code} (SINTÉTICO)", "base_uom": uom})
        Party.objects.create(
            tenant_id=w.tenant_id, company_id=cid, code="PRV-001", legal_name="Proveedor sintético S.A.S.", nit="900123456", dv="8", person_type="JURIDICA", roles=["vendor"]
        )
        for domain, code in (("pricing", "PRECIO_MAYOR"), ("payroll", "BONO_FORM")):
            Formula.objects.create(
                tenant_id=w.tenant_id,
                company_id=cid,
                domain=domain,
                code=code,
                expression="1",
                result_type="money",
                status="ACTIVE",
                author_id=w.users["admin"],
                effective_from=dt.date(2026, 1, 1),
            )

    run_in_unit_of_work(fn, ctx)
