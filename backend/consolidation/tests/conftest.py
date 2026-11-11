"""Fixtures of the intercompany/consolidation tests: real PostgreSQL (forced RLS), the P05 ledger engine and a
SYNTHETIC two-company chart of accounts (NOT LEGALLY VERIFIED; VAT 19 % is the S10 scenario value supplied by the
caller, never computed by this module)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from consolidation import intercompany, service
from consolidation.access import CONSOLIDATION_FLAG
from consolidation.interface import IcLine, IcSaleRequest, IntercompanyService
from consolidation.models import ConsolidationGroup, IntercompanyAgreement
from consolidation.registry import implementations as ic_impls
from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.models import FeatureFlagOverride
from kernel.testing.fakes import FakeAuditService, FakePolicyService
from kernel.testing.tenancy import make_context
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.models import Account, AccountDetermination, ExchangeRate, JournalEntry
from ledger.registry import implementations as ledger_impls

D = Decimal

PERMISSIONS = [
    *(f"consolidation.group.{a}" for a in ("read", "create", "update", "delete")),
    *(f"consolidation.run.{a}" for a in ("read", "create", "update", "delete", "post", "void")),
    "consolidation.consolidation_report.read",
    *(f"intercompany.agreement.{a}" for a in ("read", "create", "update", "delete")),
    "intercompany.reconciliation.read",
    "intercompany.document.post",
]

ACCOUNTS = (
    # code, name, type, nature, roles
    ("111005", "Bancos (SINTÉTICO)", "asset", "debit", ("BANK",)),
    ("120505", "Inversiones en subsidiarias (SINTÉTICO)", "asset", "debit", ("INVESTMENT",)),
    ("130505", "Cuentas por cobrar a vinculadas (SINTÉTICO)", "asset", "debit", ("IC_RECEIVABLE",)),
    ("143505", "Inventario de mercancías (SINTÉTICO)", "asset", "debit", ("INVENTORY",)),
    ("240810", "IVA descontable (SINTÉTICO)", "asset", "debit", ("VAT_RECOVERABLE",)),
    ("233505", "Cuentas por pagar a vinculadas (SINTÉTICO)", "liability", "credit", ("IC_PAYABLE",)),
    ("240805", "IVA por pagar (SINTÉTICO)", "liability", "credit", ("VAT_PAYABLE",)),
    ("310505", "Capital social (SINTÉTICO)", "equity", "credit", ("SHARE_CAPITAL",)),
    ("413505", "Ingresos por ventas a vinculadas (SINTÉTICO)", "income", "credit", ("REVENUE_IC",)),
    ("415505", "Ingresos por servicios (SINTÉTICO)", "income", "credit", ("REVENUE",)),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", ("ROUNDING", "FX_ROUNDING")),
    ("513505", "Servicios recibidos de vinculadas (SINTÉTICO)", "expense", "debit", ("EXPENSE_IC",)),
    ("613505", "Costo de ventas (SINTÉTICO)", "expense", "debit", ("COGS",)),
)


@pytest.fixture(autouse=True)
def ic_services() -> Iterator[dict[str, Any]]:
    impls = ledger_impls()
    audit = FakeAuditService()
    policy = FakePolicyService()
    stack = [
        services.override(PostingService, impls[PostingService]),
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(AuditService, audit),
        services.override(PolicyService, policy),
        services.override(IntercompanyService, ic_impls()[IntercompanyService]),
    ]
    for cm in stack:
        cm.__enter__()
    try:
        with clock.frozen(dt.datetime(2026, 11, 20, 15, 0, tzinfo=dt.UTC)):
            yield {"audit": audit, "policy": policy, "ledger": impls[PostingService]}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)


@dataclass
class World:
    ctx: tenancy.TenantContext  # preparer: both companies
    poster: tenancy.TenantContext  # second user (SoD): both companies
    a: uuid.UUID  # parent / seller
    b: uuid.UUID  # subsidiary / buyer
    policy: FakePolicyService
    audit: FakeAuditService

    def run(self, fn: Any, ctx: tenancy.TenantContext | None = None) -> Any:
        return run_in_unit_of_work(fn, ctx or self.ctx)

    def user(self, companies: list[uuid.UUID], perms: list[str] | None = None) -> tenancy.TenantContext:
        ctx = make_context(tenant_id=self.ctx.tenant_id, company_ids=companies)
        for p in PERMISSIONS if perms is None else perms:
            self.policy.grant(ctx.principal_id, p)
        return ctx

    def post(
        self, company: uuid.UUID, lines: list[tuple[str, str, str, uuid.UUID | None]], day: str = "2026-11-05"
    ) -> Any:
        """Direct journal entry: ``(role, DEBIT|CREDIT, amount, party company)``."""
        req = PostingRequest(
            company,
            dt.date.fromisoformat(day),
            DocumentRef("test.manual", ids.new_id(), "MAN"),
            "test",
            tuple(
                PostingLine(role, side, D(amt), party=PartyRef(p, "company") if p else None)  # type: ignore[arg-type]
                for role, side, amt, p in lines
            ),
        )
        return self.run(lambda u: services.get(PostingService).post(u, req))

    def agreement(self, **accounts: Any) -> IntercompanyAgreement:
        data = {
            "code": "AB",
            "name": "Andina → Bolívar (SINTÉTICO)",
            "company_a_id": str(self.a),
            "company_b_id": str(self.b),
            "accounts": accounts,
        }
        return self.run(lambda u: intercompany.create_agreement(u, data))

    def s10_request(self, source_id: uuid.UUID | None = None) -> IcSaleRequest:
        return IcSaleRequest(
            company_a_id=self.a,
            company_b_id=self.b,
            source=DocumentRef("sales.invoice", source_id or ids.new_id(), "FV-IC-0001"),
            document_date=dt.date(2026, 11, 10),
            lines=(
                IcLine(
                    "Mercancía S10 (SINTÉTICO)",
                    quantity=D(10),
                    unit_price=D(15000),
                    tax_amount=D(28500),
                    unit_cost=D(11500),
                    item_id=ids.new_id(),
                ),
            ),
        )

    def group(self, companies: list[uuid.UUID] | None = None, currency: str = "COP") -> ConsolidationGroup:
        data = {
            "code": f"G-{currency}",
            "name": "Grupo Andina (SINTÉTICO)",
            "companies": [str(c) for c in (companies or [self.a, self.b])],
            "presentation_currency": currency,
        }
        return self.run(lambda u: service.create_group(u, data))

    def entries(self, company: uuid.UUID, source_type: str) -> list[JournalEntry]:
        with tenancy.scope(self.ctx):
            return list(JournalEntry.objects.filter(company_id=company, source_type=source_type))

    def rate(self, company: uuid.UUID, currency: str, day: str, rate: str) -> None:
        def fn(u: Any) -> None:
            ExchangeRate.objects.create(
                tenant_id=self.ctx.tenant_id,
                company_id=company,
                currency=currency,
                date=dt.date.fromisoformat(day),
                rate=D(rate),
            )

        self.run(fn)


def bootstrap_ledger(ctx: tenancy.TenantContext, company: uuid.UUID, skip_roles: tuple[str, ...] = ()) -> None:
    def fn(u: Any) -> None:
        setup.setup_company(u, company)
        periods.ensure_year(u, company, 2026)
        for code, name, typ, nature, roles in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, code=code, name=name, type=typ, nature=nature
            )
            for r in roles:
                if r not in skip_roles:
                    AccountDetermination.objects.create(
                        tenant_id=ctx.tenant_id, company_id=company, role_code=r, account=acc
                    )

    run_in_unit_of_work(fn, ctx)


def enable_consolidation(ctx: tenancy.TenantContext) -> None:
    def fn(u: Any) -> None:
        FeatureFlagOverride.objects.create(
            tenant_id=ctx.tenant_id, code=CONSOLIDATION_FLAG, enabled=True, reason="tests"
        )

    run_in_unit_of_work(fn, ctx)


@pytest.fixture
def world(db: Any, ic_services: dict[str, Any]) -> World:
    a, b = ids.new_id(), ids.new_id()
    ctx = make_context(company_ids=[a, b])
    poster = make_context(tenant_id=ctx.tenant_id, company_ids=[a, b])
    for p in PERMISSIONS:
        ic_services["policy"].grant(ctx.principal_id, p)
        ic_services["policy"].grant(poster.principal_id, p)
    bootstrap_ledger(ctx, a)
    bootstrap_ledger(ctx, b)
    enable_consolidation(ctx)
    return World(ctx=ctx, poster=poster, a=a, b=b, policy=ic_services["policy"], audit=ic_services["audit"])


def s10_world(world: World, investment: str = "450000") -> World:
    """Parent A (80 % of B), opening balances, A's investment in B and the S10 sale A → B."""
    world.post(
        world.a,
        [
            ("BANK", "DEBIT", "1000000", None),
            ("INVENTORY", "DEBIT", "200000", None),
            ("SHARE_CAPITAL", "CREDIT", "1200000", None),
        ],
        "2026-11-02",
    )
    world.post(world.a, [("INVESTMENT", "DEBIT", investment, None), ("BANK", "CREDIT", investment, None)], "2026-11-03")
    world.post(world.b, [("BANK", "DEBIT", "500000", None), ("SHARE_CAPITAL", "CREDIT", "500000", None)], "2026-11-02")
    world.agreement()
    world.run(lambda u: intercompany.post_sale(u, world.s10_request()))
    return world
