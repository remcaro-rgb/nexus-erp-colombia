"""Test support for the purchasing module (synthetic data only, DEV).

* :class:`GrantPolicy` — deny-by-default ``PolicyService`` double with per-principal grants and
  per-principal readable confidential fields (field policy), company row scope.
* :class:`World` — one tenant, two companies, role-like principals (buyer, manager, approver, warehouse,
  accountant, outsider) and synthetic suppliers; helpers that run services in units of work.
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
from kernel.interfaces.files import FileStore
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import StockService
from kernel.interfaces.tax import TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.parameters import ParameterProvider
from kernel.tenancy import TenantContext
from kernel.testing.fakes import (
    FakeAuditService,
    FakeFileStore,
    FakeInventory,
    FakeParameterProvider,
    FakePostingService,
    FakeTaxService,
    FakeWorkflowService,
)
from kernel.testing.tenancy import make_context
from kernel.uow import UnitOfWork, run_in_unit_of_work

BUYER = (
    "purchasing.rfq.read",
    "purchasing.rfq.create",
    "purchasing.rfq.update",
    "purchasing.rfq.delete",
    "purchasing.quote.read",
    "purchasing.quote.create",
    "purchasing.quote.update",
    "purchasing.quote.delete",
    "purchasing.order.read",
    "purchasing.order.create",
    "purchasing.order.update",
    "purchasing.order.void",
    "purchasing.order.delete",
    "purchasing.receipt.read",
    "purchasing.vendor_invoice.read",
    "purchasing.supplier.create",
    "purchasing.supplier.read",
)
MANAGER = (*BUYER, "purchasing.rfq.approve", "purchasing.order.approve")
APPROVER = (
    "purchasing.rfq.read",
    "purchasing.rfq.approve",
    "purchasing.order.read",
    "purchasing.order.approve",
    "purchasing.order.create",
    "purchasing.quote.read",
)
WAREHOUSE = (
    "purchasing.receipt.read",
    "purchasing.receipt.create",
    "purchasing.receipt.update",
    "purchasing.receipt.delete",
    "purchasing.order.read",
)
ACCOUNTANT = (
    "purchasing.vendor_invoice.read",
    "purchasing.vendor_invoice.create",
    "purchasing.vendor_invoice.update",
    "purchasing.vendor_invoice.post",
    "purchasing.vendor_invoice.delete",
    "purchasing.ap_open_item.read",
    "purchasing.ap_aging.read",
    "purchasing.grni.read",
    "purchasing.order.read",
)
ADMIN = ("purchasing.settings.update",)
CONFIDENTIAL_READ = {
    "purchasing.quote": frozenset({"party", "landed_total"}),
    "purchasing.order": frozenset({"party"}),
    "purchasing.receipt": frozenset({"party"}),
    "purchasing.vendor_invoice": frozenset({"party"}),
    "purchasing.ap_open_item": frozenset({"party", "balance"}),
}
NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá


class GrantPolicy:
    """Deny by default; ``grant`` adds actions; ``confidential`` lists principals that read confidential fields."""

    def __init__(self) -> None:
        self.grants: set[tuple[uuid.UUID, str]] = set()
        self.confidential: set[uuid.UUID] = set()
        self.version = 1

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.update((principal, a) for a in actions)
        self.version += 1

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        allowed = (ctx.principal_id, action) in self.grants
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self.authorize(ctx, action, resource).allowed:
            raise PermissionDenied(f"missing permission {action}", code="policy_denied")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        readable = (
            CONFIDENTIAL_READ.get(resource_type, frozenset()) if ctx.principal_id in self.confidential else frozenset()
        )
        return FieldMask(readable, frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


@dataclass
class Fakes:
    policy: GrantPolicy
    workflow: FakeWorkflowService
    inventory: FakeInventory
    posting: Any
    tax: FakeTaxService
    parameters: FakeParameterProvider
    audit: FakeAuditService
    files: FakeFileStore


@contextmanager
def fake_services(*, real_posting: Any = None) -> Iterator[Fakes]:
    params = FakeParameterProvider(
        {
            "tax.rate.IVA19": FakeParameterProvider.synthetic("tax.rate.IVA19", Decimal("0.19")),
            "tax.rate.RTE25": FakeParameterProvider.synthetic("tax.rate.RTE25", Decimal("0.025")),
        }
    )
    fakes = Fakes(
        policy=GrantPolicy(),
        workflow=FakeWorkflowService(),
        inventory=FakeInventory(),
        posting=real_posting or FakePostingService(),
        tax=FakeTaxService(params),
        parameters=params,
        audit=FakeAuditService(),
        files=FakeFileStore(),
    )
    overrides: list[tuple[Callable[..., Any], Any]] = [
        (PolicyService, fakes.policy),
        (WorkflowService, fakes.workflow),
        (StockService, fakes.inventory),
        (PostingService, fakes.posting),
        (TaxService, fakes.tax),
        (ParameterProvider, params),
        (AuditService, fakes.audit),
        (FileStore, fakes.files),
    ]
    with ExitStack() as stack:
        for interface, impl in overrides:
            stack.enter_context(services.override(interface, impl))
        yield fakes


@dataclass
class World:
    fakes: Fakes
    tenant_id: uuid.UUID = field(default_factory=ids.new_id)
    company: uuid.UUID = field(default_factory=ids.new_id)
    other_company: uuid.UUID = field(default_factory=ids.new_id)
    principals: dict[str, TenantContext] = field(default_factory=dict)
    suppliers: dict[str, Any] = field(default_factory=dict)

    def user(
        self, name: str, grants: Iterable[str], *, companies: list[uuid.UUID] | None = None, confidential: bool = True
    ) -> TenantContext:
        ctx = make_context(tenant_id=self.tenant_id, company_ids=companies or [self.company])
        self.fakes.policy.grant(ctx.principal_id, *grants)
        if confidential:
            self.fakes.policy.confidential.add(ctx.principal_id)
        self.principals[name] = ctx
        return ctx

    def ctx(self, name: str) -> TenantContext:
        return self.principals[name]

    def run(self, name: str, fn: Callable[[UnitOfWork], Any]) -> Any:
        return run_in_unit_of_work(fn, self.ctx(name))

    def supplier(
        self,
        code: str,
        name: str,
        *,
        rating: str = "3",
        terms: int = 30,
        currency: str = "COP",
        company: uuid.UUID | None = None,
    ) -> Any:
        from purchasing.services.suppliers import create_supplier

        target = company or self.company
        principal = "admin_other" if target == self.other_company else "admin"
        s = self.run(
            principal,
            lambda u: create_supplier(
                u,
                target,
                {
                    "code": code,
                    "name": name,
                    "nit": f"9000{len(self.suppliers):05d}",
                    "rating": rating,
                    "payment_terms_days": terms,
                    "currency": currency,
                    "party_id": str(ids.new_id()),
                },
            ),
        )
        self.suppliers[code] = s
        return s


def make_world(fakes: Fakes) -> World:
    w = World(fakes)
    w.user("admin", (*ADMIN, "purchasing.supplier.create"))
    w.user("admin_other", (*ADMIN, "purchasing.supplier.create"), companies=[w.other_company])
    w.user("buyer", BUYER)
    w.user("buyer_no_confidential", BUYER, confidential=False)
    w.user("manager", MANAGER)
    w.user("approver", APPROVER)
    w.user("warehouse", WAREHOUSE)
    w.user("accountant", ACCOUNTANT)
    w.user("outsider", ())
    w.user("other_buyer", MANAGER, companies=[w.other_company])
    return w


# --------------------------------------------------------------------------- API client

_contexts: dict[str, TenantContext] = {}


def resolve_context(request: Any) -> TenantContext | None:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


class ApiClient:
    def __init__(self, world: World) -> None:
        from rest_framework.test import APIClient

        self.world = world
        self.client = APIClient()
        self.client.force_authenticate(user=_User())
        self.as_("buyer")

    def as_(self, name: str) -> ApiClient:
        key = str(uuid.uuid4())
        _contexts[key] = self.world.ctx(name)
        self.client.credentials(HTTP_X_TEST_CONTEXT=key)
        return self

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(f"/api/v1/purchasing/{url}", params)

    def send(
        self,
        method: str,
        url: str,
        data: Any = None,
        *,
        key: str | None = None,
        if_match: str | None = None,
        fmt: str = "json",
    ) -> Any:
        extra = {"HTTP_IDEMPOTENCY_KEY": key or f"test-{uuid.uuid4()}"}
        if if_match:
            extra["HTTP_IF_MATCH"] = if_match
        call = getattr(self.client, method)
        if fmt == "multipart":
            return call(f"/api/v1/purchasing/{url}", data, format="multipart", **extra)
        return call(f"/api/v1/purchasing/{url}", data, format="json", **extra)

    def post(self, url: str, data: Any = None, **kw: Any) -> Any:
        return self.send("post", url, data, **kw)


def advance(to: dt.datetime) -> None:
    clock.get_clock().tick(to - clock.now())  # type: ignore[attr-defined]
