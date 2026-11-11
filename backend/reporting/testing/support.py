"""Test support for the reporting module (synthetic data only, DEV).

* :class:`ScopedPolicy` — deny-by-default ``PolicyService`` double: per-principal grants (``module.*`` patterns),
  readable protected fields (field policy) and an optional branch row scope (``scope_queryset`` keeps rows whose
  ``branch_id`` is in the principal's branches — the P04 role scope by branch).
* :func:`reporting_world` — one tenant, two companies, the REAL P05 ledger (posting engine, periods, dimension
  masters + effective-dated closure), synthetic PUC-like accounts labelled SINTÉTICO, real IAM branches, P09
  warehouses and P12 POS configurations (composite FKs to the branch).
* :class:`ApiClient` — DRF test client bound to a principal through ``ERP_TENANT_CONTEXT_RESOLVER``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel import clock, ids, services
from kernel.errors import PermissionDenied
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.tenancy import TenantContext
from kernel.testing.fakes import FakeAuditService
from kernel.testing.tenancy import bound, make_context
from kernel.uow import UnitOfWork, run_in_unit_of_work

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá
TODAY = dt.date(2026, 11, 9)
D = Decimal

ANALYST = (
    "reporting.*",
    "documents.*",
    "ledger.entry.read",
    "ledger.dimension.read",
    "ledger.dimension.create",
    "ledger.dimension.update",
    "treasury.receipt.read",
    "payroll.payslip.read",
)
ACCOUNTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("110505", "Caja general (SINTÉTICO)", "asset", "debit", "CASH"),
    ("111005", "Bancos (SINTÉTICO)", "asset", "debit", "BANK"),
    ("143505", "Inventario mercancías (SINTÉTICO)", "asset", "debit", "INVENTORY"),
    ("413505", "Ventas de mercancías (SINTÉTICO)", "income", "credit", "SALES"),
    ("613505", "Costo de ventas (SINTÉTICO)", "expense", "debit", "COGS"),
    ("519595", "Gastos diversos (SINTÉTICO)", "expense", "debit", "EXPENSE"),
)


class ScopedPolicy:
    """Deny by default; grants (``module.*`` patterns), readable protected fields and branch row scopes."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, set[str]] = {}
        self.fields: dict[uuid.UUID, set[str]] = {}
        self.branches: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.version = 1

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.setdefault(principal, set()).update(actions)
        self.version += 1

    def revoke(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.setdefault(principal, set()).difference_update(actions)
        self.version += 1

    def _allowed(self, principal: uuid.UUID, action: str) -> bool:
        mine = self.grants.get(principal, set())
        return action in mine or any(g.endswith(".*") and action.startswith(g[:-1]) for g in mine)

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        allowed = self._allowed(ctx.on_behalf_of or ctx.principal_id, action) and self._allowed(
            ctx.principal_id, action
        )
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self.authorize(ctx, action, resource).allowed:
            raise PermissionDenied(f"missing permission {action}", code="policy_denied")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()
        qs = queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))
        scope = self.branches.get(ctx.principal_id)
        if scope is not None and any(f.name == "branch_id" for f in queryset.model._meta.get_fields()):
            qs = qs.filter(branch_id__in=list(scope))
        return qs

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        return FieldMask(frozenset(self.fields.get(ctx.principal_id, set())), frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


@dataclass
class World:
    policy: ScopedPolicy
    tenant_id: uuid.UUID = field(default_factory=ids.new_id)
    company: uuid.UUID = field(default_factory=ids.new_id)
    other_company: uuid.UUID = field(default_factory=ids.new_id)
    principals: dict[str, TenantContext] = field(default_factory=dict)
    accounts: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)

    def user(self, name: str, grants: Iterable[str], *, companies: list[uuid.UUID] | None = None) -> TenantContext:
        ctx = make_context(tenant_id=self.tenant_id, company_ids=companies or [self.company])
        self.policy.grant(ctx.principal_id, *grants)
        self.principals[name] = ctx
        return ctx

    def ctx(self, name: str) -> TenantContext:
        return self.principals[name]

    def run(self, name: str, fn: Callable[[UnitOfWork], Any]) -> Any:
        return run_in_unit_of_work(fn, self.ctx(name))

    @contextmanager
    def scope(self, name: str = "admin") -> Iterator[TenantContext]:
        with bound(self.ctx(name)) as ctx:
            yield ctx

    # ------------------------------------------------------------------ masters
    def branch(self, code: str, *, head: bool = False) -> Any:
        from iam.models import Branch

        def fn(u: UnitOfWork) -> Any:
            return Branch.objects.create(
                tenant_id=self.tenant_id, company_id=self.company, code=code, name=f"Sucursal {code} (SINTÉTICA)",
                is_head_office=head,
            )  # fmt: skip

        return self.run("admin", fn)

    def warehouse(self, code: str, branch: Any) -> Any:
        from inventory.models import Warehouse

        def fn(u: UnitOfWork) -> Any:
            return Warehouse.objects.create(
                tenant_id=self.tenant_id, company_id=self.company, code=code, name=f"Bodega {code} (SINTÉTICA)",
                branch_id=branch.id,
            )  # fmt: skip

        return self.run("admin", fn)

    def pos_config(self, name: str, branch: Any, warehouse: Any) -> Any:
        from pos.models import PosConfig

        def fn(u: UnitOfWork) -> Any:
            return PosConfig.objects.create(
                tenant_id=self.tenant_id, company_id=self.company, name=name, branch_id=branch.id,
                warehouse_id=warehouse.id,
            )  # fmt: skip

        return self.run("admin", fn)

    def node(self, dimension: str, code: str, node_type: str, parent: Any = None) -> Any:
        from ledger.dimensions import service as dims

        data = dims.NodeInput(
            code=code,
            description=f"{code} (SINTÉTICO)",
            node_type=node_type,
            parent_id=str(parent.id) if parent is not None else None,
            valid_from="2026-01-01",
        )
        return self.run("admin", lambda u: dims.create_node(u, self.company, dimension, data))

    def move(self, node: Any, new_parent: Any) -> Any:
        from ledger.dimensions import service as dims
        from ledger.models import DimensionNode

        def fn(u: UnitOfWork) -> Any:
            fresh = DimensionNode.objects.get(id=node.id)
            return dims.move_node(u, fresh, str(new_parent.id))

        return self.run("admin", fn)

    # ------------------------------------------------------------------ postings
    def post(
        self,
        source_type: str,
        number: str,
        amount: str,
        *,
        debit_role: str = "CASH",
        credit_role: str = "SALES",
        day: dt.date = TODAY,
        branch: Any = None,
        warehouse: Any = None,
        dims: DimensionValues | None = None,
    ) -> Any:
        """A balanced synthetic entry: Dr ``debit_role`` / Cr ``credit_role`` with branch/warehouse/dimension slots."""
        value = D(amount)
        slots = dims or DimensionValues()
        wid = warehouse.id if warehouse is not None else None
        request = PostingRequest(
            company_id=self.company,
            posting_date=day,
            source=DocumentRef(source_type, ids.new_id(), number),
            purpose="test",
            lines=(
                PostingLine(debit_role, "DEBIT", value, warehouse_id=wid, dimensions=slots),
                PostingLine(credit_role, "CREDIT", value, warehouse_id=wid, dimensions=slots),
            ),
            branch_id=branch.id if branch is not None else None,
        )
        return self.run("admin", lambda u: services.get(PostingService).post(u, request))


