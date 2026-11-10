"""T-INV-REQ-02 — consolidation of 3 requests keeps lineage; Σ consolidated = Σ sources; split fulfilment closes
each source line correctly. T-INV-REQ-03 — auto-PO draft only within the D-011 limit and with a valid vendor price;
above the limit a buyer task; the PO still needs approval.

Real P11a purchasing (RFQ → award → order → receipt), real P09 inventory (transfers ship/receive through
in-transit), real P05 ledger, real policy engine."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from inventory.models import Transfer
from inventory.transfers import service as transfers
from purchasing.models import PurchaseOrder
from purchasing.requisitions import service
from purchasing.requisitions.models import BuyerTask, InternalRequisition, RequisitionLine
from purchasing.requisitions.tests.kit import P11bWorld, approved_price, order_sent, receive_order

D = Decimal
BASE = "/api/v1/purchasing/internal-requisitions"
CONS = "/api/v1/purchasing/consolidations"


def approved_requisition(w: P11bWorld, user: str, wh: Any, item: Any, qty: str, needed_by: str) -> str:
    api = w.api(user)
    r = api.post(
        BASE,
        {
            "document_date": "2026-11-09",
            "requesting_warehouse_id": str(wh.id),
            "needed_by": needed_by,
            "lines": [{"line_no": 1, "item_id": str(item.id), "quantity": {"value": qty, "uom": "UND"}}],
        },
    )
    assert r.status_code == 201, r.content
    rid = str(r.json()["id"])
    assert api.post(f"{BASE}/{rid}:submit").status_code == 200
    r = w.api("jefe").post(f"{BASE}/{rid}:approve", {"reason": "ok"})
    assert r.status_code == 200, r.content
    return rid


def line_of(w: P11bWorld, rid: str) -> RequisitionLine:
    with w.scope():
        line: RequisitionLine = RequisitionLine.objects.get(requisition_id=rid)
    return line


def status_of(w: P11bWorld, rid: str) -> str:
    with w.scope():
        return str(InternalRequisition.objects.get(id=rid).status)


def sync(w: P11bWorld, *rids: str) -> None:
    for rid in rids:
        with w.scope():
            req = InternalRequisition.objects.get(id=rid)
        w.run(lambda u, r=req: service.sync_fulfilment(u, r), user="comprador")


def test_T_INV_REQ_02_consolidation_keeps_lineage_and_split_fulfilment_closes_each_line(w: P11bWorld) -> None:
    tienda, central = w.warehouse("TIENDA"), w.warehouse("CENTRAL")
    item = w.item("ARROZ-1K")
    w.receive(item, central, "4")  # only 4 units in the company: A gets them by transfer
    vendor = w.supplier("PROV-ARROZ")
    approved_price(w, item, vendor, "2500")
    # three requisitions of the same item / week / delivery warehouse, different requesters and days
    a = approved_requisition(w, "sol", tienda, item, "10", "2026-11-16")
    b = approved_requisition(w, "sol2", tienda, item, "12", "2026-11-17")
    c = approved_requisition(w, "sol", tienda, item, "8", "2026-11-18")
    assert line_of(w, a).qty_transfer == D(4) and line_of(w, a).qty_procure == D(6)

    buyer = w.api("comprador")
    preview = buyer.post(f"{CONS}:run?dry_run=true")
    assert preview.status_code == 202, preview.content
    plan = preview.json()["lines"]
    assert len(plan) == 1 and plan[0]["quantity"] == "26.000000" and plan[0]["action"] == "DRAFT_PO"
    assert [s["qty"] for s in plan[0]["sources"]] == ["6.000000", "12.000000", "8.000000"]
    assert sum(D(s["qty"]) for s in plan[0]["sources"]) == D(plan[0]["quantity"])
    assert buyer.get(CONS).json()["items"] == []  # the dry run wrote nothing

    run = buyer.post(f"{CONS}:run?dry_run=false")
    assert run.status_code == 202 and run.json()["status"] == "SUCCEEDED", run.content
    href = run.json()["result_links"][0]["href"]
    batch = buyer.get(href).json()
    lineage = batch["lineage"]["1"]
    assert {s["requisition_id"] for s in lineage} == {a, b, c}
    assert sum(D(s["qty"]) for s in lineage) == D(batch["lines"]["1"]["quantity"]) == D(26)
    order_id = batch["lines"]["1"]["purchase_order_id"]
    for rid in (a, b, c):
        kinds = [e["kind"] for e in line_of(w, rid).lineage]
        assert "consolidation" in kinds and "purchase_order" in kinds
        assert line_of(w, rid).status == "IN_PURCHASE"
    assert buyer.post(f"{CONS}:run?dry_run=false").json()["lines"] == []  # nothing consolidated twice

    # split fulfilment: transfer of A (ship + receive through in-transit), then two partial PO receipts
    with w.scope():
        t = Transfer.objects.get(source_id=a)
    w.run(lambda u: transfers.receive(u, transfers.ship(u, transfers.approve(u, t))), user="bodega")
    order = order_sent(w, order_id)
    receive_order(w, order, tienda, "15")
    sync(w, a, b, c)
    # 15 received are split in needed-by order: A 6 (+4 transferred = 10 → FULFILLED), B 9, C 0
    assert (line_of(w, a).fulfilled_qty, line_of(w, a).status) == (D(10), "FULFILLED")
    assert (line_of(w, b).fulfilled_qty, line_of(w, b).status) == (D(9), "IN_PURCHASE")
    assert line_of(w, c).fulfilled_qty == D(0)
    assert status_of(w, a) == "FULFILLED" and status_of(w, b) == "IN_PROGRESS"
    receive_order(w, buyer.get(f"/api/v1/purchasing/orders/{order_id}").json(), tienda, "11")
    sync(w, a, b, c)
    assert [line_of(w, r).fulfilled_qty for r in (a, b, c)] == [D(10), D(12), D(8)]
    assert [status_of(w, r) for r in (a, b, c)] == ["FULFILLED", "FULFILLED", "FULFILLED"]
    for rid in (a, b, c):
        ln = line_of(w, rid)
        assert ln.qty_transfer + ln.qty_procure + ln.qty_cancelled == ln.base_qty


def test_T_INV_REQ_02_groups_follow_d031_keys(w: P11bWorld) -> None:
    tienda, norte = w.warehouse("TIENDA"), w.warehouse("NORTE")
    item = w.item("SAL-1K")
    approved_requisition(w, "sol", tienda, item, "3", "2026-11-16")
    approved_requisition(w, "sol", tienda, item, "4", "2026-11-23")  # next week → own group
    approved_requisition(w, "sol", norte, item, "5", "2026-11-16")  # other delivery warehouse → own group
    plan = w.api("comprador").post(f"{CONS}:run?dry_run=true").json()["lines"]
    assert sorted((p["needed_week"], p["quantity"]) for p in plan) == [
        ("2026-11-16", "3.000000"),
        ("2026-11-16", "5.000000"),
        ("2026-11-23", "4.000000"),
    ]
    assert all(p["action"] == "BUYER_TASK" and p["reason_code"] == "vendor_price_missing" for p in plan)


def test_T_INV_REQ_03_auto_po_only_within_limit_and_with_valid_price(w: P11bWorld) -> None:
    tienda = w.warehouse("TIENDA")
    cheap, pricey, unpriced = w.item("TORNILLO"), w.item("MOTOR"), w.item("REPUESTO")
    vendor = w.supplier("PROV-FERR")
    approved_price(w, cheap, vendor, "1000")
    approved_price(w, pricey, vendor, "900000")
    approved_requisition(w, "sol", tienda, cheap, "100", "2026-11-20")  # 100 000 COP ≤ limit
    approved_requisition(w, "sol", tienda, pricey, "6", "2026-11-20")  # 5 400 000 COP > 5 000 000 (D-011)
    approved_requisition(w, "sol", tienda, unpriced, "2", "2026-11-20")  # no valid vendor price
    run = w.api("comprador").post(f"{CONS}:run?dry_run=false")
    assert run.status_code == 202, run.content
    body = run.json()
    assert [x["code"] for x in body["warnings"]] == ["auto_po_limit_exceeded"]
    actions = {p["item_id"]: (p["action"], p["reason_code"]) for p in body["lines"]}
    assert actions[str(cheap.id)] == ("DRAFT_PO", None)
    assert actions[str(pricey.id)] == ("BUYER_TASK", "auto_po_limit_exceeded")
    assert actions[str(unpriced.id)] == ("BUYER_TASK", "vendor_price_missing")
    with w.scope():
        tasks = {t.consolidation_line.item_id: t for t in BuyerTask.objects.select_related("consolidation_line")}
        drafts = list(PurchaseOrder.objects.filter(source__type="purchasing.consolidation"))
    assert set(tasks) == {pricey.id, unpriced.id}
    assert tasks[pricey.id].estimated_total == D("5400000.0000")
    # the automatic draft is a DRAFT order (never sent) that still needs the normal approval
    assert len(drafts) == 1 and drafts[0].status == "DRAFT" and drafts[0].sent_at is None
    oid = str(drafts[0].id)
    assert w.api("comprador").post(f"/api/v1/purchasing/orders/{oid}:send").status_code == 409
    r = w.api("comprador").post(f"/api/v1/purchasing/orders/{oid}:submit")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED"
    # a higher company limit lets the same remainder become a draft next time
    w.run(lambda u: service.configure(u, w.company(), auto_po_limit="6000000"), user="admin")
    approved_requisition(w, "sol", tienda, pricey, "6", "2026-11-20")
    again = w.api("comprador").post(f"{CONS}:run?dry_run=true").json()["lines"]
    assert [p["action"] for p in again] == ["DRAFT_PO"]


def test_T_INV_REQ_03_consolidation_needs_its_permission(w: P11bWorld) -> None:
    assert w.api("sol").post(f"{CONS}:run?dry_run=true").status_code == 403
