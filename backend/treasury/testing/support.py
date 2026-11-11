"""Test support for the treasury module (synthetic data only, DEV).

* :class:`GrantPolicy` — deny-by-default ``PolicyService`` double with per-principal grants (``module.*`` patterns),
  per-principal readable confidential fields (field policy) and the company row scope.
* :func:`treasury_world` — one tenant, two companies, the REAL P05 ledger (synthetic PUC-like accounts, labelled
  SINTÉTICO), the REAL P07 approvals (``DbWorkflowService``), P10's real ``ArOpenItems``, the AP adapter over P11's
  real AP open-item table, and P06's contract double for withholdings with labelled synthetic rates
  (``tax.rate.RTE25`` = 2.5 %, ``tax.rate.RICA1`` = 1 %; NOT LEGALLY VERIFIED).
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
from kernel.interfaces.ar import ArOpenItems
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.tax import TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.parameters import ParameterProvider
from kernel.tenancy import TenantContext
from kernel.testing.fakes import FakeAuditService, FakeParameterProvider, FakeTaxService
from kernel.testing.tenancy import bound, make_context
from kernel.uow import UnitOfWork, run_in_unit_of_work
from treasury.interfaces import POLICY_CE, POLICY_DUAL, POLICY_RC, ApOpenItems

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá
TODAY = dt.date(2026, 11, 9)
D = Decimal

TREASURY_ALL = (
    "treasury.*",
    "sales.receipt.post",  # ArOpenItems.apply (P10) checks it
    "sales.party.create",
    "sales.party.read",
    "purchasing.supplier.create",
    "ledger.dimension.create",
)
CASHIER = (
    "treasury.receipt.read",
    "treasury.receipt.create",
    "treasury.receipt.update",
    "treasury.receipt.delete",
    "treasury.receipt.post",
    "treasury.receipt.void",
    "treasury.disbursement.read",
    "treasury.disbursement.create",
    "treasury.disbursement.update",
    "treasury.disbursement.delete",
    "treasury.disbursement.post",
    "treasury.disbursement.void",
    "treasury.payment_batch.read",
    "treasury.payment_batch.create",
    "treasury.payment_batch.update",
    "treasury.payment_batch.post",
    "treasury.fund.read",
    "treasury.concept.read",
    "sales.receipt.post",
)
APPROVER = (
    "treasury.receipt.read",
    "treasury.receipt.approve",
    "treasury.disbursement.read",
    "treasury.disbursement.approve",
    "treasury.payment_batch.read",
    "treasury.payment_batch.approve",
    "workflow.approval.approve",
)
CONFIDENTIAL_READ = {
    "treasury.receipt": frozenset({"party"}),
    "treasury.disbursement": frozenset({"party"}),
    "treasury.bank_account": frozenset({"account_number"}),
}

#: Synthetic PUC-like accounts (SINTÉTICO, not a legal chart of accounts): code, name, type, nature, control
#: sources, role.
ACCOUNTS: tuple[tuple[str, str, str, str, list[str], str], ...] = (
    ("111005", "Bancos cuenta corriente (SINTÉTICO)", "asset", "debit", [], ""),
    ("110505", "Caja general (SINTÉTICO)", "asset", "debit", [], ""),
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", ["sales.", "treasury."], "AR"),
    ("220505", "Proveedores nacionales (SINTÉTICO)", "liability", "credit", ["purchasing.", "treasury."], "AP"),
    ("280505", "Anticipos de clientes (SINTÉTICO)", "liability", "credit", [], "CUSTOMER_ADVANCE"),
    ("133005", "Anticipos a proveedores (SINTÉTICO)", "asset", "debit", [], "VENDOR_ADVANCE"),
    ("135515", "Retención en la fuente a favor (SINTÉTICO)", "asset", "debit", [], "WHT_RECEIVABLE"),
    ("236540", "Retención en la fuente por pagar (SINTÉTICO)", "liability", "credit", [], "WHT_PAYABLE"),
    ("210505", "Obligaciones financieras (SINTÉTICO)", "liability", "credit", [], "LOAN_PAYABLE"),
    ("138095", "Reintegros por cobrar (SINTÉTICO)", "asset", "debit", [], "REIMBURSEMENT"),
    ("311505", "Aportes sociales (SINTÉTICO)", "equity", "credit", [], "CAPITAL"),
    ("519595", "Gastos diversos (SINTÉTICO)", "expense", "debit", [], "EXPENSE"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", [], "ROUNDING"),
)
SYNTHETIC_RATES = {"RTE25": D("0.025"), "RICA1": D("0.01")}


class GrantPolicy:
    """Deny by default; ``grant`` adds actions (``module.*`` patterns); ``confidential`` reads masked fields."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, set[str]] = {}
        self.confidential: set[uuid.UUID] = set()
        self.version = 1

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.setdefault(principal, set()).update(actions)
        self.version += 1

    def _allowed(self, principal: uuid.UUID, action: str) -> bool:
        mine = self.grants.get(principal, set())
        return action in mine or any(g.endswith(".*") and action.startswith(g[:-1]) for g in mine)

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        allowed = self._allowed(ctx.principal_id, action)
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
class World:
    policy: GrantPolicy
    params: FakeParameterProvider
    tenant_id: uuid.UUID = field(default_factory=ids.new_id)
    company: uuid.UUID = field(default_factory=ids.new_id)
    other_company: uuid.UUID = field(default_factory=ids.new_id)
    principals: dict[str, TenantContext] = field(default_factory=dict)
    accounts: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ principals / units of work
    def user(
        self, name: str, grants: Iterable[str], *, companies: list[uuid.UUID] | None = None, confidential: bool = True
    ) -> TenantContext:
        ctx = make_context(tenant_id=self.tenant_id, company_ids=companies or [self.company])
        self.policy.grant(ctx.principal_id, *grants)
        if confidential:
            self.policy.confidential.add(ctx.principal_id)
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
    def customer(self, code: str) -> Any:
        from localization_co.nit import check_digit
        from sales.party.service import create_party

        nit = f"9001{len(self.env.get('parties', [])):05d}"
        self.env.setdefault("parties", []).append(code)
        data = {
            "code": code,
            "legal_name": f"Cliente {code} S.A.S. (SINTÉTICO)",
            "nit": nit,
            "dv": str(check_digit(nit)),
            "person_type": "JURIDICA",
            "roles": ["customer"],
        }
        return self.run("admin", lambda u: create_party(u, self.company, data))

    def vendor(self, code: str) -> Any:
        """A purchasing supplier linked to a P10 party id (the CE party)."""
        from purchasing.services.suppliers import create_supplier

        party_id = ids.new_id()
        data = {
            "code": code,
            "name": f"Proveedor {code} S.A.S. (SINTÉTICO)",
            "nit": f"8001{len(self.env.setdefault('vendors', [])):05d}",
            "party_id": str(party_id),
        }
        self.env["vendors"].append(code)
        return self.run("admin", lambda u: create_supplier(u, self.company, data))

    def ar_item(self, party: Any, number: str, amount: str, due: dt.date = TODAY) -> Any:
        """A synthetic AR open item of a posted sales invoice (P10 sub-ledger row)."""
        from sales.services.ar import open_item

        doc = DocumentRef("sales.invoice", ids.new_id(), number)
        value = D(amount)
        return self.run(
            "admin", lambda u: open_item(u, party, doc, TODAY - dt.timedelta(days=10), due, "COP", D(1), value, value)
        )

    def ap_item(self, supplier: Any, number: str, amount: str) -> Any:
        """A synthetic AP open item of a posted vendor invoice (P11 sub-ledger row)."""
        from purchasing.models import ApOpenItem

        value = D(amount)

        def fn(u: UnitOfWork) -> Any:
            return ApOpenItem.objects.create(
                tenant_id=self.tenant_id,
                company_id=self.company,
                supplier=supplier,
                document_type="purchasing.vendor_invoice",
                document_id=ids.new_id(),
                document_number=number,
                document_date=TODAY - dt.timedelta(days=15),
                due_date=TODAY,
                currency="COP",
                amount=value,
                balance=value,
                amount_cop=value,
                balance_cop=value,
            )

        return self.run("admin", fn)

    def concept(self, code: str, direction: str, role: str, **extra: Any) -> Any:
        from treasury.services.masters import create_concept

        data = {"code": code, "name": f"Concepto {code} (SINTÉTICO)", "direction": direction, "account_role": role}
        return self.run("admin", lambda u: create_concept(u, self.company, {**data, **extra}, extended=True))

    def cost_center(self, code: str) -> Any:
        from ledger.dimensions import service as dims

        return self.run(
            "admin",
            lambda u: dims.create_node(
                u,
                self.company,
                "cost_center",
                dims.NodeInput(
                    code=code, description=f"CC {code} (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
                ),
            ),
        )

    def configure(self, **values: Any) -> Any:
        from treasury.services.common import configure

        return self.run("admin", lambda u: configure(u, self.company, **values))

    def approval_policies(self, approvers: list[str]) -> None:
        """P07 policies TES-RC / TES-CE (one of the approvers) and TES-DUAL (two different approvers)."""
        from workflow.approvals.service import validate_steps
        from workflow.models import ApprovalPolicy

        people = [{"kind": "user", "ref": str(self.ctx(a).principal_id)} for a in approvers]
        with self.scope():
            for code, doc, quorum in (
                (POLICY_RC, "treasury.receipt", 1),
                (POLICY_CE, "treasury.disbursement", 1),
                (POLICY_DUAL, "treasury.disbursement", 2),
            ):
                ApprovalPolicy.objects.create(
                    tenant_id=self.tenant_id,
                    company_id=self.company,
                    code=code,
                    name=f"Tesorería {code} (SINTÉTICA)",
                    document_type=doc,
                    steps=validate_steps([{"approvers": people, "quorum": quorum}]),
                )

    # ------------------------------------------------------------------ ledger reads
    def entry_lines(self, entry_id: Any) -> list[tuple[str, Decimal, Decimal]]:
        """``(account code, debit, credit)`` of the primary-book entry, in line order."""
        from ledger.models import JournalLine

        with self.scope():
            rows = (
                JournalLine.objects.filter(entry_id=entry_id, entry__book__primary=True)
                .select_related("account")
                .order_by("line_no")
            )
            return [(r.account.code, r.debit, r.credit) for r in rows]

    def entry_count(self) -> int:
        from ledger.models import JournalEntry

        with self.scope():
            return JournalEntry.objects.filter(company_id=self.company, book__primary=True).count()


