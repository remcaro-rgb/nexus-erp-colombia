"""Shared fixtures for ledger tests (real PostgreSQL, synthetic data)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.testing.fakes import FakeAuditService, FakePolicyService
from kernel.testing.tenancy import make_context
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.dimensions import service as dims
from ledger.models import Account, AccountDetermination
from ledger.registry import implementations

TODAY = dt.date(2026, 11, 9)


@pytest.fixture(autouse=True)
def ledger_services() -> Iterator[dict[str, Any]]:
    impls = implementations()
    audit = FakeAuditService()
    policy = FakePolicyService()
    stack = [
        services.override(PostingService, impls[PostingService]),
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(AuditService, audit),
        services.override(PolicyService, policy),
    ]
    for cm in stack:
        cm.__enter__()
    try:
        with clock.frozen(dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)):
            yield {"audit": audit, "policy": policy, "posting": impls[PostingService]}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)


@dataclass
class World:
    ctx: tenancy.TenantContext
    company: uuid.UUID
    other_company: uuid.UUID

    def run(self, fn: Any, ctx: tenancy.TenantContext | None = None) -> Any:
        return run_in_unit_of_work(fn, ctx or self.ctx)

    def node(self, dimension: str, code: str, node_type: str = "DETALLE", parent: Any = None, company: Any = None, **kw: Any) -> Any:
        data = dims.NodeInput(
            code=code,
            description=kw.pop("description", f"Nodo {code} (SINTÉTICO)"),
            node_type=node_type,
            parent_id=str(parent.id) if parent is not None and hasattr(parent, "id") else parent,
            valid_from=kw.pop("valid_from", "2026-01-01"),
            **kw,
        )
        return self.run(lambda u: dims.create_node(u, company or self.company, dimension, data))

    def post(self, lines: list[PostingLine], *, source_type: str = "ledger.manual", purpose: str = "manual",
             day: dt.date = TODAY, journal: str = "GENERAL", company: Any = None, after: Any = None) -> Any:
        req = self.request(lines, source_type=source_type, purpose=purpose, day=day, journal=journal, company=company)

        def fn(u: Any) -> Any:
            result = services.get(PostingService).post(u, req)
            if after is not None:
                after(u, result)
            return result

        return self.run(fn)

    def simulate(self, lines: list[PostingLine], **kw: Any) -> Any:
        req = self.request(lines, **kw)
        return self.run(lambda u: services.get(PostingService).simulate(u, req))

    def request(self, lines: list[PostingLine], *, source_type: str = "ledger.manual", purpose: str = "manual",
                day: dt.date = TODAY, journal: str = "GENERAL", company: Any = None) -> PostingRequest:
        return PostingRequest(
            company_id=company or self.company,
            posting_date=day,
            source=DocumentRef(source_type, ids.new_id()),
            purpose=purpose,
            lines=tuple(lines),
            journal_code=journal,
        )

    def scope(self) -> Any:
        return tenancy.scope(self.ctx)


def line(role: str, side: str, amount: str, **dimensions: Any) -> PostingLine:
    values = DimensionValues(**{k: (v.id if hasattr(v, "id") else v) for k, v in dimensions.items()})
    return PostingLine(account_role=role, side=side, amount=Decimal(amount), dimensions=values)  # type: ignore[arg-type]


ACCOUNTS = (
    ("110505", "Caja general", "asset", "debit", False, []),
    ("111005", "Bancos nacionales", "asset", "debit", False, []),
    ("130505", "Clientes nacionales", "asset", "debit", True, ["sales."]),
    ("413595", "Ingresos (SINTÉTICO)", "income", "credit", False, []),
    ("510506", "Sueldos", "expense", "debit", False, []),
    ("519595", "Gastos diversos", "expense", "debit", False, []),
    ("529595", "Gastos de ventas", "expense", "debit", False, []),
    ("429581", "Ajuste al peso", "income", "credit", False, []),
)


def bootstrap_company(ctx: tenancy.TenantContext, company: uuid.UUID) -> None:
    def fn(u: Any) -> None:
        setup.setup_company(u, company)
        periods.ensure_year(u, company, 2026)
        for code, name, typ, nature, control, sources in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=company, code=code, name=name, type=typ, nature=nature,
                is_control=control, control_sources=sources,
            )
            role = {"110505": "CASH", "111005": "BANK", "130505": "AR", "413595": "SALES_REVENUE",
                    "519595": "EXPENSE", "510506": "PAYROLL_EXPENSE", "529595": "SELLING_EXPENSE",
                    "429581": "ROUNDING"}[code]
            AccountDetermination.objects.create(tenant_id=ctx.tenant_id, company_id=company, role_code=role, account=acc)

    run_in_unit_of_work(fn, ctx)


@pytest.fixture
def world(db: Any) -> World:
    a, b = ids.new_id(), ids.new_id()
    ctx = make_context(company_ids=[a, b])
    bootstrap_company(ctx, a)
    bootstrap_company(ctx, b)
    return World(ctx=ctx, company=a, other_company=b)
