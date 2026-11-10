"""Demo tenant ``demo-andina`` for the cross-module scenarios (shared/11 "Demo tenant").

Companies **A** "Comercial Andina S.A.S." and **B** "Distribuciones del Valle S.A.S." (synthetic, COP,
America/Bogota). The ledger of both companies is bootstrapped with the **real** P05 services
(``ledger.setup.setup_company``, ``ledger.periods.ensure_year``) and a synthetic PUC whose codes follow
the shared/07 role defaults. Every account role a scenario posts to is mapped by an
``AccountDetermination`` row; control accounts (AR, AP, GRNI, IC) accept postings only from their
sub-ledger document types, exactly like production.

The PUC mapping is a template that the contador must review (EG-8). Everything is SINTÉTICO.
P21 owns the real demo seed (``backend/kernel/fixtures/demo_tenant.yaml``); this module is the P22
scenario world and does not replace it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .principals import synthetic_id

TENANT_KEY = "demo-andina"
COMPANY_A = "Comercial Andina S.A.S. (SINTÉTICO)"
COMPANY_B = "Distribuciones del Valle S.A.S. (SINTÉTICO)"

SALES = ["sales.", "pos.", "intercompany.", "assets.disposal", "onboarding."]
PURCHASES = ["purchase.", "intercompany.", "assets.acquisition", "treasury.", "onboarding."]
TREASURY = ["treasury.", "pos.", "sales.receipt", "onboarding."]

#: (code, name, type, nature, control_sources or None, roles)
PUC: tuple[tuple[str, str, str, str, list[str] | None, tuple[str, ...]], ...] = (
    ("110505", "Caja general", "asset", "debit", None, ("CASH",)),
    ("110510", "Caja POS", "asset", "debit", None, ("CASH_POS",)),
    ("111005", "Bancos nacionales", "asset", "debit", None, ("BANK",)),
    ("111010", "Bancos moneda extranjera", "asset", "debit", None, ("BANK_USD",)),
    ("112005", "Tarjetas por cobrar (datáfono)", "asset", "debit", None, ("CARD_CLEARING",)),
    ("130505", "Clientes nacionales", "asset", "debit", SALES + TREASURY, ("AR",)),
    ("131505", "Cuentas por cobrar a vinculados", "asset", "debit", SALES, ("IC_RECEIVABLE",)),
    ("135515", "Retención en la fuente a favor", "asset", "debit", None, ("WHT_RECEIVABLE_RENTA",)),
    ("135517", "ReteIVA a favor", "asset", "debit", None, ("WHT_RECEIVABLE_IVA",)),
    ("135518", "ReteICA a favor", "asset", "debit", None, ("WHT_RECEIVABLE_ICA",)),
    ("143505", "Mercancías no fabricadas por la empresa", "asset", "debit", None, ("INVENTORY",)),
    ("152405", "Equipo de cómputo", "asset", "debit", None, ("FIXED_ASSET_COST",)),
    ("159215", "Depreciación acumulada equipo de cómputo", "asset", "credit", None, ("ACCUM_DEPRECIATION",)),
    ("220505", "Proveedores nacionales", "liability", "credit", PURCHASES, ("AP",)),
    ("222005", "Cuentas por pagar a vinculados", "liability", "credit", PURCHASES, ("IC_PAYABLE",)),
    ("233595", "Mercancía recibida no facturada (GRNI)", "liability", "credit", ["purchase."], ("GRNI",)),
    ("236540", "Retención en la fuente por pagar", "liability", "credit", None, ("WHT_PAYABLE",)),
    ("236801", "ReteICA por pagar", "liability", "credit", None, ("ICA_WHT_PAYABLE",)),
    ("237005", "Aportes EPS/AFP empleado por pagar", "liability", "credit", None, ("SS_PAYABLE_EMPLOYEE",)),
    ("238030", "Aportes AFP empleador por pagar", "liability", "credit", None, ("SS_PAYABLE_AFP",)),
    ("237010", "Aportes CCF por pagar", "liability", "credit", None, ("SS_PAYABLE_CCF",)),
    ("237006", "Aportes ARL por pagar", "liability", "credit", None, ("SS_PAYABLE_ARL",)),
    ("240801", "IVA generado por pagar", "liability", "credit", None, ("VAT_PAYABLE",)),
    ("240802", "IVA descontable", "asset", "debit", None, ("VAT_RECOVERABLE",)),
    ("250505", "Salarios por pagar", "liability", "credit", None, ("PAYROLL_PAYABLE",)),
    ("261005", "Provisión cesantías", "liability", "credit", None, ("PROVISION_CESANTIAS",)),
    ("261010", "Provisión intereses sobre cesantías", "liability", "credit", None, ("PROVISION_INT_CESANTIAS",)),
    ("261015", "Provisión prima de servicios", "liability", "credit", None, ("PROVISION_PRIMA",)),
    ("261020", "Provisión vacaciones", "liability", "credit", None, ("PROVISION_VACACIONES",)),
    ("310505", "Patrimonio de apertura (SINTÉTICO)", "equity", "credit", None, ("OPENING_BALANCE_EQUITY",)),
    ("413535", "Comercio al por mayor y al por menor", "income", "credit", None, ("REVENUE",)),
    ("413536", "Ventas a vinculados", "income", "credit", None, ("REVENUE_IC",)),
    ("424505", "Utilidad en venta de activos", "income", "credit", None, ("GAIN_ON_DISPOSAL",)),
    ("429581", "Ajuste al peso", "income", "credit", None, ("ROUNDING", "FX_ROUNDING")),
    ("510506", "Sueldos", "expense", "debit", None, ("SALARY_EXPENSE",)),
    ("510527", "Auxilio de transporte", "expense", "debit", None, ("TRANSPORT_ALLOWANCE_EXPENSE",)),
    ("510568", "Aportes parafiscales y seguridad social", "expense", "debit", None, ("SS_EXPENSE",)),
    ("510530", "Prestaciones sociales", "expense", "debit", None, ("BENEFITS_EXPENSE",)),
    ("513595", "Servicios (documento soporte)", "expense", "debit", None, ("SERVICES_EXPENSE",)),
    ("516020", "Depreciación equipo de cómputo", "expense", "debit", None, ("DEPRECIATION_EXPENSE",)),
    ("519595", "Gastos diversos", "expense", "debit", None, ("EXPENSE",)),
    ("530525", "Diferencia en cambio realizada", "expense", "debit", None, ("FX_REALIZED_LOSS",)),
    ("530526", "Diferencia en cambio no realizada", "expense", "debit", None, ("UNREALIZED_FX_LOSS",)),
    ("539520", "Faltantes de caja", "expense", "debit", None, ("CASH_DIFFERENCE",)),
    ("613535", "Costo de ventas comercio", "expense", "debit", None, ("COGS",)),
)

#: kernel.sequences codes the reference document flows allocate (gap-free, D-017).
SEQUENCES = (
    ("FV", "FV-"),  # factura de venta
    ("REC", "EA-"),  # entrada de almacén (goods receipt)
    ("FC", "FC-"),  # factura de compra
    ("RC", "RC-"),  # recibo de caja
    ("CE", "CE-"),  # comprobante de egreso
    ("POS", "POS-"),  # tiquete POS
    ("DS", "DS-"),  # documento soporte
    ("NC", "NC-"),  # nota crédito / devolución
    ("NOM", "NOM-"),  # nómina
    ("AF", "AF-"),  # activos fijos
    ("IC", "IC-"),  # intercompañía
    ("APE", "APE-"),  # apertura
)


@dataclass
class Andina:
    ctx: Any
    a: uuid.UUID
    b: uuid.UUID
    warehouse: dict[str, uuid.UUID] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------- execution
    def run(self, fn: Callable[[Any], Any], **kw: Any) -> Any:
        from kernel.uow import run_in_unit_of_work

        return run_in_unit_of_work(fn, self.ctx, **kw)

    @contextmanager
    def scope(self) -> Iterator[Any]:
        from kernel.testing.tenancy import bound

        with bound(self.ctx) as c:
            yield c

    def grant(self, *permissions: str, principal: uuid.UUID | None = None) -> None:
        """Grant permission codes in the policy fake (explicit, per principal; never a blanket bypass)."""
        from kernel import services
        from kernel.interfaces.policy import PolicyService

        policy = services.get(PolicyService)
        for code in permissions:
            policy.grant(principal or self.ctx.principal_id, code)  # type: ignore[attr-defined]

    # ----------------------------------------------------------------- GL reads (real ledger tables)
    def entries(self, source: Any, book: str = "NIIF") -> list[Any]:
        from ledger.models import JournalEntry

        with self.scope():
            return list(
                JournalEntry.objects.filter(source_type=source.type, source_id=source.id, book__code=book).order_by(
                    "number"
                )
            )

    def lines(self, source: Any, purpose: str | None = None, book: str = "NIIF") -> list[tuple[str, str, Decimal]]:
        """``[(role, 'Dr'|'Cr', amount_cop)]`` of the source's entries in ``book`` (roles via determination)."""
        from ledger.models import JournalEntry, JournalLine

        roles = self.role_by_account_code()
        with self.scope():
            q = JournalEntry.objects.filter(source_type=source.type, source_id=source.id, book__code=book)
            if purpose:
                q = q.filter(purpose=purpose)
            out = []
            for ln in JournalLine.objects.filter(entry__in=q).select_related("account").order_by("entry__number",
                                                                                                  "line_no"):  # fmt: skip
                role = "ROUNDING" if ln.is_rounding else roles[(ln.company_id, ln.account.code)]
                out.append((role, "Dr", ln.debit) if ln.debit else (role, "Cr", ln.credit))
            return out

    def balance(self, role: str, company: uuid.UUID, book: str = "NIIF", currency: str | None = None) -> Decimal:
        """Debit-positive GL balance in COP (or in ``currency`` amounts) of the account mapped to ``role``."""
        from django.db.models import Q, Sum

        from ledger.models import AccountDetermination, JournalLine

        with self.scope():
            account_ids = list(
                AccountDetermination.objects.filter(company_id=company, role_code=role).values_list(
                    "account_id", flat=True
                )
            )
            q = JournalLine.objects.filter(account_id__in=account_ids, entry__book__code=book, company_id=company)
            if currency:
                q = q.filter(currency=currency)
                agg = q.aggregate(
                    d=Sum("amount_currency", filter=Q(debit__gt=0)), c=Sum("amount_currency", filter=Q(credit__gt=0))
                )
            else:
                agg = q.aggregate(d=Sum("debit"), c=Sum("credit"))
        return (agg["d"] or Decimal(0)) - (agg["c"] or Decimal(0))

    def role_by_account_code(self) -> dict[tuple[uuid.UUID, str], str]:
        from ledger.models import AccountDetermination

        with self.scope():
            rows = AccountDetermination.objects.select_related("account").order_by("role_code")
            out: dict[tuple[uuid.UUID, str], str] = {}
            for r in rows:
                out.setdefault((r.company_id, r.account.code), r.role_code)
            return out

    def counts(self) -> dict[str, int]:
        """Row counts of the effect tables (entries, lines, stock moves, outbox, audit, sequences used)."""
        from django.db import connection

        tables = {
            "journal_entries": "ledger_journal_entry",
            "journal_lines": "ledger_journal_line",
            "stock_moves": "qa_ref_move",
            "outbox": "kernel_outbox",
            "audit": "workflow_audit_event",
            "documents": "qa_ref_document",
        }
        out = {}
        with self.scope(), connection.cursor() as cur:
            for key, table in tables.items():
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
                out[key] = int(cur.fetchone()[0])
            cur.execute("SELECT coalesce(sum(next_number - 1), 0) FROM kernel_sequence")
            out["numbers_consumed"] = int(cur.fetchone()[0])
        return out


