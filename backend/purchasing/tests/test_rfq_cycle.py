"""Integration tests of the RFQ → quotes → comparison → award → approval → PO cycle through the public API
(real PostgreSQL with forced RLS; kernel contract doubles for policy, workflow, stock, posting, tax).

T-PUR-CYCLE-01: RFQ to 3 vendors, quotes received (one late), the deadline closes; the late quote is
excluded unless the RFQ is reopened.
T-PUR-CYCLE-04: an award different from the recommendation requires a reason; the award approval (SoD) is
required before purchase orders exist.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from kernel import clock
from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from purchasing.models import Award, PurchaseOrder, Rfq, VendorQuote
from purchasing.services import rfq as rfq_svc
from purchasing.testing.support import NOW, ApiClient, World, advance

DEADLINE = NOW + dt.timedelta(days=2)


def suppliers(world: World) -> list[Any]:
    return [
        world.supplier("PROV-A", "Proveedor A (SINTÉTICO)", rating="4", terms=30),
        world.supplier("PROV-B", "Proveedor B (SINTÉTICO)", rating="3", terms=60),
        world.supplier("PROV-C", "Proveedor C (SINTÉTICO)", rating="5", terms=30),
    ]


def create_rfq(api: ApiClient, vendors: list[Any], *, deadline: dt.datetime = DEADLINE) -> dict[str, Any]:
    r = api.as_("buyer").post(
        "rfqs",
        {
            "document_date": "2026-11-09",
            "deadline": deadline.isoformat(),
            "currency": "COP",
            "invited_vendors": [str(v.id) for v in vendors],
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(uuid.uuid5(uuid.NAMESPACE_OID, "harina")),
                    "description": "Harina (SINTÉTICO)",
                    "quantity": {"value": "100", "uom": "KG"},
                },
                {
                    "line_no": 2,
                    "item_id": str(uuid.uuid5(uuid.NAMESPACE_OID, "azucar")),
                    "description": "Azúcar (SINTÉTICO)",
                    "quantity": {"value": "50", "uom": "KG"},
                },
            ],
        },
    )
    assert r.status_code == 201, r.content
    return r.json()


def quote_body(vendor: Any, p1: str, p2: str, *, lead: int, terms: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "party": {"id": str(vendor.id)},
        "currency": "COP",
        "lead_time_days": lead,
        "lines": [
            {"line_no": 1, "quantity": {"value": "100", "uom": "KG"}, "unit_price": {"amount": p1, "currency": "COP"}},
            {"line_no": 2, "quantity": {"value": "50", "uom": "KG"}, "unit_price": {"amount": p2, "currency": "COP"}},
        ],
    }
    if terms is not None:
        body["payment_terms_days"] = terms
    return body


def run_to_evaluated(api: ApiClient, world: World) -> tuple[dict[str, Any], list[Any], dict[str, str]]:
    vendors = suppliers(world)
    rfq = create_rfq(api, vendors)
    assert api.post(f"rfqs/{rfq['id']}:send").status_code == 200
    quotes = {}
    for vendor, (p1, p2, lead) in zip(
        vendors, [("3000", "4000", 5), ("2900", "4100", 3), ("3500", "4500", 2)], strict=True
    ):
        r = api.post(f"rfqs/{rfq['id']}/quotes", quote_body(vendor, p1, p2, lead=lead))
        assert r.status_code == 201, r.content
        quotes[vendor.code] = r.json()["id"]
    advance(DEADLINE + dt.timedelta(minutes=1))
    assert api.post(f"rfqs/{rfq['id']}:close").status_code == 200
    r = api.post(f"rfqs/{rfq['id']}:evaluate")
    assert r.status_code == 200, r.content
    return rfq, vendors, quotes


def test_T_PUR_CYCLE_01_late_quote_excluded_unless_reopened(api: ApiClient, world: World) -> None:
    vendors = suppliers(world)
    rfq = create_rfq(api, vendors)
    rid = rfq["id"]
    assert rfq["status"] == "DRAFT" and len(rfq["invited_vendors"]) == 3 and "warnings" not in rfq

    # Quotes are only accepted once the RFQ is sent.
    assert api.post(f"rfqs/{rid}/quotes", quote_body(vendors[0], "3000", "4000", lead=5)).status_code == 409
    assert api.post(f"rfqs/{rid}:send").json()["status"] == "SENT"

    # Two quotes before the deadline.
    a = api.post(f"rfqs/{rid}/quotes", quote_body(vendors[0], "3000", "4000", lead=5))
    b = api.post(f"rfqs/{rid}/quotes", quote_body(vendors[1], "2900", "4100", lead=3))
    assert a.status_code == b.status_code == 201
    assert a.json()["quote_status"] == b.json()["quote_status"] == "RECEIVED"
    # A vendor that was not invited is refused; the RFQ cannot close before its deadline.
    stranger = world.supplier("PROV-X", "No invitado (SINTÉTICO)")
    assert api.post(f"rfqs/{rid}/quotes", quote_body(stranger, "1", "1", lead=1)).status_code == 422
    early = api.post(f"rfqs/{rid}:close")
    assert early.status_code == 409 and early.json()["code"] == "invalid_state_transition"

    # The deadline passes: the third quote is stored LATE (warning quote_late, D-010) and the RFQ closes.
    advance(DEADLINE + dt.timedelta(hours=1))
    late = api.post(f"rfqs/{rid}/quotes", quote_body(vendors[2], "1000", "1000", lead=1))
    assert late.status_code == 201, late.content
    assert late.json()["quote_status"] == "LATE" and late.json()["warnings"] == ["quote_late"]
    # A quote cannot be revised after the deadline.
    revise = api.send(
        "patch",
        f"rfqs/{rid}/quotes/{a.json()['id']}",
        {"lead_time_days": 1},
        if_match=api.get(f"rfqs/{rid}/quotes/{a.json()['id']}")["ETag"],
    )
    assert revise.status_code in (409, 422)
    assert api.post(f"rfqs/{rid}:close").json()["status"] == "CLOSED"

    evaluated = api.post(f"rfqs/{rid}:evaluate")
    assert evaluated.json()["status"] == "EVALUATED"
    comparison = api.get(f"rfqs/{rid}/comparison").json()
    assert [r["vendor_code"] for r in comparison["ranked"]] == ["PROV-B", "PROV-A"]
    assert [(e["vendor_code"], e["reason"]) for e in comparison["excluded"]] == [("PROV-C", "LATE")]
    assert "Excluida: PROV-C (cotización tardía, no puntúa (D-010))." in comparison["explanation"]

    # The late quote cannot be awarded while excluded.
    manager = api.as_("manager")
    blocked = manager.post(f"rfqs/{rid}:award", {"reason": "más barata", "parameters": {"quote_id": late.json()["id"]}})
    assert blocked.status_code == 409 and blocked.json()["code"] == "invalid_state_transition"

    # Reopen needs a reason and a future deadline; it readmits the LATE quote.
    buyer = api.as_("buyer")
    no_reason = buyer.post(
        f"rfqs/{rid}:reopen", {"parameters": {"deadline": (clock.now() + dt.timedelta(days=1)).isoformat()}}
    )
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "reason_required"
    past = buyer.post(f"rfqs/{rid}:reopen", {"reason": "x", "parameters": {"deadline": NOW.isoformat()}})
    assert past.json()["code"] == "rfq_deadline_passed"
    new_deadline = clock.now() + dt.timedelta(days=1)
    reopened = buyer.post(
        f"rfqs/{rid}:reopen",
        {
            "reason": "Proveedor C reportó falla del portal (SINTÉTICO)",
            "parameters": {"deadline": new_deadline.isoformat()},
        },
    )
    assert reopened.status_code == 200 and reopened.json()["status"] == "SENT"
    assert reopened.json()["reopen_count"] == 1
    assert buyer.get(f"rfqs/{rid}/quotes/{late.json()['id']}").json()["quote_status"] == "RECEIVED"

    advance(new_deadline + dt.timedelta(minutes=1))
    assert buyer.post(f"rfqs/{rid}:close").status_code == 200
    assert buyer.post(f"rfqs/{rid}:evaluate").status_code == 200
    second = buyer.get(f"rfqs/{rid}/comparison").json()
    assert second["sequence"] == 2 and second["excluded"] == []
    assert second["ranked"][0]["vendor_code"] == "PROV-C"
    # The first snapshot is immutable and still there.
    with bound(world.ctx("buyer")):
        assert Rfq.objects.get(id=rid).comparisons.count() == 2


def test_T_PUR_CYCLE_01_rfq_needs_an_invited_vendor_and_warns_below_three(api: ApiClient, world: World) -> None:
    vendors = suppliers(world)
    none = api.as_("buyer").post(
        "rfqs",
        {
            "document_date": "2026-11-09",
            "deadline": DEADLINE.isoformat(),
            "invited_vendors": [],
            "lines": [{"line_no": 1, "quantity": {"value": "1", "uom": "UND"}}],
        },
    )
    assert none.status_code == 422
    one = create_rfq(api, vendors[:1])
    assert one["warnings"] == ["fewer_than_3_vendors"]


def test_T_PUR_CYCLE_01_default_deadline_is_five_bogota_business_days(world: World) -> None:
    """D-010: Fri 2026-11-13 + 5 business days skips the weekend and the Monday holiday 2026-11-16
    (Independencia de Cartagena, moved by Ley 51/1983) → Mon 2026-11-23, end of day in Bogotá."""
    bogota = clock.tz("America/Bogota")
    deadline = rfq_svc.default_deadline(dt.date(2026, 11, 13), 5).astimezone(bogota)
    assert deadline.date() == dt.date(2026, 11, 23)
    assert deadline.time() == dt.time(23, 59, 59)
    # Without a holiday in between: Mon 2026-11-23 + 5 → Mon 2026-11-30.
    assert rfq_svc.default_deadline(dt.date(2026, 11, 23), 5).astimezone(bogota).date() == dt.date(2026, 11, 30)


def test_T_PUR_CYCLE_01_scheduled_close_job_closes_due_rfqs(api: ApiClient, world: World) -> None:
    vendors = suppliers(world)
    rid = create_rfq(api, vendors)["id"]
    api.post(f"rfqs/{rid}:send")
    advance(DEADLINE + dt.timedelta(seconds=1))
    closed = run_in_unit_of_work(lambda u: rfq_svc.close_due_rfqs(u, world.company), world.ctx("buyer"))
    assert [str(x) for x in closed] == [rid]
    assert api.get(f"rfqs/{rid}").json()["status"] == "CLOSED"


def test_T_PUR_CYCLE_04_award_reason_and_approval_before_purchase_orders(api: ApiClient, world: World) -> None:
    rfq, _vendors, quotes = run_to_evaluated(api, world)
    rid = rfq["id"]
    comparison = api.get(f"rfqs/{rid}/comparison").json()
    recommended = comparison["recommended_quote_id"]
    assert recommended == quotes[comparison["ranked"][0]["vendor_code"]]
    other = next(q for q in quotes.values() if q != recommended)

    # A buyer (comprador role) cannot award at all: purchasing.rfq.approve is denied by default.
    denied = api.as_("buyer").post(f"rfqs/{rid}:award", {"parameters": {"quote_id": recommended}})
    assert denied.status_code in (403, 404)

    manager = api.as_("manager")
    missing = manager.post(f"rfqs/{rid}:award", {"parameters": {"quote_id": other}})
    assert missing.status_code == 422 and missing.json()["code"] == "award_reason_required"

    awarded = manager.post(
        f"rfqs/{rid}:award", {"reason": "Mejor historial de calidad (SINTÉTICO)", "parameters": {"quote_id": other}}
    )
    assert awarded.status_code == 200, awarded.content
    body = awarded.json()
    assert body["status"] == "AWARD_PENDING_APPROVAL"
    assert body["award"]["differs_from_recommendation"] is True and body["award"]["status"] == "PENDING_APPROVAL"

    # No purchase order exists before the approval, and none can be created.
    with bound(world.ctx("buyer")):
        assert not PurchaseOrder.objects.filter(rfq_id=rid).exists()
    award_id = body["award"]["id"]

    def early(u: Any) -> Any:
        return rfq_svc.create_orders_from_award(u, Award.objects.get(id=award_id))

    from purchasing.errors import AwardNotApproved

    with pytest.raises(AwardNotApproved):
        run_in_unit_of_work(early, world.ctx("approver"))

    # SoD: neither the buyer of the RFQ nor the proposer of the award approves it.
    sod = manager.post(f"rfqs/{rid}:approve-award", {"reason": "ok"})
    assert sod.status_code == 403 and sod.json()["code"] == "sod_violation"
    reject_without_reason = api.as_("approver").post(f"rfqs/{rid}:approve-award", {"parameters": {"approve": False}})
    assert reject_without_reason.json()["code"] == "reason_required"

    approved = api.as_("approver").post(f"rfqs/{rid}:approve-award", {"reason": "Aprobado (SINTÉTICO)"})
    assert approved.status_code == 200, approved.content
    assert approved.json()["status"] == "PO_CREATED"
    assert approved.json()["awarded_quote_id"] == other
    with bound(world.ctx("buyer")):
        orders = list(PurchaseOrder.objects.filter(rfq_id=rid))
        assert (
            len(orders) == 1
            and orders[0].status == "DRAFT"
            and orders[0].supplier_id == VendorQuote.objects.get(id=other).supplier_id
        )
        lines = {line.line_no: line for line in orders[0].lines.all()}
        assert lines[1].quantity == Decimal("100") and lines[1].uom == "KG"
        events = OutboxMessage.objects.filter(event_type="purchasing.rfq.awarded.v1")
        payload = events.get().envelope["payload"]
        assert payload["quote_id"] == other and payload["reason"] == "Mejor historial de calidad (SINTÉTICO)"
        assert VendorQuote.objects.get(id=other).quote_status == "AWARDED"


def test_T_PUR_CYCLE_04_award_matching_the_recommendation_needs_no_reason_and_rejection_returns(
    api: ApiClient, world: World
) -> None:
    rfq, _vendors, _quotes = run_to_evaluated(api, world)
    rid = rfq["id"]
    recommended = api.get(f"rfqs/{rid}/comparison").json()["recommended_quote_id"]
    awarded = api.as_("manager").post(f"rfqs/{rid}:award", {"parameters": {"quote_id": recommended}})
    assert awarded.status_code == 200 and awarded.json()["award"]["differs_from_recommendation"] is False
    rejected = api.as_("approver").post(
        f"rfqs/{rid}:approve-award", {"reason": "Presupuesto agotado (SINTÉTICO)", "parameters": {"approve": False}}
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "EVALUATED"
    assert rejected.json()["award"]["status"] == "REJECTED"
    with bound(world.ctx("buyer")):
        assert not PurchaseOrder.objects.filter(rfq_id=rid).exists()


def test_T_PUR_CYCLE_04_split_award_creates_one_order_per_vendor(api: ApiClient, world: World) -> None:
    rfq, _vendors, quotes = run_to_evaluated(api, world)
    rid = rfq["id"]
    lines = {line["line_no"]: line["id"] for line in rfq["lines"]}
    split = [
        {"rfq_line_id": lines[1], "quote_id": quotes["PROV-B"]},
        {"rfq_line_id": lines[2], "quote_id": quotes["PROV-A"]},
    ]
    r = api.as_("manager").post(
        f"rfqs/{rid}:award", {"reason": "Mejor precio por línea", "parameters": {"lines": split}}
    )
    assert r.status_code == 200, r.content
    assert api.as_("approver").post(f"rfqs/{rid}:approve-award", {"reason": "ok"}).json()["status"] == "PO_CREATED"
    with bound(world.ctx("buyer")):
        orders = PurchaseOrder.objects.filter(rfq_id=rid).order_by("number")
        assert {o.supplier.code for o in orders} == {"PROV-A", "PROV-B"}
        assert sorted(o.lines.count() for o in orders) == [1, 1]
