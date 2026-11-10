"""T-INV-BOM-03 (integration): a kit sale issues its components at cost — REAL P09 stock + REAL P05 ledger.

The KIT BOM itself belongs to P09c (F-INV-BOM); the explosion is reached through the ``KitExplosion`` port
(CCR P10a-2). Here a synthetic implementation of the port answers the BOM; everything else is real: the
inventory stock service values and moves the components, the ledger posts Dr COGS / Cr INVENTORY and the AR /
revenue / VAT entry, all in the same unit of work as the sales document.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum

from inventory.models import Item, ItemValuation
from kernel.errors import DomainError
from kernel.testing.tenancy import bound
from ledger.models import JournalLine
from sales.models import Invoice
from sales.services import documents, posting
from sales.testing.support import TODAY, World

pytestmark = pytest.mark.django_db


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


def test_T_INV_BOM_03_kit_delivery_from_order_and_insufficient_component(
    kit_world: tuple[World, dict[str, Any]],
) -> None:
    world, env = kit_world
    wh, kit = env["wh"], env["kit"]
    order_data = {
        "party": str(env["customer"].id),
        "document_date": TODAY.isoformat(),
        "lines": [{"item_id": str(kit.id), "quantity": "2", "warehouse_id": str(wh.id)}],
    }
    order = world.run("admin", lambda u: documents.create_order(u, world.company, order_data))
    world.run("admin", lambda u: documents.confirm_order(u, order, ""))
    delivery = world.run(
        "admin",
        lambda u: posting.create_delivery(u, world.company, {"order": str(order.id), "warehouse_id": str(wh.id)}),
    )
    done = world.run("admin", lambda u: posting.validate_delivery(u, delivery))
    assert Decimal(done.cost_total) == Decimal(4 * 100 + 2 * 250)
    assert _qty(world, env["a"]) == Decimal(6) and _qty(world, env["b"]) == Decimal(3)
    gl = _gl(world, done.id)
    assert gl["613595"] == Decimal(900) and gl["143505"] == Decimal(-900)
    # a kit whose component is short fails as a whole at delivery (nothing issued, nothing posted)
    big = {**order_data, "lines": [{"item_id": str(kit.id), "quantity": "4", "warehouse_id": str(wh.id)}]}
    o2 = world.run("admin", lambda u: documents.create_order(u, world.company, big))
    world.run("admin", lambda u: documents.confirm_order(u, o2, ""))
    d2 = world.run(
        "admin", lambda u: posting.create_delivery(u, world.company, {"order": str(o2.id), "warehouse_id": str(wh.id)})
    )
    with pytest.raises(DomainError):
        world.run("admin", lambda u: posting.validate_delivery(u, d2))
    assert _gl(world, d2.id) == {}
    assert _qty(world, env["a"]) == Decimal(6) and _qty(world, env["b"]) == Decimal(3)
