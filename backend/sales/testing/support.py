"""Test support for the sales / CRM modules (synthetic data only, DEV).

* :class:`PatternPolicy` — deny-by-default ``PolicyService`` double: per-principal grants with ``*`` patterns,
  per-principal confidential field reads (every ``confidential``/``restricted-pii`` field of the sales and CRM
  field registries), company row scope.
* :class:`World` — one tenant, two companies, role-like principals and helpers that create synthetic masters
  (customers, base prices, price lists, pricing formulas through the REAL P07 formula lifecycle with SoD).
* :class:`ApiClient` — DRF test client bound to a principal through ``ERP_TENANT_CONTEXT_RESOLVER``.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel import ids, services
from kernel.errors import PermissionDenied
from kernel.interfaces.audit import AuditService
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import Inventory, StockService
from kernel.interfaces.tax import TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.parameters import ParameterProvider
from kernel.tenancy import TenantContext
from kernel.testing.fakes import (
    FakeAuditService,
    FakeInventory,
    FakeParameterProvider,
    FakePostingService,
    FakeTaxService,
    FakeWorkflowService,
)
from kernel.testing.tenancy import make_context
from kernel.uow import UnitOfWork, run_in_unit_of_work

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá
TODAY = dt.date(2026, 11, 9)
SALES_ALL = ("sales.*", "crm.*")
FORMULA_AUTHOR = ("workflow.formula.create", "workflow.formula.update", "workflow.formula.read")
FORMULA_APPROVER = ("workflow.formula.approve", "workflow.formula.read")
SELLER = (
    "sales.party.read",
    "sales.party.create",
    "sales.pricing.read",
    "sales.price_list.read",
    "sales.price_formula.read",
    "sales.quote.*",
    "sales.order.read",
    "sales.order.create",
    "sales.order.update",
    "sales.invoice.read",
    "crm.*",
)
PORTAL_CUSTOMER = ("portal.customer_*.read",)


def _confidential_fields() -> dict[str, frozenset[str]]:
    from crm.api.serializers import FIELDS as CRM_FIELDS
    from sales.api.serializers import FIELDS as SALES_FIELDS

    out: dict[str, frozenset[str]] = {}
    for registry in (SALES_FIELDS, CRM_FIELDS):
        for resource, fields in registry.items():
            out[resource] = frozenset(k for k, v in fields.items() if v != "internal" and v != "public")
    return out


class PatternPolicy:
    """Deny by default; grants may use ``fnmatch`` patterns (``sales.*``); ``confidential`` principals read the
    confidential / PII fields (cost roles), the others get them masked."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, set[str]] = {}
        self.confidential: set[uuid.UUID] = set()
        self.version = 1
        self.calls: list[tuple[uuid.UUID, str]] = []

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.setdefault(principal, set()).update(actions)
        self.version += 1

    def revoke(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.setdefault(principal, set()).difference_update(actions)
        self.version += 1

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        self.calls.append((ctx.principal_id, action))
        allowed = any(fnmatch.fnmatchcase(action, g) for g in self.grants.get(ctx.principal_id, ()))
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self.authorize(ctx, action, resource).allowed:
            raise PermissionDenied(f"missing permission {action}", code="policy_denied")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        if ctx.principal_id not in self.confidential:
            return FieldMask(frozenset(), frozenset())
        return FieldMask(_confidential_fields().get(resource_type, frozenset()), frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


@dataclass
class Fakes:
    policy: PatternPolicy
    workflow: FakeWorkflowService
    inventory: Any
    posting: Any
    tax: FakeTaxService
    parameters: FakeParameterProvider
    audit: FakeAuditService


@contextmanager
def fake_services(*, real_posting: Any = None, real_stock: Any = None) -> Iterator[Fakes]:
    """Contract doubles of the other modules; ``real_posting`` / ``real_stock`` plug P05 / P09 in."""
    params = FakeParameterProvider(
        {
            "tax.rate.IVA19": FakeParameterProvider.synthetic("tax.rate.IVA19", Decimal("0.19")),
            "tax.rate.RTE25": FakeParameterProvider.synthetic("tax.rate.RTE25", Decimal("0.025")),
        }
    )
    fakes = Fakes(
        policy=PatternPolicy(),
        workflow=FakeWorkflowService(),
        inventory=real_stock or FakeInventory(),
        posting=real_posting or FakePostingService(),
        tax=FakeTaxService(params),
        parameters=params,
        audit=FakeAuditService(),
    )
    overrides: list[tuple[Callable[..., Any], Any]] = [
        (PolicyService, fakes.policy),
        (WorkflowService, fakes.workflow),
        (StockService, fakes.inventory),
        (Inventory, fakes.inventory),
        (PostingService, fakes.posting),
        (TaxService, fakes.tax),
        (ParameterProvider, params),
        (AuditService, fakes.audit),
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
    parties: dict[str, Any] = field(default_factory=dict)

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

    # ------------------------------------------------------------ masters
    def customer(self, code: str, *, group: str = "", nit: str | None = None, **extra: Any) -> Any:
        from localization_co.nit import check_digit
        from sales.party.service import create_party

        number = nit or f"9001{len(self.parties):05d}"
        data = {
            "code": code,
            "legal_name": f"Cliente {code} S.A.S. (SINTÉTICO)",
            "nit": number,
            "dv": str(check_digit(number)),
            "person_type": "JURIDICA",
            "roles": ["customer"],
            "customer_group": group,
            **extra,
        }
        party = self.run("admin", lambda u: create_party(u, self.company, data))
        self.parties[code] = party
        return party

    def base_price(self, item: uuid.UUID, price: str, currency: str = "COP", **extra: Any) -> Any:
        from sales.pricing.service import set_item_price

        data = {"item_id": str(item), "currency": currency, "base_price": price, "valid_from": "2026-01-01", **extra}
        return self.run("admin", lambda u: set_item_price(u, self.company, data))

    def price_list(self, code: str, prices: list[dict[str, Any]], **extra: Any) -> Any:
        from sales.pricing.service import create_price_list

        data = {"code": code, "name": f"Lista {code}", "currency": "COP", "prices": prices, **extra}
        return self.run("admin", lambda u: create_price_list(u, self.company, data))

    def formula(self, code: str, expression: str, fields: dict[str, str], tests: list[dict[str, Any]]) -> Any:
        """ACTIVE P07 pricing formula: authored by ``admin``, approved by ``formula_approver`` (SoD)."""
        from workflow.formula.lifecycle import FormulaService

        svc = FormulaService()
        data = {
            "code": code,
            "domain": "pricing",
            "expression": expression,
            "inputs": {"fields": fields},
            "result_type": "money",
            "tests": tests,
            "effective_from": "2026-01-01",
        }
        row = svc.create(self.ctx("admin"), self.company, data)
        svc.submit(self.ctx("admin"), row.id)
        return svc.approve(self.ctx("formula_approver"), row.id, "aprobada (SINTÉTICO)")

    def bind_formula(self, code: str, formula_code: str, *, scope: dict[str, Any] | None = None, priority: int = 0) -> Any:
        from sales.pricing.service import create_price_formula

        data = {
            "code": code,
            "name": f"Fórmula {code}",
            "formula_code": formula_code,
            "scope": scope or {},
            "priority": priority,
            "valid_from": "2026-01-01",
        }
        return self.run("admin", lambda u: create_price_formula(u, self.company, data))


def make_world(fakes: Fakes) -> World:
    w = World(fakes)
    _companies(w)
    w.user("admin", (*SALES_ALL, *FORMULA_AUTHOR))
    w.user("formula_approver", FORMULA_APPROVER)
    w.user("approver", ("sales.*",))
    w.user("seller", SELLER, confidential=False)
    w.user("portal", PORTAL_CUSTOMER, confidential=False)
    w.user("outsider", ())
    w.user("other_admin", SALES_ALL, companies=[w.other_company])
    return w


def _companies(w: World) -> None:
    """``iam_company`` rows of the two synthetic companies (other modules' masters reference them)."""
    from iam.models import Company
    from kernel.uow import unit_of_work

    for n, cid in enumerate((w.company, w.other_company)):
        ctx = make_context(tenant_id=w.tenant_id, company_ids=[cid])
        with unit_of_work(ctx):
            Company.objects.create(
                id=cid, tenant_id=w.tenant_id, code=f"SYN{n}", nit=f"80000000{n}", dv="0", legal_name=f"Empresa {n} S.A.S. (sintética)"
            )


# --------------------------------------------------------------------------- API client

_contexts: dict[str, TenantContext] = {}


def resolve_context(request: Any) -> TenantContext | None:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


class ApiClient:
    def __init__(self, world: World, module: str = "sales") -> None:
        from rest_framework.test import APIClient

        self.world = world
        self.module = module
        self.client = APIClient()
        self.client.force_authenticate(user=_User())
        self.as_("admin")

    def as_(self, name: str) -> ApiClient:
        key = str(uuid.uuid4())
        _contexts[key] = self.world.ctx(name)
        self.client.credentials(HTTP_X_TEST_CONTEXT=key)
        return self

    def url(self, path: str) -> str:
        return f"/api/v1/{self.module}/{path}"

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(self.url(path), params)

    def send(self, method: str, path: str, data: Any = None, *, key: str | None = None, if_match: str | None = None) -> Any:
        extra = {"HTTP_IDEMPOTENCY_KEY": key or f"test-{uuid.uuid4()}"}
        if if_match:
            extra["HTTP_IF_MATCH"] = if_match
        return getattr(self.client, method)(self.url(path), data, format="json", **extra)

    def post(self, path: str, data: Any = None, **kw: Any) -> Any:
        return self.send("post", path, data, **kw)

    def patch(self, path: str, data: Any = None, **kw: Any) -> Any:
        return self.send("patch", path, data, **kw)
