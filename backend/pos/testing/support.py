"""Test support for the POS module (synthetic data only, DEV).

Builds on the sales test support (P10a): deny-by-default :class:`PatternPolicy` (here extended with the POS field
registry for confidential principals), kernel doubles for workflow/tax/audit, and plugs in the REAL P09 stock service
and the REAL P05 ledger. ``boot`` creates one company with a synthetic PUC-like chart (SINTÉTICO), account roles of
the POS postings, a synthetic DIAN-like DEE POS range (``SETP``, NOT a real resolution), two branches, a warehouse per
branch, items with presentations and barcodes, opening stock and base prices.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from kernel import ids, services
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import FieldMask, PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import StockLine
from kernel.models import Sequence
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from sales.testing.support import Fakes, PatternPolicy, World, fake_services, make_world

#: ``cajero_pos`` role of contracts/permissions/catalog.yaml (grants; max_classification internal).
CASHIER = (
    "pos.session.*",
    "pos.ticket.*",
    "pos.return.create",
    "pos.return.read",
    "pos.cash_movement.create",
    "pos.cash_movement.read",
    "pos.catalog_snapshot.read",
    "inventory.item.read",
    "inventory.availability.read",
    "sales.party.read",
    "sales.party.create",
)
SUPERVISOR = ("pos.*", "sales.party.read", "inventory.item.read")

ACCOUNTS: tuple[
    tuple[str, str, str, str, bool, list[str], str], ...
] = (  # synthetic PUC-like codes (SINTÉTICO, not a legal chart of accounts)
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", True, ["sales."], "AR"),
    ("413595", "Ingresos por ventas (SINTÉTICO)", "income", "credit", False, [], "REVENUE"),
    ("240805", "IVA generado (SINTÉTICO)", "liability", "credit", False, [], "VAT_PAYABLE"),
    ("613595", "Costo de ventas (SINTÉTICO)", "expense", "debit", False, [], "COGS"),
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("110510", "Caja POS (SINTÉTICO)", "asset", "debit", False, [], "CASH_POS"),
    ("110505", "Caja general (SINTÉTICO)", "asset", "debit", False, [], "CASH_TRANSFER"),
    ("111020", "Consignaciones en tránsito (SINTÉTICO)", "asset", "debit", False, [], "BANK_TRANSIT"),
    ("138095", "Datáfonos por cobrar (SINTÉTICO)", "asset", "debit", False, [], "CARD_CLEARING"),
    ("280505", "Bonos por redimir (SINTÉTICO)", "liability", "credit", False, [], "VOUCHER_LIABILITY"),
    ("530595", "Faltantes y sobrantes de caja (SINTÉTICO)", "expense", "debit", False, [], "CASH_DIFFERENCE"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
)


def _pos_confidential() -> dict[str, frozenset[str]]:
    from pos.registry import FIELDS

    return {r: frozenset(k for k, v in f.items() if v not in ("internal", "public")) for r, f in FIELDS.items()}


class PosPolicy(PatternPolicy):
    """``PatternPolicy`` whose confidential principals also read the POS confidential / PII fields."""

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        if resource_type.startswith("pos.") and ctx.principal_id in self.confidential:
            return FieldMask(_pos_confidential().get(resource_type, frozenset()), frozenset())
        return super().field_mask(ctx, resource_type)


@contextmanager
def pos_services(*, failing_stock: bool = False) -> Iterator[Fakes]:
    """Kernel doubles + REAL ledger (P05) + REAL stock (P09); ``failing_stock`` injects a failure at the stock step."""
    from inventory import registry as inv_registry
    from inventory.stock.service import SERVICE
    from ledger.registry import implementations

    impls = implementations()
    stock: Any = _failing_stock() if failing_stock else SERVICE
    with ExitStack() as stack:
        fakes = stack.enter_context(fake_services(real_posting=impls[PostingService], real_stock=stock))
        policy = PosPolicy()
        fakes.policy = policy
        stack.enter_context(services.override(PolicyService, cast(PolicyService, policy)))
        stack.enter_context(services.override(PeriodService, impls[PeriodService]))
        stack.enter_context(services.override(Dimensions, impls[Dimensions]))
        inv_registry.register_branch_reference()
        from pos import registry

        registry.register_branch_reference()
        registry.register_fields()
        yield fakes


def _failing_stock() -> Any:
    from inventory.stock.service import InventoryStockService

    class FailingStock(InventoryStockService):
        """REAL P09 stock service whose ``issue`` raises AFTER the moves were written (failure injection, T-P12-02)."""

        armed = False

        def issue(
            self,
            uow: UnitOfWork,
            company_id: uuid.UUID,
            source: DocumentRef,
            lines: list[StockLine],
            move_date: dt.date,
        ) -> Any:
            result = super().issue(uow, company_id, source, lines, move_date)
            if self.armed:
                raise RuntimeError("injected failure after the stock step")
            return result

    return FailingStock()


@dataclass
class PosEnv:
    world: World
    branch: Any
    other_branch: Any
    foreign_branch: Any
    warehouse: Any
    other_warehouse: Any
    items: dict[str, Any] = field(default_factory=dict)
    presentations: dict[str, Any] = field(default_factory=dict)
    pos_range: Any = None
    config: Any = None
    terminal: Any = None

    @property
    def company(self) -> uuid.UUID:
        return self.world.company

    def run(self, who: str, fn: Any) -> Any:
        return self.world.run(who, fn)


def build(fakes: Fakes) -> PosEnv:
    """Company, chart, ranges, branches, warehouses, items (+ presentation 'Caja x12' with its barcode), stock."""
    from iam.models import Branch
    from inventory import masters
    from inventory.stock.service import SERVICE
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination

    world = make_world(fakes)
    world.user("pos_admin", ("pos.*", "sales.*", "inventory.*"))
    world.user("cashier", CASHIER, confidential=False)
    world.user("cashier2", CASHIER, confidential=False)
    world.user("supervisor", SUPERVISOR)
    admin = world.ctx("admin")
    fakes.policy.grant(admin.principal_id, "pos.*", "inventory.*")

    def boot(u: UnitOfWork) -> dict[str, Any]:
        setup.setup_company(u, world.company)
        periods.ensure_year(u, world.company, 2026)
        for code, name, typ, nature, control, sources, role in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=world.tenant_id,
                company_id=world.company,
                code=code,
                name=name,
                type=typ,
                nature=nature,
                is_control=control,
                control_sources=sources,
            )
            AccountDetermination.objects.create(
                tenant_id=world.tenant_id, company_id=world.company, role_code=role, account=acc
            )
        pos_range = Sequence.objects.create(  # synthetic DIAN-like range — NOT a real numbering resolution
            tenant_id=world.tenant_id,
            company_id=world.company,
            sequence_code="POS",
            prefix="SETP",
            is_fiscal=True,
            range_from=1,
            range_to=5000,
            valid_from=dt.date(2026, 1, 1),
            valid_to=dt.date(2027, 12, 31),
            resolution_number="SINTETICA-POS-0001",
        )
        branch = Branch.objects.create(
            id=ids.new_id(),
            tenant_id=world.tenant_id,
            company_id=world.company,
            code="BOG",
            name="Sucursal BOG (SINTÉTICA)",
            is_head_office=True,
        )
        other = Branch.objects.create(
            id=ids.new_id(),
            tenant_id=world.tenant_id,
            company_id=world.company,
            code="MED",
            name="Sucursal MED (SINTÉTICA)",
        )
        masters.create_uom(u, world.company, {"code": "UND", "name": "Unidad", "precision": 0})
        wh = masters.create_warehouse(
            u, world.company, {"code": "PDV", "name": "Bodega punto de venta", "branch_id": str(branch.id)}
        )
        wh2 = masters.create_warehouse(
            u, world.company, {"code": "MED1", "name": "Bodega MED", "branch_id": str(other.id)}
        )
        soda = masters.create_item(
            u,
            world.company,
            {
                "code": "GAS-350",
                "name": "Gaseosa 350 ml (SINTÉTICO)",
                "base_uom": "UND",
                "sales_tax_code": "IVA19",
                "gtin": "7701234567897",
            },
        )
        snack = masters.create_item(
            u,
            world.company,
            {"code": "PAQ-01", "name": "Paquete pasabocas (SINTÉTICO)", "base_uom": "UND", "sales_tax_code": "IVA19"},
        )
        box = masters.create_presentation(
            u, soda, {"code": "CJ12", "name": "Caja x12", "uom": "CAJA", "factor": "12", "barcode": "2001234567893"}
        )
        six = masters.create_presentation(
            u, soda, {"code": "SIX", "name": "Six pack", "uom": "PACK", "factor": "6", "barcode": "2009876543213"}
        )
        receipt = DocumentRef("purchasing.receipt", ids.new_id(), "REC-SYN-POS")
        SERVICE.receive(
            u,
            world.company,
            receipt,
            [
                StockLine(
                    "1", soda.id, wh.id, Decimal(24), presentation_id=box.id, unit_cost=Decimal("1000")
                ),  # 2 boxes
                StockLine("2", soda.id, wh.id, Decimal(6), unit_cost=Decimal("1000")),  # 6 loose
                StockLine("3", snack.id, wh.id, Decimal(50), unit_cost=Decimal("2000")),
            ],
            dt.date(2026, 11, 2),
        )
        return {
            "branch": branch,
            "other": other,
            "wh": wh,
            "wh2": wh2,
            "soda": soda,
            "snack": snack,
            "box": box,
            "six": six,
            "range": pos_range,
        }

    env = cast(dict[str, Any], run_in_unit_of_work(boot, admin))
    foreign = _foreign_branch(world)
    world.base_price(env["soda"].id, "2500")
    world.base_price(env["snack"].id, "100000")
    pos = PosEnv(
        world=world,
        branch=env["branch"],
        other_branch=env["other"],
        foreign_branch=foreign,
        warehouse=env["wh"],
        other_warehouse=env["wh2"],
        items={"soda": env["soda"], "snack": env["snack"]},
        presentations={"box": env["box"], "six": env["six"]},
        pos_range=env["range"],
    )
    return pos


def _foreign_branch(world: World) -> Any:
    """A branch of the OTHER company of the same tenant (cross-company link tests)."""
    from iam.models import Branch
    from kernel.testing.tenancy import make_context
    from kernel.uow import unit_of_work

    ctx = make_context(tenant_id=world.tenant_id, company_ids=[world.other_company])
    with unit_of_work(ctx):
        return Branch.objects.create(
            id=ids.new_id(),
            tenant_id=world.tenant_id,
            company_id=world.other_company,
            code="BOG",
            name="Sucursal BOG empresa 2",
            is_head_office=True,
        )


def open_counter(env: PosEnv, *, opening: str = "200000", cashier: str = "cashier") -> Any:
    """POS config + terminal of the BOG branch and an open session for ``cashier``."""
    from pos.services import sessions, setup

    cfg = env.run(
        "pos_admin",
        lambda u: setup.create_config(
            u,
            env.company,
            {
                "name": "Caja 1 BOG",
                "branch_id": str(env.branch.id),
                "warehouse_id": str(env.warehouse.id),
                "numbering_range_id": str(env.pos_range.id),
            },
        ),
    )
    term = env.run(
        "pos_admin",
        lambda u: setup.create_terminal(u, env.company, {"code": "T1", "name": "Terminal 1", "config_id": str(cfg.id)}),
    )
    env.config, env.terminal = cfg, term
    return env.run(
        cashier, lambda u: sessions.open_session(u, env.company, {"terminal_id": str(term.id), "opening_cash": opening})
    )