def demo_context(company_ids: Iterable[uuid.UUID] | None = None, **kw: Any) -> Any:
    from kernel.testing.tenancy import make_context

    tenant = synthetic_id("tenant", TENANT_KEY)
    a, b = synthetic_id(TENANT_KEY, "A"), synthetic_id(TENANT_KEY, "B")
    return make_context(tenant_id=tenant, company_ids=list(company_ids or [a, b]), **kw)


def service_overrides() -> ExitStack:
    """The real P05 ledger + P07 audit services, a permissive-deny policy fake (no blanket bypass:
    only the soft-close permission is never granted here)."""
    from kernel import services
    from kernel.interfaces.audit import AuditService
    from kernel.interfaces.dimensions import Dimensions
    from kernel.interfaces.periods import PeriodService
    from kernel.interfaces.policy import PolicyService
    from kernel.interfaces.posting import PostingService
    from kernel.testing.fakes import FakePolicyService
    from ledger.registry import implementations
    from workflow.audit.service import DbAuditService

    impls = implementations()
    stack = ExitStack()
    stack.enter_context(services.override(PostingService, impls[PostingService]))
    stack.enter_context(services.override(PeriodService, impls[PeriodService]))
    stack.enter_context(services.override(Dimensions, impls[Dimensions]))
    stack.enter_context(services.override(AuditService, DbAuditService()))
    stack.enter_context(services.override(PolicyService, FakePolicyService()))
    return stack


