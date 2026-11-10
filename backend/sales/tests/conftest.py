"""Fixtures of the sales tests (real PostgreSQL, real P07 formula engine; kernel doubles for other modules)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

from inventory import masters
from inventory import registry as inv_registry
from inventory.stock.service import SERVICE
from kernel import clock, ids, services
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import StockLine
from kernel.models import Sequence
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.models import Account, AccountDetermination
from ledger.registry import implementations
from sales.kits import KitComponent, KitExplosion
from sales.testing.support import NOW, TODAY, ApiClient, Fakes, World, fake_services, make_world


@pytest.fixture
def fakes() -> Iterator[Fakes]:
    with clock.frozen(NOW), fake_services() as f:
        yield f


@pytest.fixture
def world(db: Any, fakes: Fakes) -> World:
    return make_world(fakes)


@pytest.fixture
def api(settings: Any, world: World) -> ApiClient:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "sales.testing.support.resolve_context"
    return ApiClient(world)


ACCOUNTS = (  # synthetic PUC-like codes (SINTÉTICO, not a legal chart of accounts)
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", True, ["sales."], "AR"),
    ("413595", "Ingresos por ventas (SINTÉTICO)", "income", "credit", False, [], "REVENUE"),
    ("240805", "IVA generado (SINTÉTICO)", "liability", "credit", False, [], "VAT_PAYABLE"),
    ("613595", "Costo de ventas (SINTÉTICO)", "expense", "debit", False, [], "COGS"),
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
    ("417505", "Devoluciones en ventas (SINTÉTICO)", "income", "debit", False, [], "SALES_DISCOUNTS"),
    ("111005", "Bancos (SINTÉTICO)", "asset", "debit", False, [], "BANK"),
    ("135515", "Retención en la fuente a favor (SINTÉTICO)", "asset", "debit", False, [], "WHT_RECEIVABLE"),
    ("519910", "Deterioro de cartera (SINTÉTICO)", "expense", "debit", False, [], "BAD_DEBT_EXPENSE"),
    ("421020", "Diferencia en cambio ingreso (SINTÉTICO)", "income", "credit", False, [], "FX_REALIZED_GAIN"),
    ("530525", "Diferencia en cambio gasto (SINTÉTICO)", "expense", "debit", False, [], "FX_REALIZED_LOSS"),
)


class SyntheticKits:
    """Stand-in for P09c's KIT BOM explosion (multi-level flattened), keyed by kit item id."""

    def __init__(self) -> None:
        self.boms: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] = {}

    def is_kit(self, company_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        return item_id in self.boms

    def explode(self, company_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal, on: dt.date) -> list[KitComponent]:
        return [KitComponent(c, q * quantity) for c, q in self.boms.get(item_id, [])]


@pytest.fixture
def kit_world(db: Any, settings: Any) -> Iterator[tuple[World, dict[str, Any]]]:
    impls = implementations()
    kits = SyntheticKits()
    with (
        clock.frozen(NOW),
        fake_services(real_posting=impls[PostingService], real_stock=SERVICE) as fakes,
        services.override(PeriodService, impls[PeriodService]),
        services.override(Dimensions, impls[Dimensions]),
        services.override(KitExplosion, kits),
    ):
        inv_registry.register_branch_reference()
        world = make_world(fakes)
        settings.ERP_TENANT_CONTEXT_RESOLVER = "sales.testing.support.resolve_context"
        admin = world.ctx("admin")

        def boot(u: Any) -> dict[str, Any]:
            from iam.models import Branch

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
            for code, prefix in (("FV", "SETT"), ("NC", "NCS"), ("ND", "NDS")):  # synthetic DIAN-like ranges
                Sequence.objects.create(
                    tenant_id=world.tenant_id,
                    company_id=world.company,
                    sequence_code=code,
                    prefix=prefix,
                    is_fiscal=True,
                    range_from=1,
                    range_to=5000,
                    valid_from=dt.date(2026, 1, 1),
                    valid_to=dt.date(2027, 12, 31),
                    resolution_number="SINTETICA-0001",
                )
            branch = Branch.objects.create(
                id=ids.new_id(),
                tenant_id=world.tenant_id,
                company_id=world.company,
                code="BOG",
                name="Sucursal BOG (SINTÉTICA)",
                is_head_office=True,
            )
            masters.create_uom(u, world.company, {"code": "UND", "name": "Unidad", "precision": 0})
            wh = masters.create_warehouse(
                u, world.company, {"code": "PRIN", "name": "Bodega principal", "branch_id": str(branch.id)}
            )
            part_a = masters.create_item(
                u, world.company, {"code": "COMP-A", "name": "Componente A (SINTÉTICO)", "base_uom": "UND"}
            )
            part_b = masters.create_item(
                u, world.company, {"code": "COMP-B", "name": "Componente B (SINTÉTICO)", "base_uom": "UND"}
            )
            kit = masters.create_item(
                u,
                world.company,
                {"code": "KIT-1", "name": "Kit regalo (SINTÉTICO)", "base_uom": "UND", "item_type": "KIT"},
            )
            receipt = DocumentRef("purchasing.receipt", ids.new_id(), "REC-SYN")
            SERVICE.receive(
                u,
                world.company,
                receipt,
                [
                    StockLine("1", part_a.id, wh.id, Decimal(10), unit_cost=Decimal("100")),
                    StockLine("2", part_b.id, wh.id, Decimal(5), unit_cost=Decimal("250")),
                ],
                TODAY,
            )
            return {"wh": wh, "a": part_a, "b": part_b, "kit": kit, "branch": branch}

        env = run_in_unit_of_work(boot, admin)
        kits.boms[env["kit"].id] = [(env["a"].id, Decimal(2)), (env["b"].id, Decimal(1))]
        world.base_price(env["kit"].id, "1000")
        env["customer"] = world.customer("C-KIT")
        yield world, env