def _companies(w: World) -> None:
    from iam.models import Company
    from kernel.uow import unit_of_work

    for n, cid in enumerate((w.company, w.other_company)):
        ctx = make_context(tenant_id=w.tenant_id, company_ids=[cid])
        with unit_of_work(ctx):
            Company.objects.create(
                id=cid, tenant_id=w.tenant_id, code=f"REP{n}", nit=f"90000002{n}", dv="0",
                legal_name=f"Empresa reportes {n} S.A.S. (sintética)",
            )  # fmt: skip


def _boot(w: World) -> None:
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination

    def boot(u: UnitOfWork) -> None:
        setup.setup_company(u, w.company)
        periods.ensure_year(u, w.company, 2026)
        for code, name, typ, nature, role in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=w.tenant_id, company_id=w.company, code=code, name=name, type=typ, nature=nature
            )
            w.accounts[code] = acc
            AccountDetermination.objects.create(
                tenant_id=w.tenant_id, company_id=w.company, role_code=role, account=acc
            )

    w.run("admin", boot)


@contextmanager
def reporting_world() -> Iterator[World]:
    from ledger.registry import implementations

    policy = ScopedPolicy()
    ledger = implementations()
    overrides: list[tuple[Callable[..., Any], Any]] = [
        (PolicyService, policy),
        (PostingService, ledger[PostingService]),
        (PeriodService, ledger[PeriodService]),
        (Dimensions, ledger[Dimensions]),
        (AuditService, FakeAuditService()),
    ]
    with ExitStack() as stack:
        stack.enter_context(clock.frozen(NOW))
        for interface, impl in overrides:
            stack.enter_context(services.override(interface, impl))
        w = World(policy)
        _companies(w)
        w.user("admin", (*ANALYST, "ledger.*", "iam.*", "inventory.*", "pos.*"))
        w.user("analyst", ANALYST)
        w.user("outsider", ())
        _boot(w)
        yield w


# --------------------------------------------------------------------------- API client

_contexts: dict[str, TenantContext] = {}


def resolve_context(request: Any) -> TenantContext | None:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


class ApiClient:
    def __init__(self, contexts: dict[str, TenantContext], name: str) -> None:
        from rest_framework.test import APIClient

        self.contexts = contexts
        self.client = APIClient()
        self.client.force_authenticate(user=_User())
        self.as_(name)

    def as_(self, name: str) -> ApiClient:
        key = str(uuid.uuid4())
        _contexts[key] = self.contexts[name]
        self.client.credentials(HTTP_X_TEST_CONTEXT=key)
        return self

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(url, params)

    def send(
        self, method: str, url: str, data: Any = None, *, key: str | None = None, if_match: str | None = None
    ) -> Any:
        extra = {"HTTP_IDEMPOTENCY_KEY": key or f"test-{uuid.uuid4()}"}
        if if_match:
            extra["HTTP_IF_MATCH"] = if_match
        return getattr(self.client, method)(url, data, format="json", **extra)

    def post(self, url: str, data: Any = None, **kw: Any) -> Any:
        return self.send("post", url, data, **kw)