def bootstrap(years: Iterable[int] = (2026,)) -> Andina:
    """Create the demo tenant ledger (both companies), sequences and the reference doc table."""
    from kernel.models import Sequence
    from kernel.uow import run_in_unit_of_work
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination, AccountRole

    from . import refdocs, refstock

    refstock.install()
    refdocs.install()
    ctx = demo_context()
    a, b = synthetic_id(TENANT_KEY, "A"), synthetic_id(TENANT_KEY, "B")

    def fn(uow: Any) -> None:
        for company in (a, b):
            setup.setup_company(uow, company)
            for year in years:
                periods.ensure_year(uow, company, year)
            for code, name, typ, nature, control, roles in PUC:
                acc = Account.objects.create(
                    tenant_id=ctx.tenant_id, company_id=company, code=code, name=f"{name} (SINTÉTICO)", type=typ,
                    nature=nature, is_control=control is not None, control_sources=control or [], puc_code=code,
                )  # fmt: skip
                for role in roles:
                    AccountRole.objects.get_or_create(
                        tenant_id=ctx.tenant_id, company_id=company, code=role, defaults={"name": role}
                    )
                    AccountDetermination.objects.create(
                        tenant_id=ctx.tenant_id, company_id=company, role_code=role, account=acc
                    )
            for code, prefix in SEQUENCES:
                Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, sequence_code=code, prefix=prefix)

    run_in_unit_of_work(fn, ctx)
    world = Andina(ctx=ctx, a=a, b=b)
    world.warehouse = {
        "BOG-01": synthetic_id(TENANT_KEY, "A", "BOG-01"),
        "CLO-01": synthetic_id(TENANT_KEY, "B", "CLO-01"),
    }
    return world


def item(code: str) -> uuid.UUID:
    return synthetic_id(TENANT_KEY, "item", code)


def party(name: str) -> uuid.UUID:
    return synthetic_id(TENANT_KEY, "party", name)


def add_rate(world: Andina, company: uuid.UUID, currency: str, day: dt.date, rate: str) -> None:
    """Fixture TRM (FIXTURE — the Banco de la República/Superfinanciera adapter is P16's)."""
    from ledger.models import ExchangeRate

    world.run(
        lambda u: ExchangeRate.objects.create(
            tenant_id=world.ctx.tenant_id, company_id=company, currency=currency, date=day, rate=Decimal(rate),
            source="fixture",
        )
    )  # fmt: skip
