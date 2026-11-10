"""T-INV-REQ-01 — request → approval (SoD) → sourcing: available stock reserved + DRAFT transfer from the priority
warehouse (D-012); the remainder becomes TO_PROCURE. Real policy engine, real P09 inventory, real PostgreSQL."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from purchasing.requisitions import service
from purchasing.requisitions.models import InternalRequisition, RequisitionLine, SourcingDecision
from purchasing.requisitions.testkit import P11bWorld

D = Decimal
BASE = "/api/v1/purchasing/internal-requisitions"


def stock_world(w: P11bWorld) -> dict[str, Any]:
    """Store TIENDA requests; CENTRAL (priority 1) has 6, NORTE has 30 — priority wins over quantity."""
    tienda, central, norte = w.warehouse("TIENDA"), w.warehouse("CENTRAL"), w.warehouse("NORTE")
    item = w.item("CAFE-500")
    w.receive(item, central, "6")
    w.receive(item, norte, "30")
    w.run(lambda u: service.configure(u, w.company(), warehouse_priority=[str(central.id)]), user="admin")
    return {"tienda": tienda, "central": central, "norte": norte, "item": item}


def request_body(s: dict[str, Any], qty: str) -> dict[str, Any]:
    return {
        "document_date": "2026-11-09",
        "requesting_warehouse_id": str(s["tienda"].id),
        "needed_by": "2026-11-13",
        "priority": "HIGH",
        "lines": [{"line_no": 1, "item_id": str(s["item"].id), "quantity": {"value": qty, "uom": "UND"}}],
    }


def create_and_submit(w: P11bWorld, s: dict[str, Any], qty: str, user: str = "sol") -> dict[str, Any]:
    api = w.api(user)
    r = api.post(BASE, request_body(s, qty))
    assert r.status_code == 201, r.content
    req = r.json()
    assert req["status"] == "DRAFT" and req["number"].startswith("SOL-")
    r = api.post(f"{BASE}/{req['id']}:submit")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.content
    return req


def test_T_INV_REQ_01_approval_with_sod_reserves_stock_and_leaves_the_rest_to_procure(w: P11bWorld) -> None:
    s = stock_world(w)
    req = create_and_submit(w, s, "50")
    rid = req["id"]

    # the requester can neither approve nor source the own requisition (SoD)
    r = w.api("sol").post(f"{BASE}/{rid}:approve", {"reason": "yo mismo"})
    assert r.status_code == 403
    # sourcing before approval is refused
    r = w.api("jefe").post(f"{BASE}/{rid}:source?mode=simulate")
    assert r.status_code == 422 and r.json()["code"] == "requisition_not_approved"

    r = w.api("jefe").post(f"{BASE}/{rid}:approve", {"reason": "ok"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["requisition_status"] == "IN_PROGRESS" and body["status"] == "APPROVED"
    line = body["lines"][0]
    # 50 requested: 6 from CENTRAL (priority list), 30 from NORTE (next: highest available), 14 to procure
    assert line["qty_transfer"] == "36.000000" and line["qty_procure"] == "14.000000"
    assert line["line_status"] == "TO_PROCURE"
    transfers = [e for e in line["lineage"] if e["kind"] == "transfer"]
    assert [(e["from_warehouse_id"], e["qty"]) for e in transfers] == [
        (str(s["central"].id), "6.000000"),
        (str(s["norte"].id), "30.000000"),
    ]

    # P09 side: reservations made, DRAFT-stage transfers referencing the requisition, nothing shipped
    assert w.available(s["item"], s["central"]) == D(0)
    assert w.available(s["item"], s["norte"]) == D(0)
    from inventory.models import Transfer

    with w.scope():
        rows = list(Transfer.objects.filter(source_id=rid).order_by("number"))
        decision = SourcingDecision.objects.get(line__requisition_id=rid)
    assert len(rows) == 2 and all(t.status in ("DRAFT", "SUBMITTED") for t in rows)
    assert {t.to_warehouse_id for t in rows} == {s["tienda"].id}
    assert decision.source == "MIXED" and decision.created_by_kind == "system-policy"
    assert decision.policy_ref == "D-012" and decision.qty_purchase == D(14)


def test_T_INV_REQ_01_simulate_writes_nothing_and_apply_is_idempotent_per_remaining(w: P11bWorld) -> None:
    s = stock_world(w)
    req = create_and_submit(w, s, "4")
    with w.scope():
        obj = InternalRequisition.objects.get(id=req["id"])
    # approve at service level without the automatic sourcing to exercise :source explicitly
    w.run(lambda u: service._decide(u, obj, True, "ok"), user="jefe")
    sim = w.api("jefe").post(f"{BASE}/{req['id']}:source?mode=simulate")
    assert sim.status_code == 200, sim.content
    plan = sim.json()["data"]["lines"][0]
    assert plan["source"] == "TRANSFER" and plan["transfer"][0]["from_warehouse_code"] == "CENTRAL"
    assert w.available(s["item"], s["central"]) == D(6)  # simulation reserved nothing

    applied = w.api("jefe").post(f"{BASE}/{req['id']}:source?mode=apply")
    assert applied.status_code == 200 and applied.json()["data"]["lines"][0]["status"] == "RESERVED"
    assert w.available(s["item"], s["central"]) == D(2)
    again = w.api("jefe").post(f"{BASE}/{req['id']}:source?mode=apply")
    assert again.status_code == 200 and again.json()["data"]["lines"] == []  # nothing left to source
    with w.scope():
        line = RequisitionLine.objects.get(requisition_id=req["id"])
    assert line.qty_transfer + line.qty_procure + line.qty_cancelled == line.base_qty == D(4)


def test_T_INV_REQ_01_transfers_stay_inside_the_company_and_foreign_ids_are_404(w: P11bWorld) -> None:
    s = stock_world(w)
    beta_wh = w.warehouse("BETA-1", company="BETA")
    w.receive(s["item"], s["central"], "1")
    body = request_body(s, "1")
    body["requesting_warehouse_id"] = str(beta_wh.id)
    r = w.api("sol").post(BASE, body)
    assert r.status_code == 422  # a warehouse of BETA is not a warehouse of ALFA
    req = create_and_submit(w, s, "2")
    assert w.api("sol", company="BETA").get(f"{BASE}/{req['id']}").status_code == 404
    assert w.api("externo").get(f"{BASE}/{req['id']}").status_code == 403
