"""Fixtures for inventory tests: real PostgreSQL, real IAM policy engine, real ledger posting, synthetic data.

Two companies (ALFA, BETA) with a head-office branch each, the ledger bootstrapped with synthetic accounts for
INVENTORY / INVENTORY_IN_TRANSIT (labels say SINTÉTICO; no real PUC claim), and an ``inv`` helper that drives the
services inside units of work.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from iam.models import Branch
from iam.oidc import verifier
from iam.oidc.local import LocalIssuer
from iam.testing.world import World, make_world
from inventory import masters, registry
from inventory.models import Item, Location, Presentation, Warehouse
from inventory.stock.service import SERVICE
from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import Inventory, StockLine, StockService
from kernel.testing.fakes import FakeAuditService
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.models import Account, AccountDetermination
from ledger.registry import implementations as ledger_impls

ISSUER = "http://127.0.0.1:8080/auth/realms/erp-dev"
TODAY = dt.date(2026, 11, 9)
INV_GRANTS = ["inventory.*.*", "iam.branch.read", "ledger.*.read"]

ACCOUNTS = (
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", "INVENTORY"),
    ("143595", "Mercancías en tránsito (SINTÉTICO)", "asset", "debit", "INVENTORY_IN_TRANSIT"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", "ROUNDING"),
)


@pytest.fixture(scope="session")
def _issuer_session() -> LocalIssuer:
    return LocalIssuer(ISSUER)


@pytest.fixture
def issuer(_issuer_session: LocalIssuer) -> Iterator[LocalIssuer]:
    verifier.set_key_provider(_issuer_session, ISSUER)
    try:
        yield _issuer_session
    finally:
        verifier.set_key_provider(None)


@pytest.fixture(autouse=True)
def inventory_services() -> Iterator[dict[str, Any]]:
    cache.clear()
    impls = ledger_impls()
    audit = FakeAuditService()
    stack = [
        services.override(PostingService, impls[PostingService]),
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(AuditService, audit),
        services.override(Inventory, SERVICE),
        services.override(StockService, SERVICE),
    ]
    for cm in stack:
        cm.__enter__()
    registry.register_branch_reference()
    registry.register_fields()
    try:
        with clock.frozen(dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)):
            yield {"audit": audit}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)
        cache.clear()


def bootstrap_ledger(ctx: tenancy.TenantContext, company: uuid.UUID) -> None:
    def fn(u: Any) -> None:
        setup.setup_company(u, company)
        periods.ensure_year(u, company, 2026)
        for code, name, typ, nature, role in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, code=code, name=name, type=typ, nature=nature
            )
            AccountDetermination.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, role_code=role, account=acc
            )

    run_in_unit_of_work(fn, ctx)


class Api:
    def __init__(self, token: str, company: uuid.UUID | None) -> None:
        self.client = APIClient()
        self.token = token
        self.company = company

    def _h(self, idem: bool, extra: dict[str, str]) -> dict[str, str]:
        h = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        if self.company:
            h["HTTP_X_COMPANY_ID"] = str(self.company)
        if idem:
            h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
        h.update(extra)
        return h

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(url, params, **self._h(False, {}))

    def post(self, url: str, data: Any = None, **extra: str) -> Any:
        return self.client.post(url, data or {}, format="json", **self._h(True, extra))

    def patch(self, url: str, data: Any, **extra: str) -> Any:
        return self.client.patch(url, data, format="json", **self._h(True, extra))


@dataclass
class Inv:
    """Synthetic inventory world: companies, branches, warehouses, items; drives the services."""

    world: World
    branches: dict[str, uuid.UUID] = field(default_factory=dict)

    # ------------------------------------------------------------ context
    def ctx(self, company: str = "ALFA") -> tenancy.TenantContext:
        return self.world.ctx([company])

    def company(self, name: str = "ALFA") -> uuid.UUID:
        return self.world.companies[name]

    def run(self, fn: Any, company: str = "ALFA") -> Any:
        return run_in_unit_of_work(fn, self.ctx(company))

    def scope(self, company: str = "ALFA") -> Any:
        return tenancy.scope(self.ctx(company))

    # ------------------------------------------------------------ masters
    def branch(self, company: str, code: str, head: bool = False) -> uuid.UUID:
        bid = ids.new_id()
        with self.world.seed():
            Branch.objects.create(
                id=bid,
                tenant_id=self.world.tenant_id,
                company_id=self.company(company),
                code=code,
                name=f"Sucursal {code} (SINTÉTICA)",
                is_head_office=head,
            )
        self.branches[f"{company}:{code}"] = bid
        return bid

    def warehouse(self, code: str, company: str = "ALFA", branch: str | None = None) -> Warehouse:
        bid = self.branches[f"{company}:{branch or 'BOG'}"]
        return self.run(
            lambda u: masters.create_warehouse(
                u, self.company(company), {"code": code, "name": f"Bodega {code}", "branch_id": str(bid)}
            ),
            company,
        )

    def uom(self, code: str = "UND", precision: int = 0, company: str = "ALFA") -> Any:
        return self.run(
            lambda u: masters.create_uom(
                u, self.company(company), {"code": code, "name": code, "precision": precision}
            ),
            company,
        )

    def item(self, code: str, company: str = "ALFA", **extra: Any) -> Item:
        data = {"code": code, "name": f"Producto {code} (SINTÉTICO)", "base_uom": extra.pop("base_uom", "UND"), **extra}
        return self.run(lambda u: masters.create_item(u, self.company(company), data), company)

    def presentation(self, item: Item, code: str, factor: str, **extra: Any) -> Presentation:
        data = {"code": code, "name": f"{code} x{factor}", "uom": code.lower(), "factor": factor, **extra}
        return self.run(
            lambda u: masters.create_presentation(u, Item.objects.select_related("base_uom").get(id=item.id), data)
        )

    def bin(self, wh: Warehouse, code: str) -> Location:
        with self.scope():
            return Location.objects.get(warehouse_id=wh.id, code=code)

    # ------------------------------------------------------------ stock
    def line(self, item: Item, wh: Warehouse, qty: str, ref: str = "1", **kw: Any) -> StockLine:
        def _id(v: Any) -> Any:
            return getattr(v, "id", v)

        return StockLine(
            line_ref=ref,
            item_id=item.id,
            warehouse_id=wh.id,
            quantity=Decimal(qty),
            location_id=_id(kw.get("location")),
            lot_id=_id(kw.get("lot")),
            presentation_id=_id(kw.get("presentation")),
            unit_cost=Decimal(kw["cost"]) if "cost" in kw else None,
        )

    def receive(self, *lines: StockLine, company: str = "ALFA", source: DocumentRef | None = None) -> Any:
        doc = source or DocumentRef("purchasing.receipt", ids.new_id(), "REC-SYN")
        return self.run(lambda u: SERVICE.receive(u, self.company(company), doc, list(lines), TODAY), company)

    def issue(self, *lines: StockLine, company: str = "ALFA", source: DocumentRef | None = None) -> Any:
        doc = source or DocumentRef("sales.delivery", ids.new_id(), "ENT-SYN")
        return self.run(lambda u: SERVICE.issue(u, self.company(company), doc, list(lines), TODAY), company)

    def reserve(self, *lines: StockLine, source: DocumentRef | None = None) -> list[uuid.UUID]:
        doc = source or DocumentRef("sales.order", ids.new_id(), "PED-SYN")
        return self.run(lambda u: SERVICE.reserve(u, self.company(), doc, list(lines)))

    def avail(self, **kw: Any) -> list[Any]:
        with self.scope():
            return SERVICE.detailed_availability(self.company(), **kw)


@pytest.fixture
def world(db: Any) -> World:
    return make_world()


@pytest.fixture
def inv(world: World) -> Inv:
    env = Inv(world)
    env.branch("ALFA", "BOG", head=True)
    env.branch("ALFA", "MED")
    env.branch("BETA", "BOG", head=True)
    for name in ("ALFA", "BETA"):
        bootstrap_ledger(world.ctx([name]), world.companies[name])
        env.uom("UND", 0, name)
        env.uom("KG", 3, name)
    return env


@pytest.fixture
def login(issuer: LocalIssuer, world: World) -> Any:
    def _login(user: str, company: str | None = "ALFA") -> Api:
        subject = world.users[user].idp_subject
        token = issuer.issue(subject, world.tenant_id, "erp-web")
        return Api(token, world.companies[company] if company else None)

    return _login


@pytest.fixture
def clerk(world: World, inv: Inv, issuer: LocalIssuer) -> str:
    """User ``bodeguero`` with inventory rights (confidential cost fields visible) in both companies."""
    world.add_role("inv_admin", INV_GRANTS, max_cls="confidential")
    world.add_user("bodeguero", ["ALFA", "BETA"], subject=str(uuid.uuid4()))
    world.assign("bodeguero", "inv_admin", "ALFA")
    world.assign("bodeguero", "inv_admin", "BETA")
    return "bodeguero"