def _companies(w: World) -> None:
    from iam.models import Company
    from kernel.uow import unit_of_work

    for n, cid in enumerate((w.company, w.other_company)):
        ctx = make_context(tenant_id=w.tenant_id, company_ids=[cid])
        with unit_of_work(ctx):
            Company.objects.create(
                id=cid,
                tenant_id=w.tenant_id,
                code=f"TES{n}",
                nit=f"80000001{n}",
                dv="0",
                legal_name=f"Empresa tesorería {n} S.A.S. (sintética)",
            )


def _boot(w: World) -> None:
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination
    from treasury.services.masters import create_bank_account, create_cash_box

    def boot(u: UnitOfWork) -> None:
        setup.setup_company(u, w.company)
        periods.ensure_year(u, w.company, 2026)
        for code, name, typ, nature, sources, role in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=w.tenant_id,
                company_id=w.company,
                code=code,
                name=name,
                type=typ,
                nature=nature,
                is_control=bool(sources),
                control_sources=sources,
            )
            w.accounts[code] = acc
            if role:
                AccountDetermination.objects.create(
                    tenant_id=w.tenant_id, company_id=w.company, role_code=role, account=acc
                )
        w.env["bank"] = create_bank_account(
            u,
            w.company,
            {
                "code": "BCO-1",
                "name": "Banco sintético cuenta corriente",
                "bank_code": "SYN-BANK",
                "account_number": "000123456789",
                "currency": "COP",
                "account_id": str(w.accounts["111005"].id),
            },
        )
        w.env["cash"] = create_cash_box(
            u,
            w.company,
            {
                "code": "CAJA-1",
                "name": "Caja general sintética",
                "currency": "COP",
                "account_id": str(w.accounts["110505"].id),
            },
        )

    w.run("admin", boot)
    w.env["fund"] = w.env["bank"].fund
    w.env["cash_fund"] = w.env["cash"].fund


