"""T-INV-BOM-03 (integration): a kit sale issues its components at cost — REAL P09 stock + REAL P05 ledger.

The KIT BOM itself belongs to P09c (F-INV-BOM); the explosion is reached through the ``KitExplosion`` port
(CCR P10a-2). Here a synthetic implementation of the port answers the BOM; everything else is real: the
inventory stock service values and moves the components, the ledger posts Dr COGS / Cr INVENTORY and the AR /
revenue / VAT entry, all in the same unit of work as the sales document.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum

from inventory import masters
from inventory import registry as inv_registry
from inventory.models import Item, ItemValuation
from inventory.stock.service import SERVICE
from kernel import clock, ids, services
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import StockLine
from kernel.models import Sequence
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.models import Account, AccountDetermination, JournalLine
from ledger.registry import implementations
from kernel.errors import DomainError
from sales.kits import KitComponent, KitExplosion
from sales.models import Invoice
from sales.services import documents, posting
from sales.testing.support import NOW, TODAY, World, fake_services, make_world

pytestmark = pytest.mark.django_db

ACCOUNTS = (  # synthetic PUC-like codes (SINTÉTICO, not a legal chart of accounts)
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", True, ["sales."], "AR"),
    ("413595", "Ingresos por ventas (SINTÉTICO)", "income", "credit", False, [], "REVENUE"),
    ("240805", "IVA generado (SINTÉTICO)", "liability", "credit", False, [], "VAT_PAYABLE"),
    ("613595", "Costo de ventas (SINTÉTICO)", "expense", "debit", False, [], "COGS"),
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
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
                    tenant_id=world.tenant_id, company_id=world.company, code=code, name=name, type=typ,
                    nature=nature, is_control=control, control_sources=sources,
                )  # fmt: skip
                AccountDetermination.objects.create(tenant_id=world.tenant_id, company_id=world.company, role_code=role, account=acc)
            Sequence.objects.create(
                tenant_id=world.tenant_id, company_id=world.company, sequence_code="FV", prefix="SETT", is_fiscal=True,
                range_from=1, range_to=5000, valid_from=dt.date(2026, 1, 1), valid_to=dt.date(2027, 12, 31),
                resolution_number="SINTETICA-0001",
            )  # fmt: skip
            branch = Branch.objects.create(
                id=ids.new_id(), tenant_id=world.tenant_id, company_id=world.company, code="BOG",
                name="Sucursal BOG (SINTÉTICA)", is_head_office=True,
            )  # fmt: skip
            masters.create_uom(u, world.company, {"code": "UND", "name": "Unidad", "precision": 0})
            wh = masters.create_warehouse(u, world.company, {"code": "PRIN", "name": "Bodega principal", "branch_id": str(branch.id)})
            part_a = masters.create_item(u, world.company, {"code": "COMP-A", "name": "Componente A (SINTÉTICO)", "base_uom": "UND"})
            part_b = masters.create_item(u, world.company, {"code": "COMP-B", "name": "Componente B (SINTÉTICO)", "base_uom": "UND"})
            kit = masters.create_item(
                u, world.company, {"code": "KIT-1", "name": "Kit regalo (SINTÉTICO)", "base_uom": "UND", "item_type": "KIT"}
            )
            receipt = DocumentRef("purchasing.receipt", ids.new_id(), "REC-SYN")
            SERVICE.receive(u, world.company, receipt, [
                StockLine("1", part_a.id, wh.id, Decimal(10), unit_cost=Decimal("100")),
                StockLine("2", part_b.id, wh.id, Decimal(5), unit_cost=Decimal("250")),
            ], TODAY)  # fmt: skip
            return {"wh": wh, "a": part_a, "b": part_b, "kit": kit, "branch": branch}

        env = run_in_unit_of_work(boot, admin)
        kits.boms[env["kit"].id] = [(env["a"].id, Decimal(2)), (env["b"].id, Decimal(1))]
        world.base_price(env["kit"].id, "1000")
        env["customer"] = world.customer("C-KIT")
        yield world, env


def _gl(world: World, source_id: uuid.UUID) -> dict[str, Decimal]:
    with bound(world.ctx("admin")):
        rows = (
            JournalLine.objects.filter(company_id=world.company, entry__source_id=source_id, entry__book__code="NIIF")
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: r["d"] - r["c"] for r in rows}


def _qty(world: World, item: Item) -> Decimal:
    with bound(world.ctx("admin")):
        return Decimal(ItemValuation.objects.get(company_id=world.company, item_id=item.id).qty)


def test_T_INV_BOM_03_kit_sale_consumes_components_at_cost(kit_world: tuple[World, dict[str, Any]]) -> None:
    world, env = kit_world
    wh, kit = env["wh"], env["kit"]
    data = {
        "party": str(env["customer"].id),
        "document_date": TODAY.isoformat(),
        "deliver_on_invoice": True,
        "warehouse_id": str(wh.id),
        "branch_id": str(env["branch"].id),
        "lines": [{"item_id": str(kit.id), "quantity": "3", "tax_code": "IVA19", "warehouse_id": str(wh.id)}],
    }
    inv = world.run("admin", lambda u: posting.create_invoice(u, world.company, data))
    posted = world.run("admin", lambda u: posting.invoice_action(u, inv, "post", "", {}))
    assert posted.status == Invoice.Status.POSTED
    # components issued: 3 kits × (2 A + 1 B); the kit item itself never moves
    assert _qty(world, env["a"]) == Decimal(4) and _qty(world, env["b"]) == Decimal(2)
    with bound(world.ctx("admin")):
        assert not ItemValuation.objects.filter(company_id=world.company, item_id=kit.id, qty__lt=0).exists()
        line = posted.lines.get()
    cost = Decimal(6 * 100 + 3 * 250)  # 1350 at the components' average cost
    assert Decimal(line.cost_value) == cost
    assert sorted((c["item_id"], Decimal(c["quantity"])) for c in line.components) == sorted(
        [(str(env["a"].id), Decimal(6)), (str(env["b"].id), Decimal(3))]
    )
    gl = _gl(world, posted.id)
    assert gl["613595"] == cost and gl["143505"] == -cost  # Dr COGS / Cr INVENTORY at component cost
    assert gl["130505"] == Decimal("3570.00")  # AR = 3000 + 19 % synthetic VAT
    assert gl["413595"] == Decimal("-3000.00") and gl["240805"] == Decimal("-570.00")
    assert sum(gl.values()) == 0


def test_T_INV_BOM_03_kit_delivery_from_order_and_insufficient_component(kit_world: tuple[World, dict[str, Any]]) -> None:
    world, env = kit_world
    wh, kit = env["wh"], env["kit"]
    order_data = {
        "party": str(env["customer"].id),
        "document_date": TODAY.isoformat(),
        "lines": [{"item_id": str(kit.id), "quantity": "2", "warehouse_id": str(wh.id)}],
    }
    order = world.run("admin", lambda u: documents.create_order(u, world.company, order_data))
    world.run("admin", lambda u: documents.confirm_order(u, order, ""))
    delivery = world.run("admin", lambda u: posting.create_delivery(u, world.company, {"order": str(order.id), "warehouse_id": str(wh.id)}))
    done = world.run("admin", lambda u: posting.validate_delivery(u, delivery))
    assert Decimal(done.cost_total) == Decimal(4 * 100 + 2 * 250)
    assert _qty(world, env["a"]) == Decimal(6) and _qty(world, env["b"]) == Decimal(3)
    gl = _gl(world, done.id)
    assert gl["613595"] == Decimal(900) and gl["143505"] == Decimal(-900)
    # a kit whose component is short fails as a whole at delivery (nothing issued, nothing posted)
    big = {**order_data, "lines": [{"item_id": str(kit.id), "quantity": "4", "warehouse_id": str(wh.id)}]}
    o2 = world.run("admin", lambda u: documents.create_order(u, world.company, big))
    world.run("admin", lambda u: documents.confirm_order(u, o2, ""))
    d2 = world.run("admin", lambda u: posting.create_delivery(u, world.company, {"order": str(o2.id), "warehouse_id": str(wh.id)}))
    with pytest.raises(DomainError):
        world.run("admin", lambda u: posting.validate_delivery(u, d2))
    assert _gl(world, d2.id) == {}
    assert _qty(world, env["a"]) == Decimal(6) and _qty(world, env["b"]) == Decimal(3)
