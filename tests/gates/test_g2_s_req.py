"""G2 gate test T-INV-REQ-06 (gate-owned, written by the wave-05 integrator; synthetic data, DEV only).

End-to-end S-REQ scenario on the integrated product: internal requisition → approval (SoD) → D-012 sourcing
(partial DRAFT transfer from the priority warehouse + remainder TO_PROCURE) → D-031 consolidation into a DRAFT PO
(D-011 auto-PO with a valid vendor price) → PO approval/send → transfer approve/ship/receive through in-transit and
PO receipt → requisition FULFILLED.

Real components: PostgreSQL with forced RLS, P04 policy engine + local OIDC bearer tokens, P09 inventory
(reservations, transfers, valuation), P05 ledger, P11a purchasing (RFQ, award, orders, receipts), P11b
requisitions. Contract doubles only for P07 workflow/audit/notifications, exactly as the P11b kit wires them.
Fulfilment is read through ``purchasing.requisitions.service.sync_fulfilment`` (the module's public service; no
API route exposes it yet).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.db.models import Sum

from iam.oidc import verifier
from iam.oidc.local import LocalIssuer
from iam.testing.world import make_world
from purchasing.requisitions.tests.kit import (
    ISSUER,
    P11bWorld,
    approved_price,
    make_p11b_world,
    order_sent,
    p11b_services,
    receive_order,
)

D = Decimal
BASE = "/api/v1/purchasing/internal-requisitions"
CONS = "/api/v1/purchasing/consolidations"
pytestmark = pytest.mark.django_db


@pytest.fixture
def w(db: Any) -> Iterator[P11bWorld]:
    issuer = LocalIssuer(ISSUER)
    verifier.set_key_provider(issuer, ISSUER)
    cache.clear()
    try:
        with p11b_services():
            yield make_p11b_world(make_world(), issuer)
    finally:
        verifier.set_key_provider(None)
        cache.clear()


def gl(w: P11bWorld) -> dict[str, Decimal]:
    from ledger.models import JournalLine

    with w.scope():
        rows = (
            JournalLine.objects.filter(company_id=w.company(), entry__book__primary=True)
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: r["d"] - r["c"] for r in rows}


def on_hand(w: P11bWorld, item: Any) -> dict[str, Decimal]:
    from inventory.stock.service import SERVICE

    out: dict[str, Decimal] = {}
    for code, wh in w.warehouses.items():
        with w.scope():
            rows = SERVICE.availability(w.company(), item.id, wh.id)
        out[code] = rows[0].on_hand if rows else D(0)
    return out


def test_T_INV_REQ_06_s_req_request_partial_transfer_and_po_receipt_fulfilled(w: P11bWorld) -> None:
    from inventory.models import Transfer
    from inventory.transfers import service as transfers
    from purchasing.requisitions import service
    from purchasing.requisitions.models import InternalRequisition, RequisitionLine

    tienda, central = w.warehouse("TIENDA"), w.warehouse("CENTRAL")
    item = w.item("ACEITE-1L")
    w.receive(item, central, "4", cost="8000")  # opening stock: 4 × 8,000 in CENTRAL only
    w.run(lambda u: service.configure(u, w.company(), warehouse_priority=[str(central.id)]), user="admin")
    vendor = w.supplier("PROV-ACEITE")
    approved_price(w, item, vendor, "8500")  # D-011 valid vendor price (approved award)
    assert gl(w) == {}  # the synthetic opening balance is stock only (no GL source document)

    # 1. request 10 → submit → approve (the requester cannot approve: SoD)
    api = w.api("sol")
    r = api.post(
        BASE,
        {
            "document_date": "2026-11-09",
            "requesting_warehouse_id": str(tienda.id),
            "needed_by": "2026-11-16",
            "priority": "HIGH",
            "lines": [{"line_no": 1, "item_id": str(item.id), "quantity": {"value": "10", "uom": "UND"}}],
        },
    )
    assert r.status_code == 201, r.content
    rid = r.json()["id"]
    assert api.post(f"{BASE}/{rid}:submit").json()["status"] == "SUBMITTED"
    assert w.api("sol").post(f"{BASE}/{rid}:approve", {"reason": "yo"}).status_code == 403
    r = w.api("jefe").post(f"{BASE}/{rid}:approve", {"reason": "ok"})
    assert r.status_code == 200, r.content
    line = r.json()["lines"][0]
    # 2. sourcing: 4 reserved by transfer from CENTRAL, 6 to procure
    assert (line["qty_transfer"], line["qty_procure"]) == ("4.000000", "6.000000")
    assert line["line_status"] == "TO_PROCURE"
    assert w.available(item, central) == D(0)  # reserved, still on hand

    # 3. consolidation → DRAFT PO for the remainder at the approved vendor price
    run = w.api("comprador").post(f"{CONS}:run?dry_run=false")
    assert run.status_code == 202 and run.json()["status"] == "SUCCEEDED", run.content
    batch = w.api("comprador").get(run.json()["result_links"][0]["href"]).json()
    assert batch["lines"]["1"]["quantity"] == "6.000000"
    order_id = batch["lines"]["1"]["purchase_order_id"]
    order = order_sent(w, order_id)  # normal approval (aprobador) before sending
    assert order["lines"][0]["unit_price"]["amount"].startswith("8500")
    with w.scope():
        ln = RequisitionLine.objects.get(requisition_id=rid)
    assert ln.status == "IN_PURCHASE" and {e["kind"] for e in ln.lineage} >= {"transfer", "purchase_order"}

    # 4a. transfer: approve → ship (into in-transit) → receive at TIENDA
    with w.scope():
        (t,) = Transfer.objects.filter(source_id=rid)
    shipped = w.run(lambda u: transfers.ship(u, transfers.approve(u, t)), user="bodega")
    mid = gl(w)
    assert mid == {"143505": D("-32000.0000"), "143595": D("32000.0000")}  # moved to in-transit, counted once
    assert sum(on_hand(w, item).values()) == D(0)
    w.run(lambda u: transfers.receive(u, shipped), user="bodega")
    # 4b. PO receipt of the 6 units into TIENDA
    receive_order(w, order, tienda, "6")

    # 5. fulfilment → requisition FULFILLED
    with w.scope():
        req = InternalRequisition.objects.get(id=rid)
    w.run(lambda u: service.sync_fulfilment(u, req), user="comprador")
    with w.scope():
        req = InternalRequisition.objects.get(id=rid)
        ln = RequisitionLine.objects.get(requisition_id=rid)
    assert req.status == "FULFILLED"
    assert (ln.status, ln.fulfilled_qty) == ("FULFILLED", D(10))
    assert ln.qty_transfer + ln.qty_procure + ln.qty_cancelled == ln.base_qty == D(10)
    detail = w.api("sol").get(f"{BASE}/{rid}")
    assert detail.status_code == 200 and detail.json()["requisition_status"] == "FULFILLED"

    # stock counted once; kardex value = GL inventory; in-transit cleared; GRNI = receipt value
    assert on_hand(w, item) == {"TIENDA": D(10), "CENTRAL": D(0)}
    end = gl(w)
    assert end.get("143595", D(0)) == D(0)
    assert end["143505"] == D("51000.0000")  # −32,000 shipped + 32,000 received + 6 × 8,500 PO receipt
    assert end["229505"] == D("-51000.0000")
    assert sum(end.values()) == D(0)
    from inventory.models import ItemValuation, StockMove

    with w.scope():
        val = ItemValuation.objects.get(item_id=item.id)
        kardex = sum((m.value for m in StockMove.objects.filter(item_id=item.id, state="done")), D(0))
    opening = D("32000.0000")  # the stock-only synthetic opening layer (no GL source)
    assert val.qty == D(10) and val.value == kardex == opening + end["143505"]