def make_world(policy: GrantPolicy, params: FakeParameterProvider) -> World:
    w = World(policy, params)
    _companies(w)
    w.user("admin", TREASURY_ALL)
    w.user("cashier", CASHIER)
    w.user("cashier2", CASHIER)
    w.user("approver", APPROVER)
    w.user("approver2", APPROVER)
    w.user("outsider", ())
    w.user("other_admin", TREASURY_ALL, companies=[w.other_company])
    _boot(w)
    return w


@contextmanager
def treasury_world() -> Iterator[World]:
    from ledger.registry import implementations
    from sales.services.ar import SalesArOpenItems
    from treasury.vouchers.ap import PurchasingApOpenItems
    from workflow.approvals.service import DbWorkflowService

    params = FakeParameterProvider(
        {
            f"tax.rate.{code}": FakeParameterProvider.synthetic(f"tax.rate.{code}", rate)
            for code, rate in SYNTHETIC_RATES.items()
        }
    )
    policy = GrantPolicy()
    ledger = implementations()
    overrides: list[tuple[Callable[..., Any], Any]] = [
        (PolicyService, policy),
        (PostingService, ledger[PostingService]),
        (PeriodService, ledger[PeriodService]),
        (Dimensions, ledger[Dimensions]),
        (TaxService, FakeTaxService(params)),
        (ParameterProvider, params),
        (AuditService, FakeAuditService()),
        (WorkflowService, DbWorkflowService()),
        (ArOpenItems, SalesArOpenItems()),
        (ApOpenItems, PurchasingApOpenItems()),
    ]
    with ExitStack() as stack:
        stack.enter_context(clock.frozen(NOW))
        for interface, impl in overrides:
            stack.enter_context(services.override(interface, impl))
        yield make_world(policy, params)


# --------------------------------------------------------------------------- API client

_contexts: dict[str, TenantContext] = {}


def resolve_context(request: Any) -> TenantContext | None:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


class ApiClient:
    def __init__(self, world: World, name: str = "cashier") -> None:
        from rest_framework.test import APIClient

        self.world = world
        self.client = APIClient()
        self.client.force_authenticate(user=_User())
        self.as_(name)

    def as_(self, name: str) -> ApiClient:
        key = str(uuid.uuid4())
        _contexts[key] = self.world.ctx(name)
        self.client.credentials(HTTP_X_TEST_CONTEXT=key)
        return self

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(f"/api/v1/treasury/{url}", params)

    def send(
        self, method: str, url: str, data: Any = None, *, key: str | None = None, if_match: str | None = None
    ) -> Any:
        extra = {"HTTP_IDEMPOTENCY_KEY": key or f"test-{uuid.uuid4()}"}
        if if_match:
            extra["HTTP_IF_MATCH"] = if_match
        return getattr(self.client, method)(f"/api/v1/treasury/{url}", data, format="json", **extra)

    def post(self, url: str, data: Any = None, **kw: Any) -> Any:
        return self.send("post", url, data, **kw)


def money(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "COP"}
