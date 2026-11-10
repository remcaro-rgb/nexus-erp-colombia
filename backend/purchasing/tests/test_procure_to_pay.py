"""T-PUR-CYCLE-05 (integration): PO partial receipt then full receipt with the over-receipt tolerance,
then the three-way match with the vendor invoice, its approval on breach and the in-line posting.

Real PostgreSQL; StockService / PostingService / TaxService / WorkflowService are the kernel contract
doubles (balanced entries, no negative stock, fail-closed parameters, SoD).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from kernel.interfaces.common import DocumentRef
from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from purchasing.models import ApOpenItem, PurchaseOrder
from purchasing.services.common import configure
from purchasing.testing.support import ApiClient, World

ITEM = uuid.uuid5(uuid.NAMESPACE_OID, "harina-p2p")
WAREHOUSE = uuid.uuid5(uuid.NAMESPACE_OID, "bodega-principal")
CC = uuid.uuid5(uuid.NAMESPACE_OID, "cc-produccion")


def setup_order(
    api: ApiClient, world: World, *, receipt_tol: str = "5", price_tol: str = "2", cost_center: uuid.UUID = CC
) -> dict[str, Any]:
    run_in_unit_of_work(
        lambda u: configure(u, world.company, receipt_tolerance_pct=receipt_tol, invoice_price_tolerance_pct=price_tol),
        world.ctx("admin"),
    )
    vendor = world.supplier("PROV-P2P", "Molinos del Sur (SINTÉTICO)", terms=30)
    r = api.as_("buyer").post(
        "orders",
        {
            "document_date": "2026-11-09",
            "party": {"id": str(vendor.id)},
            "currency": "COP",
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(ITEM),
                    "description": "Harina (SINTÉTICO)",
                    "quantity": {"value": "100", "uom": "KG"},
                    "unit_price": {"amount": "3000", "currency": "COP"},
                    "tax_code": "IVA19",
                    "dimensions": {"cost_center_id": str(cost_center)},
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    order = r.json()
    assert order["status"] == "DRAFT" and order["total"] == {"amount": "300000.0000", "currency": "COP"}
    oid = order["id"]
    assert api.post(f"orders/{oid}:submit").json()["status"] == "SUBMITTED"
    sod = api.post(f"orders/{oid}:approve")  # the buyer has no approve grant → policy
    assert sod.status_code in (403, 404)
    manager = api.as_("manager")  # has the grant but did not create it
    assert manager.post(f"orders/{oid}:approve").json()["status"] == "APPROVED"
    assert api.as_("buyer").post(f"orders/{oid}:send").json()["status"] == "SENT"
    return api.get(f"orders/{oid}").json()


def receive(api: ApiClient, order: dict[str, Any], qty: str) -> Any:
    r = api.as_("warehouse").post(
        "receipts",
        {
            "document_date": "2026-11-10",
            "order": {"type": "purchasing.order", "id": order["id"]},
            "warehouse_id": str(WAREHOUSE),
            "lines": [{"order_line_id": order["lines"][0]["id"], "quantity": {"value": qty, "uom": "KG"}}],
        },
    )
    assert r.status_code == 201, r.content
    return r.json()


def entries_for(world: World, doc_type: str) -> list[Any]:
    return [req for req in world.fakes.posting.entries.values() if req.source.type == doc_type]


def lines_of(request: Any) -> list[tuple[str, str, str]]:
    return sorted((line.account_role, line.side, f"{line.amount:f}") for line in request.lines)


def test_T_PUR_CYCLE_05_partial_then_full_receipt_with_tolerance(api: ApiClient, world: World) -> None:
    order = setup_order(api, world)
    oid = order["id"]

    first = receive(api, order, "60")
    simulated = api.post(f"receipts/{first['id']}:simulate")
    assert simulated.status_code == 200
    assert simulated.json()["total_cop"] == "180000.0000" and not entries_for(world, "purchasing.receipt")
    validated = api.post(f"receipts/{first['id']}:validate")
    assert validated.status_code == 200, validated.content
    assert validated.json()["status"] == "POSTED" and validated.json()["receipt_status"] == "VALIDATED"
    assert api.get(f"orders/{oid}").json()["status"] == "PARTIALLY_RECEIVED"
    # Stock moved through StockService at the PO price (valuation layer 60 × 3,000).
    inv = world.fakes.inventory
    assert inv.on_hand[(ITEM, WAREHOUSE)] == Decimal("60") and inv.value[(ITEM, WAREHOUSE)] == Decimal("180000.0000")
    (entry,) = entries_for(world, "purchasing.receipt")
    assert lines_of(entry) == [("GRNI", "CREDIT", "180000.0000"), ("INVENTORY", "DEBIT", "180000.0000")]
    assert all(line.dimensions.cost_center_id == CC for line in entry.lines)

    # 60 + 46 = 106 > 100 + 5 % → refused, nothing moves.
    over = receive(api, order, "46")
    refused = api.post(f"receipts/{over['id']}:validate")
    assert refused.status_code == 422 and refused.json()["code"] == "receipt_over_tolerance"
    assert inv.on_hand[(ITEM, WAREHOUSE)] == Decimal("60") and len(entries_for(world, "purchasing.receipt")) == 1
    assert api.send("delete", f"receipts/{over['id']}").status_code == 204

    # 60 + 44 = 104 ≤ 105: accepted within the tolerance; the order is fully received.
    second = receive(api, order, "44")
    assert api.post(f"receipts/{second['id']}:validate").status_code == 200
    after = api.get(f"orders/{oid}").json()
    assert after["status"] == "RECEIVED" and after["lines"][0]["received_qty"] == "104.000000"
    assert inv.on_hand[(ITEM, WAREHOUSE)] == Decimal("104")
    # A validated receipt is immutable: it cannot be validated again or deleted.
    assert api.post(f"receipts/{second['id']}:validate").status_code == 409
    assert api.send("delete", f"receipts/{second['id']}").status_code == 409
    with bound(world.ctx("buyer")):
        events = OutboxMessage.objects.filter(event_type="purchasing.receipt.validated.v1").count()
    assert events == 2
    grni = api.as_("accountant").get("reports/grni", as_of="2026-11-30").json()
    assert grni["total"] == "312000.0000" and grni["rows"][0]["pending_qty"] == "104.000000"


def invoice(api: ApiClient, order: dict[str, Any], number: str, qty: str, price: str) -> dict[str, Any]:
    r = api.as_("accountant").post(
        "vendor-invoices",
        {
            "document_date": "2026-11-12",
            "party": order.get("party"),
            "vendor_invoice_number": number,
            "currency": "COP",
            "order": {"type": "purchasing.order", "id": order["id"]},
            "lines": [
                {
                    "order_line_id": order["lines"][0]["id"],
                    "quantity": {"value": qty, "uom": "KG"},
                    "unit_price": {"amount": price, "currency": "COP"},
                    "tax_code": "IVA19",
                    "withholding_codes": ["RTE25"],
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    return r.json()


def test_T_PUR_CYCLE_05_three_way_match_post_and_breach_approval(api: ApiClient, world: World) -> None:
    order = setup_order(api, world)
    oid = order["id"]
    for qty in ("60", "40"):
        rec = receive(api, order, qty)
        assert api.post(f"receipts/{rec['id']}:validate").status_code == 200
    accountant = api.as_("accountant")

    # Invoice 1: 60 kg at the PO price → MATCHED → posted.
    inv1 = invoice(api, order, "FV-SINT-001", "60", "3000")
    assert inv1["due_date"] == "2026-12-12"
    duplicate = accountant.post(
        "vendor-invoices",
        {
            "party": inv1["party"],
            "vendor_invoice_number": "fv-sint-001",
            "lines": [{"quantity": "1", "unit_price": "1"}],
        },
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "vendor_invoice_duplicate"
    matched = accountant.post(f"vendor-invoices/{inv1['id']}:match")
    assert matched.json()["match_status"] == "MATCHED"
    sim = accountant.post(f"vendor-invoices/{inv1['id']}:simulate").json()
    assert sim["totals"] == {
        "subtotal": "180000.0000",
        "taxes": "34200.0000",
        "withholdings": "4500.0000",
        "payable": "209700.0000",
        "payable_cop": "209700.0000",
        "currency": "COP",
    }
    posted = accountant.post(f"vendor-invoices/{inv1['id']}:post")
    assert posted.status_code == 200, posted.content
    assert posted.json()["status"] == "POSTED" and posted.json()["total"]["amount"] == "209700.0000"
    (entry,) = entries_for(world, "purchasing.vendor_invoice")
    assert lines_of(entry) == [
        ("AP", "CREDIT", "209700.0000"),
        ("GRNI", "DEBIT", "180000.0000"),
        ("VAT_RECOVERABLE", "DEBIT", "34200.0000"),
        ("WHT_PAYABLE", "CREDIT", "4500.0000"),
    ]
    # AP subledger = GL AP credit (invariant: AP = GL).
    items = accountant.get("ap/open-items").json()["items"]
    assert [(i["document"]["id"], i["balance_cop"]["amount"]) for i in items] == [(inv1["id"], "209700.0000")]
    assert accountant.post(f"vendor-invoices/{inv1['id']}:post").status_code == 409  # immutable once posted

    # Invoice 2: 50 kg (only 40 left to invoice) and +5 % price (tolerance 2 %) → MISMATCH, post refused.
    inv2 = invoice(api, order, "FV-SINT-002", "50", "3150")
    m2 = accountant.post(f"vendor-invoices/{inv2['id']}:match").json()
    assert m2["match_status"] == "MISMATCH"
    assert m2["match_result"]["lines"][0]["issues"] == ["quantity", "price"]
    refused = accountant.post(f"vendor-invoices/{inv2['id']}:post")
    assert refused.status_code == 422 and refused.json()["code"] == "three_way_mismatch"
    assert len(entries_for(world, "purchasing.vendor_invoice")) == 1
    assert accountant.send("delete", f"vendor-invoices/{inv2['id']}").status_code == 204

    # Invoice 3: 40 kg at +1 % (within the 2 % tolerance) → MATCHED; the price difference goes to PPV.
    inv3 = invoice(api, order, "FV-SINT-003", "40", "3030")
    assert accountant.post(f"vendor-invoices/{inv3['id']}:match").json()["match_status"] == "MATCHED"
    assert accountant.post(f"vendor-invoices/{inv3['id']}:post").status_code == 200
    entry3 = entries_for(world, "purchasing.vendor_invoice")[-1]
    # 40 × 3,030 = 121,200 net: GRNI cleared at the receipt value 120,000 + PPV 1,200; VAT 19 % 23,028;
    # retención 2.5 % (synthetic fixture rate) 3,030; AP = 120,000 + 1,200 + 23,028 − 3,030 = 141,198.
    assert lines_of(entry3) == [
        ("AP", "CREDIT", "141198.0000"),
        ("GRNI", "DEBIT", "120000.0000"),
        ("PURCHASE_PRICE_VARIANCE", "DEBIT", "1200.0000"),
        ("VAT_RECOVERABLE", "DEBIT", "23028.0000"),
        ("WHT_PAYABLE", "CREDIT", "3030.0000"),
    ]
    debit = sum(x.amount for x in entry3.lines if x.side == "DEBIT")
    credit = sum(x.amount for x in entry3.lines if x.side == "CREDIT")
    assert debit == credit
    # Fully received and invoiced → the order closes; GRNI is cleared; AP = Σ open items.
    assert api.as_("buyer").get(f"orders/{oid}").json()["status"] == "CLOSED"
    accountant = api.as_("accountant")  # the test client is shared: switch the principal back
    assert accountant.get("reports/grni", as_of="2026-11-30").json()["total"] == "0.0000"
    with bound(world.ctx("accountant")):
        open_cop = sum(i.balance_cop for i in ApOpenItem.objects.all())
    ap_credits = sum(
        x.amount for req in entries_for(world, "purchasing.vendor_invoice") for x in req.lines if x.account_role == "AP"
    )
    assert open_cop == ap_credits
    aging = accountant.get("ap/aging", as_of="2026-12-31").json()
    assert aging["total"] == f"{open_cop:f}" and aging["totals"]["1_30"] == f"{open_cop:f}"


def test_T_PUR_CYCLE_05_breach_needs_an_approval_by_someone_else(api: ApiClient, world: World) -> None:
    order = setup_order(api, world)
    rec = receive(api, order, "100")
    api.post(f"receipts/{rec['id']}:validate")
    accountant = api.as_("accountant")
    inv = invoice(api, order, "FV-SINT-010", "100", "3300")  # +10 % price
    matched = accountant.post(f"vendor-invoices/{inv['id']}:match").json()
    assert matched["match_status"] == "MISMATCH"
    assert accountant.post(f"vendor-invoices/{inv['id']}:post").json()["code"] == "three_way_mismatch"

    # The breach approval was requested in the same unit of work as the match; the requester cannot decide it.
    wf = world.fakes.workflow
    (request,) = [
        r
        for r in wf.requests.values()
        if r.document == DocumentRef("purchasing.vendor_invoice", uuid.UUID(inv["id"]), inv["number"])
    ]
    from kernel.errors import PermissionDenied

    try:
        run_in_unit_of_work(lambda u: wf.decide(u, request.id, True, "ok"), world.ctx("accountant"))
        raise AssertionError("SoD not enforced")
    except PermissionDenied as exc:
        assert exc.code == "sod_violation"
    run_in_unit_of_work(
        lambda u: wf.decide(u, request.id, True, "Alza de precio acordada (SINTÉTICO)"), world.ctx("manager")
    )
    posted = accountant.post(f"vendor-invoices/{inv['id']}:post")
    assert posted.status_code == 200, posted.content
    (entry,) = entries_for(world, "purchasing.vendor_invoice")
    assert ("PURCHASE_PRICE_VARIANCE", "DEBIT", "30000.0000") in lines_of(entry)
    with bound(world.ctx("buyer")):
        assert PurchaseOrder.objects.get(id=order["id"]).status == "CLOSED"
        payload = OutboxMessage.objects.get(event_type="purchasing.vendor_invoice.posted.v1").envelope["payload"]
    assert payload["vendor_invoice_id"] == inv["id"] and payload["total"]["currency"] == "COP"


def test_T_PUR_CYCLE_05_invoice_without_receipt_is_a_mismatch(api: ApiClient, world: World) -> None:
    order = setup_order(api, world)
    inv = invoice(api, order, "FV-SINT-020", "10", "3000")
    result = api.as_("accountant").post(f"vendor-invoices/{inv['id']}:match").json()
    assert result["match_result"]["lines"][0]["issues"] == ["quantity_not_received"]
    assert api.as_("accountant").post(f"vendor-invoices/{inv['id']}:post").json()["code"] == "three_way_mismatch"


def test_T_PUR_CYCLE_05_posting_failure_rolls_back_stock_gl_and_progress(api: ApiClient, world: World) -> None:
    """Failure injection: the posting service refuses → no stock move, no receipt state change, no event."""
    from kernel.errors import ValidationFailed

    order = setup_order(api, world)
    rec = receive(api, order, "10")

    class Broken:
        def post(self, uow: Any, request: Any) -> Any:
            raise ValidationFailed("period closed (injected)", code="period_closed")

        simulate = reverse = post

    from kernel import services
    from kernel.interfaces.posting import PostingService

    with services.override(PostingService, Broken()):  # type: ignore[arg-type]
        failed = api.as_("warehouse").post(f"receipts/{rec['id']}:validate")
    assert failed.status_code == 422 and failed.json()["code"] == "period_closed"
    assert api.get(f"receipts/{rec['id']}").json()["receipt_status"] == "DRAFT"
    assert api.as_("buyer").get(f"orders/{order['id']}").json()["lines"][0]["received_qty"] == "0.000000"
    with bound(world.ctx("buyer")):
        assert not OutboxMessage.objects.filter(event_type="purchasing.receipt.validated.v1").exists()
