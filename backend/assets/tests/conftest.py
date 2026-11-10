"""Fixtures of the fixed-assets tests: real PostgreSQL, the P05 ledger engine, synthetic chart of accounts.

Every account, rate and amount here is SYNTHETIC (NOT LEGALLY VERIFIED); the fiscal 20 %/year maximum is the
S8 scenario value served by a labelled fake parameter provider.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from assets import service
from assets.interface import AssetService
from assets.models import Asset, AssetBook, AssetCategory, DepreciationRun
from assets.registry import implementations as asset_impls
from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.posting import PostingService
from kernel.parameters import ParameterProvider
from kernel.testing.fakes import FakeAuditService, FakeParameterProvider, FakePolicyService
from kernel.testing.tenancy import make_context
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.models import Account, AccountDetermination, JournalEntry, JournalLine, Period
from ledger.registry import implementations as ledger_impls

PERMISSIONS = [
    f"assets.{res}.{act}"
    for res, acts in {
        "category": ("read", "create", "update", "delete"),
        "asset": ("read", "create", "update", "delete", "void"),
        "book": ("read", "create", "update", "delete"),
        "depreciation_run": ("read", "create", "update", "delete", "post"),
        "revaluation": ("read", "create", "update", "delete", "post"),
        "impairment": ("read", "create", "update", "delete", "post"),
        "physical_count": ("read", "create", "update", "delete", "approve"),
        "asset_register": ("read",),
        "depreciation_schedule": ("read",),
        "asset_movement": ("read",),
    }.items()
    for act in acts
]

ACCOUNTS = (
    # code, name, type, nature, role(s), control sources
    ("152805", "Equipo de cómputo (SINTÉTICO)", "asset", "debit", ("FIXED_ASSET_COST",), []),
    ("159215", "Depreciación acumulada (SINTÉTICO)", "asset", "credit", ("ACCUM_DEPRECIATION",), []),
    ("159905", "Deterioro acumulado (SINTÉTICO)", "asset", "credit", ("ACCUM_IMPAIRMENT",), []),
    ("516015", "Gasto depreciación (SINTÉTICO)", "expense", "debit", ("DEPRECIATION_EXPENSE",), []),
    ("519910", "Gasto deterioro (SINTÉTICO)", "expense", "debit", ("IMPAIRMENT_EXPENSE",), []),
    ("539595", "Pérdida por revaluación (SINTÉTICO)", "expense", "debit", ("REVALUATION_LOSS",), []),
    ("381005", "Superávit por revaluación ORI (SINTÉTICO)", "equity", "credit", ("REVALUATION_SURPLUS",), []),
    ("424540", "Utilidad en venta de activos (SINTÉTICO)", "income", "credit", ("GAIN_ON_DISPOSAL",), []),
    ("531515", "Pérdida en baja de activos (SINTÉTICO)", "expense", "debit", ("LOSS_ON_DISPOSAL",), []),
    ("138095", "Deudores — venta de activos (SINTÉTICO)", "asset", "debit", ("ASSET_DISPOSAL_PROCEEDS",), []),
    ("220505", "Proveedores nacionales (SINTÉTICO)", "liability", "credit", ("AP",), ["purchasing."]),
    ("240810", "IVA descontable (SINTÉTICO)", "asset", "debit", ("VAT_RECOVERABLE",), []),
    ("143505", "Inventario (SINTÉTICO)", "asset", "debit", ("INVENTORY",), []),
    ("151605", "Construcciones en curso (SINTÉTICO)", "asset", "debit", ("CIP",), []),
    ("370505", "Resultados acumulados (SINTÉTICO)", "equity", "credit", ("RETAINED_EARNINGS",), []),
    ("311595", "Saldos iniciales (SINTÉTICO)", "equity", "credit", ("OPENING_BALANCE_EQUITY",), []),
    ("111005", "Bancos (SINTÉTICO)", "asset", "debit", ("BANK",), []),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", ("ROUNDING", "FX_ROUNDING"), []),
)
FISCAL_RATE_CODE = "assets.fiscal.max_annual_rate.computo"


@pytest.fixture(autouse=True)
def asset_services() -> Iterator[dict[str, Any]]:
    impls = ledger_impls()
    audit = FakeAuditService()
    policy = FakePolicyService()
    params = FakeParameterProvider(
        {
            FISCAL_RATE_CODE: FakeParameterProvider.synthetic(FISCAL_RATE_CODE, "20"),
            "uvt": FakeParameterProvider.synthetic("uvt", "52374"),
        }
    )
    stack = [
        services.override(PostingService, impls[PostingService]),
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(AuditService, audit),
        services.override(PolicyService, policy),
        services.override(ParameterProvider, params),
        services.override(AssetService, asset_impls()[AssetService]),
    ]
    for cm in stack:
        cm.__enter__()
    try:
        with clock.frozen(dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)):
            yield {"audit": audit, "policy": policy, "params": params}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)


@dataclass
class World:
    ctx: tenancy.TenantContext
    company: uuid.UUID
    other_company: uuid.UUID
    category: AssetCategory
    niif: AssetBook
    fiscal: AssetBook

    def run(self, fn: Any, ctx: tenancy.TenantContext | None = None) -> Any:
        return run_in_unit_of_work(fn, ctx or self.ctx)

    def period(self, code: str, company: uuid.UUID | None = None) -> Period:
        with tenancy.scope(self.ctx):
            return Period.objects.get(tenant_id=self.ctx.tenant_id, company_id=company or self.company, code=code)

    def asset(self, code: str = "LAP-001", cost: str = "4800000", acquired: str = "2026-03-10", **kw: Any) -> Asset:
        data = {"code": code, "name": kw.pop("name", f"Portátil {code} (SINTÉTICO)"),
                "category_id": str(kw.pop("category", self.category).id), "acquisition_cost": {"amount": cost,
                "currency": "COP"}, "acquisition_date": acquired, **kw}  # fmt: skip
        return self.run(lambda u: service.create_asset(u, self.company, data))

    def activate(self, asset: Asset, in_service: str = "2026-03-10", **params: Any) -> Asset:
        params.setdefault("counterpart_role", "BANK")
        params["in_service_date"] = in_service

        def fn(u: Any) -> Asset:
            obj = service.get_asset(u, self.company, asset.id, lock=True)
            return service.activate(u, obj, params, None)

        return self.run(fn)

    def depreciate(self, code: str, book: AssetBook | None = None, lines: list[dict[str, Any]] | None = None) -> Any:
        period = self.period(code)
        data = {"document_date": period.end.isoformat(), "period_id": str(period.id),
                "book_id": str((book or self.niif).id), "lines": lines or []}  # fmt: skip

        def fn(u: Any) -> DepreciationRun:
            run = service.create_run(u, self.company, data)
            posted, _ = service.post_run(u, run.id, self.company)
            return posted

        return self.run(fn)

    def months(self, first: str, last: str, book: AssetBook | None = None) -> list[DepreciationRun]:
        y, m = (int(x) for x in first.split("-"))
        out = []
        while f"{y:04d}-{m:02d}" <= last:
            out.append(self.depreciate(f"{y:04d}-{m:02d}", book))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    def book_value(self, asset: Asset, book: AssetBook) -> Any:
        from assets.models import AssetBookValue

        with tenancy.scope(self.ctx):
            return AssetBookValue.objects.get(asset_id=asset.id, book=book)

    def balance(self, account_code: str, book_code: str, until: dt.date | None = None) -> Decimal:
        """Debit − credit of an account in a ledger book (posted lines)."""
        with tenancy.scope(self.ctx):
            qs = JournalLine.objects.filter(
                tenant_id=self.ctx.tenant_id, company_id=self.company, account__code=account_code,
                entry__book__code=book_code,
            )  # fmt: skip
            if until is not None:
                qs = qs.filter(posting_date__lte=until)
            total = Decimal(0)
            for ln in qs:
                total += ln.debit - ln.credit
            return total

    def entries(self, source_type: str) -> list[JournalEntry]:
        with tenancy.scope(self.ctx):
            return list(
                JournalEntry.objects.filter(tenant_id=self.ctx.tenant_id, company_id=self.company,
                                            source_type=source_type).select_related("book")  # fmt: skip
            )


def bootstrap_ledger(ctx: tenancy.TenantContext, company: uuid.UUID) -> None:
    def fn(u: Any) -> None:
        setup.setup_company(u, company)
        periods.ensure_year(u, company, 2026)
        periods.ensure_year(u, company, 2027)
        for code, name, typ, nature, roles, sources in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, code=code, name=name, type=typ, nature=nature,
                is_control=bool(sources), control_sources=sources,
            )  # fmt: skip
            for r in roles:
                AccountDetermination.objects.create(tenant_id=ctx.tenant_id, company_id=company, role_code=r, account=acc)
        service.setup_company(u, company)

    run_in_unit_of_work(fn, ctx)


def make_category(ctx: tenancy.TenantContext, company: uuid.UUID, code: str = "COMPUTO", life: int = 36) -> Any:
    def fn(u: Any) -> AssetCategory:
        cat = AssetCategory.objects.create(
            tenant_id=ctx.tenant_id, company_id=company, code=code, name=f"Equipo de cómputo {code} (SINTÉTICO)",
            useful_life_months=life, method="STRAIGHT_LINE", created_by=ctx.principal_id,
        )  # fmt: skip
        books = {b.code: b for b in service.active_books(u, company)}
        service.set_book_default(u, cat, books["NIIF"], method="STRAIGHT_LINE", life=life)
        service.set_book_default(u, cat, books["FISCAL"], method="STRAIGHT_LINE", life=60,
                                 rate_parameter_code=FISCAL_RATE_CODE)  # fmt: skip
        return cat

    return run_in_unit_of_work(fn, ctx)


@pytest.fixture
def world(db: Any, asset_services: dict[str, Any]) -> World:
    a, b = ids.new_id(), ids.new_id()
    ctx = make_context(company_ids=[a, b])
    for perm in PERMISSIONS:
        asset_services["policy"].grant(ctx.principal_id, perm)
    asset_services["policy"].grant(ctx.principal_id, "ledger.period.update")
    bootstrap_ledger(ctx, a)
    bootstrap_ledger(ctx, b)
    cat = make_category(ctx, a)
    with tenancy.scope(ctx):
        niif = AssetBook.objects.get(company_id=a, code="NIIF")
        fiscal = AssetBook.objects.get(company_id=a, code="FISCAL")
    return World(ctx=ctx, company=a, other_company=b, category=cat, niif=niif, fiscal=fiscal)


def close_period(world: World, code: str) -> None:
    def fn(u: Any) -> None:
        Period.objects.filter(tenant_id=world.ctx.tenant_id, company_id=world.company, code=code).update(
            status="CLOSED"
        )

    world.run(fn)


D = Decimal
