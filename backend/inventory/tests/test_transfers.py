"""T-INV-REQ-04 — transfer ship/receive through in-transit; stock counted once; cross-company rejected; GL in-line."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.models import Sum

from inventory.models import ItemValuation, StockMove, StockQuant, Transfer
from inventory.stock.service import SERVICE
from kernel import ids
from kernel.interfaces.common import DocumentRef
from ledger.models import JournalEntry, JournalLine

pytestmark = pytest.mark.django_db
D = Decimal


def _totals(inv, item):
    with inv.scope():
        rows = StockQuant.objects.filter(item_id=item.id).select_related("location")
        by_type: dict[str, Decimal] = {}
        for q in rows:
            by_type[q.location.location_type] = by_type.get(q.location.location_type, D(0)) + q.on_hand
        total = rows.aggregate(t=Sum("on_hand"))["t"]
    return total, by_type


def test_T_INV_REQ_04_transfer_ship_receive_in_transit_counted_once_cross_company_rejected(inv, clerk, login, world):
    api = login(clerk)
    src = inv.warehouse("BOG1", branch="BOG")
    dst = inv.warehouse("MED1", branch="MED")
    item = inv.item("P-TRF")
    inv.receive(inv.line(item, src, "10", location=inv.bin(src, "ALMACEN"), cost="1500"))

    r = api.post(
        "/api/v1/inventory/transfers",
        {"from_warehouse_id": str(src.id), "to_warehouse_id": str(dst.id), "lines": [{"item_id": str(item.id), "quantity": "4"}]},
    )
    assert r.status_code == 201, r.content
    t = r.json()
    assert t["status"] == "DRAFT" and t["number"].startswith("TR-") and t["transit_location_id"] == str(src.transit_location_id)

    # submit reserves in the source warehouse (D-012)
    r = api.post(f"/api/v1/inventory/transfers/{t['id']}:submit")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.content
    [a] = SERVICE.availability(inv.company(), item.id, src.id)
    assert (a.on_hand, a.reserved, a.available) == (D(10), D(4), D(6))

    # ship: source bin → TRANSITO; on hand counted once (6 internal + 4 in transit = 10), not available anywhere
    r = api.post(f"/api/v1/inventory/transfers/{t['id']}:ship")
    assert r.status_code == 200, r.content
    shipped = r.json()
    assert shipped["status"] == "SHIPPED" and shipped["total"] == {"amount": "6000.0000", "currency": "COP"}
    total, by_type = _totals(inv, item)
    assert total == D(10) and by_type == {"INTERNAL": D(6), "TRANSIT": D(4)}
    assert [(x.warehouse_id, x.available) for x in SERVICE.availability(inv.company(), item.id)] == [(src.id, D(6))]

    # GL in the same transaction: Dr in-transit / Cr inventory, branch of the source warehouse
    with inv.scope():
        assert JournalEntry.objects.filter(id=shipped["ship_entry_id"], source_id=t["id"]).exists()
        entry = JournalEntry.objects.get(source_type="inventory.transfer", source_id=t["id"], purpose="ship", book__code="NIIF")
        assert entry.branch_id == src.branch_id
        lines = JournalLine.objects.filter(entry_id=entry.id)
        sides = sorted((ln.account.code, ln.debit, ln.credit, ln.branch_id) for ln in lines.select_related("account"))
    assert ("143505", D(0), D("6000.0000"), src.branch_id) in sides
    assert ("143595", D("6000.0000"), D(0), src.branch_id) in sides

    # receive: TRANSITO → destination (putaway RECEPCION); still 10 in total, counted once
    r = api.post(f"/api/v1/inventory/transfers/{t['id']}:receive")
    assert r.status_code == 200 and r.json()["status"] == "RECEIVED", r.content
    total, by_type = _totals(inv, item)
    assert total == D(10) and by_type == {"INTERNAL": D(10), "TRANSIT": D(0)}
    avail = {x.warehouse_id: x.on_hand for x in SERVICE.availability(inv.company(), item.id)}
    assert avail == {src.id: D(6), dst.id: D(4)}
    with inv.scope():
        recv = JournalEntry.objects.get(source_type="inventory.transfer", source_id=t["id"], purpose="receive", book__code="NIIF")
        assert {ln.branch_id for ln in JournalLine.objects.filter(entry_id=recv.id)} == {dst.branch_id}
        # company-wide valuation unchanged by the transfer
        v = ItemValuation.objects.get(item_id=item.id)
        assert (v.qty, v.value) == (D(10), D("15000.0000"))
        kinds = sorted(StockMove.objects.filter(source_id=t["id"]).values_list("kind", flat=True))
        # Σ GL over INVENTORY + INVENTORY_IN_TRANSIT: the transfer only moved value between the two roles
        # (the receipt's own Dr INVENTORY / Cr GRNI is posted by the purchasing caller, not by StockService)
        inv_bal = sum(
            (ln.debit - ln.credit)
            for ln in JournalLine.objects.filter(entry__book__code="NIIF", account__code__in=["143505", "143595"])
        )
    assert kinds == ["TRANSFER_IN", "TRANSFER_OUT"]
    assert inv_bal == D(0)

    # a received transfer cannot be shipped again
    r = api.post(f"/api/v1/inventory/transfers/{t['id']}:ship")
    assert r.status_code == 409 and r.json()["code"] == "invalid_state_transition"

    # cross-company: destination warehouse of BETA → transfer_cross_company (API and service)
    beta_wh = inv.warehouse("BETA1", company="BETA")
    r = api.post(
        "/api/v1/inventory/transfers",
        {"from_warehouse_id": str(src.id), "to_warehouse_id": str(beta_wh.id), "lines": [{"item_id": str(item.id), "quantity": "1"}]},
    )
    assert r.status_code == 422 and r.json()["code"] == "transfer_cross_company"
    with pytest.raises(Exception) as exc:
        inv.run(lambda u: SERVICE.create_transfer(u, inv.company(), DocumentRef("purchasing.requisition", ids.new_id()), src.id, beta_wh.id,
                                                  [inv.line(item, src, "1")]))  # fmt: skip
    assert exc.value.code == "transfer_cross_company"
    # an ALFA-only principal cannot even see the BETA warehouse: still rejected, not leaked
    with inv.scope():
        assert Transfer.objects.count() == 1

    # cancel releases the reservations
    tid = inv.run(lambda u: SERVICE.create_transfer(u, inv.company(), DocumentRef("purchasing.requisition", ids.new_id()), src.id, dst.id,
                                                    [inv.line(item, src, "5")]))  # fmt: skip
    assert SERVICE.availability(inv.company(), item.id, src.id)[0].available == D(1)
    r = api.post(f"/api/v1/inventory/transfers/{tid}:cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert SERVICE.availability(inv.company(), item.id, src.id)[0].available == D(6)
