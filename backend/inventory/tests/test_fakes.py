"""The fake and the real StockService agree on shared vectors (quantities and WAC values); both satisfy the protocol."""

from __future__ import annotations

from decimal import Decimal

import pytest

from inventory.stock.service import SERVICE
from inventory.testing.fakes import FakeInventoryService
from kernel import ids
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.stock import Inventory, StockLine

pytestmark = pytest.mark.django_db
D = Decimal
VECTORS = [("in", "10", "1000"), ("in", "5", "1300"), ("out", "3", None), ("in", "7", "999.99"), ("out", "19", None)]


def test_fake_and_real_inventory_produce_identical_results(inv):
    assert isinstance(SERVICE, Inventory) and isinstance(FakeInventoryService(), Inventory)
    wh = inv.warehouse("WHF")
    item = inv.item("P-FAKE")
    alm = inv.bin(wh, "ALMACEN")
    fake = FakeInventoryService({wh.id: inv.company()})
    for kind, qty, cost in VECTORS:
        doc = DocumentRef("test.vector", ids.new_id())
        line = StockLine("1", item.id, wh.id, D(qty), alm.id, None, None, D(cost) if cost else None)
        if kind == "in":
            real = inv.receive(line, source=doc)
            faked = inv.run(lambda u, line=line, doc=doc: fake.receive(u, inv.company(), doc, [line], inv_today()))
        else:
            real = inv.issue(line, source=doc)
            faked = inv.run(lambda u, line=line, doc=doc: fake.issue(u, inv.company(), doc, [line], inv_today()))
        assert real.total_value == faked.total_value, (kind, qty)
    assert [a.on_hand for a in SERVICE.availability(inv.company(), item.id)] == [
        a.on_hand for a in fake.availability(inv.company(), item.id)
    ]


def inv_today():
    from inventory.tests.conftest import TODAY

    return TODAY
