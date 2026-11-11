"""P22c — feature e2e (gate G3) of Compras: T-INV-REQ-07 (Solicitudes internas, four tabs) and T-PUR-CYCLE-08
(Cotizaciones: comparison screen and award flow).

Runner: ``qa_harness.e2e`` (real Django on loopback over a real PostgreSQL world built with the P11b kit + G2 gate
services: P04 policy engine and local OIDC tokens, P09 inventory, P05 ledger, P11 purchasing). Specs:
``tests/e2e/features/T-INV-REQ-07.requisitions.spec.ts`` and ``tests/e2e/features/T-PUR-CYCLE-08.award.spec.ts``.
Synthetic data only (SINTÉTICO); the kit clock is frozen and process-global, so it also drives the server thread.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from qa_harness import e2e

import purchasing.requisitions  # noqa: F401  # P11b part migrations must be on the path before the test DB is created

pytestmark = pytest.mark.django_db(transaction=True)

IR = "/api/v1/purchasing/internal-requisitions"


def _requisition(w: Any, user: str, wh: Any, item: Any, qty: str, needed_by: str, *, approve: bool) -> dict[str, Any]:
    api = w.api(user)
    r = api.post(
        IR,
        {
            "document_date": "2026-11-11",
            "requesting_warehouse_id": str(wh.id),
            "needed_by": needed_by,
            "priority": "NORMAL",
            "lines": [{"line_no": 1, "item_id": str(item.id), "quantity": {"value": qty, "uom": "UND"}}],
        },
    )
    assert r.status_code == 201, r.content
    req: dict[str, Any] = r.json()
    r = api.post(f"{IR}/{req['id']}:submit")
    assert r.status_code == 200, r.content
    if approve:
        r = w.api("jefe").post(f"{IR}/{req['id']}:approve", {"reason": "ok (SINTÉTICO)"})
        assert r.status_code == 200, r.content
    return req


def world_inv_req(w: Any) -> dict[str, Any]:
    """Store TIENDA requests ARROZ-1K; CENTRAL (D-012 priority) will hold 4 units; PROV-ARROZ has an approved price
    of COP 2 500 (D-011 valid vendor price, far below the COP 5 000 000 limit).

    Before the UI flow: ``sol2`` already has an APPROVED requisition of 12 (same item, delivery warehouse and week,
    approved while CENTRAL was still empty -> all 12 to procure) and a SUBMITTED one of another item (stays in the
    approver's inbox). In the UI ``sol`` requests 10 for 2026-11-16 -> approval reserves 4 by transfer, 6 to procure,
    and consolidation groups 6 + 12 = 18 into one DRAFT purchase order."""
    from purchasing.requisitions import service
    from purchasing.requisitions.tests.kit import approved_price

    tienda, central = w.warehouse("TIENDA"), w.warehouse("CENTRAL")
    item, other = w.item("ARROZ-1K"), w.item("AZUCAR-1K")
    vendor = w.supplier("PROV-ARROZ")
    approved_price(w, item, vendor, "2500")  # advances the frozen clock past the RFQ deadline (2026-11-11)
    w.run(lambda u: service.configure(u, w.company(), warehouse_priority=[str(central.id)]), user="admin")
    sol2_req = _requisition(w, "sol2", tienda, item, "12", "2026-11-17", approve=True)
    pending = _requisition(w, "sol2", tienda, other, "3", "2026-11-18", approve=False)
    w.receive(item, central, "4")
    return {
        "user": "sol",
        "tokens": e2e.tokens(w, "sol", "jefe", "comprador", "bodega"),
        "data": {
            "company_id": str(w.company()),
            "item_id": str(item.id),
            "tienda_id": str(tienda.id),
            "central_id": str(central.id),
            "vendor_name": vendor.name,
            "sol2_number": sol2_req["number"],
            "pending_number": pending["number"],
        },
    }


def test_T_INV_REQ_07_ui_flow_across_the_four_tabs() -> None:
    e2e.run_feature("T-INV-REQ-07", world_inv_req, augment=True, known_gaps=("DEF-P22-050",))


def world_pur_cycle(w: Any) -> dict[str, Any]:
    """RFQ of 100 UND HARINA-1K sent to three vendors (buyer ``comprador``), three quotes registered before the
    deadline (A 3 000 / 5 days, B 2 900 / 3 days, C 3 500 / 2 days), deadline reached and the RFQ closed (D-010)."""
    from purchasing.requisitions.tests.kit import PUR, advance, quote_lines, rfq_sent

    item = w.item("HARINA-1K")
    vendors = [w.supplier(code) for code in ("PROV-A", "PROV-B", "PROV-C")]
    items = [(item, "100")]
    rfq = rfq_sent(w, items, vendors)
    buyer = w.api("comprador")
    for vendor, (price, lead) in zip(vendors, [("3000", 5), ("2900", 3), ("3500", 2)], strict=True):
        r = buyer.post(
            f"{PUR}/rfqs/{rfq['id']}/quotes",
            {
                "party": {"id": str(vendor.id)},
                "currency": "COP",
                "lead_time_days": lead,
                "lines": quote_lines(items, [price]),
            },
        )
        assert r.status_code == 201, r.content
    advance(dt.datetime.fromisoformat(rfq["deadline"]) + dt.timedelta(minutes=1))
    r = buyer.post(f"{PUR}/rfqs/{rfq['id']}:close")  # what the scheduled close job does at the deadline
    assert r.status_code == 200 and r.json()["status"] == "CLOSED", r.content
    return {
        "user": "comprador",
        "tokens": e2e.tokens(w, "comprador", "aprobador"),
        "data": {
            "company_id": str(w.company()),
            "rfq_id": rfq["id"],
            "rfq_number": rfq["number"],
            "vendors": {v.code: v.name for v in vendors},
        },
    }


def test_T_PUR_CYCLE_08_ui_comparison_screen_and_award_flow() -> None:
    e2e.run_feature("T-PUR-CYCLE-08", world_pur_cycle, known_gaps=("DEF-P22-051",))
