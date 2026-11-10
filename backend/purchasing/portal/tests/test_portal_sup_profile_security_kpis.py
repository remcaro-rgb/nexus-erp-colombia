"""T-PORTAL-SUP-03 — a bank-account change needs two internal approvals before the vendor master changes.
T-PORTAL-SUP-04 — portal responses never include internal prices of other vendors, costs or internal notes.
T-PORTAL-SUP-05 — the dashboard KPIs computed from synthetic data match direct queries."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest

from kernel import clock, ids, services
from kernel.errors import PermissionDenied
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.treasury import PaymentStatus, PaymentStatusInfo
from kernel.testing.fakes import FakePaymentStatus
from purchasing.models import ApOpenItem, PurchaseOrder, Rfq
from purchasing.portal import service
from purchasing.portal.models import SupplierMasterData, SupplierProfileChange
from purchasing.requisitions.tests.kit import P11bWorld, onboard_supplier, order_sent, quote_lines, rfq_sent

SP = "/api/portal/v1/supplier"
PUR = "/api/v1/purchasing"
D = Decimal


def change_of(w: P11bWorld, change_id: str) -> SupplierProfileChange:
    with w.scope():
        obj: SupplierProfileChange = SupplierProfileChange.objects.get(id=change_id)
    return obj


def decide(w: P11bWorld, user: str, change_id: str, approve: bool, reason: str = "verificado") -> SupplierProfileChange:
    done: SupplierProfileChange = w.run(
        lambda u: service.decide_profile_change(u, SupplierProfileChange.objects.get(id=change_id), approve, reason),
        user=user,
    )
    return done


# --------------------------------------------------------------------------- T-PORTAL-SUP-03


def test_T_PORTAL_SUP_03_bank_change_needs_two_different_internal_approvers(
    w: P11bWorld, fakes: dict[str, Any]
) -> None:
    supplier = w.supplier("PROV-BANCO")
    portal = onboard_supplier(w, fakes["notifications"], supplier, "prov-banco")
    assert portal.get(f"{SP}/profile").json()["bank_account"] is None

    r = portal.post(
        f"{SP}/profile/change-requests",
        {"reason": "Cambio de banco", "parameters": {"bank_account": "0123-4567-8901", "bank_name": "Banco SINT"}},
    )
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.content
    assert r.json()["data"]["required_approvals"] == 2
    change_id = r.json()["resource"]["id"]
    profile = portal.get(f"{SP}/profile").json()
    assert profile["pending_change"] is True and profile["bank_account"] is None  # master unchanged
    again = portal.post(f"{SP}/profile/change-requests", {"parameters": {"contacts": {"email": "x@example.invalid"}}})
    assert again.status_code == 409 and again.json()["code"] == "portal_profile_change_pending"

    # the clear number is never stored nor shown: only the encrypted value and the last 4 digits
    with w.scope():
        row = SupplierProfileChange.objects.get(id=change_id)
    assert "012345678901" not in json.dumps(row.requested) and "012345678901" not in str(row.bank_account_enc)
    assert row.requested["bank_account"] == "****8901"

    # first approval: still pending, master data untouched
    assert decide(w, "aprobador", change_id, True).status == "SUBMITTED"
    with w.scope():
        assert not SupplierMasterData.objects.filter(supplier_id=supplier.id).exists()
    # the same approver cannot give the second approval (SoD); people without the permission cannot decide
    with pytest.raises(PermissionDenied) as sod:
        decide(w, "aprobador", change_id, True)
    assert sod.value.code == "sod_violation"
    with pytest.raises(PermissionDenied):
        decide(w, "externo", change_id, True)
    # second, different approver → APPROVED and the vendor master changes
    assert decide(w, "tesorero", change_id, True).status == "APPROVED"
    profile = portal.get(f"{SP}/profile").json()
    assert profile["bank_account"] == "****8901" and profile["pending_change"] is False
    clear = w.run(lambda u: service.bank_account_of(u, supplier), user="admin")
    assert clear == "012345678901"


def test_T_PORTAL_SUP_03_other_changes_need_one_approval_and_rejection_keeps_the_master(
    w: P11bWorld, fakes: dict[str, Any]
) -> None:
    supplier = w.supplier("PROV-CONTACTO")
    portal = onboard_supplier(w, fakes["notifications"], supplier, "prov-contacto")
    r = portal.post(f"{SP}/profile/change-requests", {"parameters": {"bank_account": "99887766"}})
    rejected = decide(w, "aprobador", r.json()["resource"]["id"], False, "certificado bancario ilegible")
    assert rejected.status == "REJECTED"
    assert portal.get(f"{SP}/profile").json()["bank_account"] is None
    r = portal.post(f"{SP}/profile/change-requests", {"parameters": {"contacts": {"email": "ventas@example.invalid"}}})
    assert r.json()["data"]["required_approvals"] == 1
    assert decide(w, "tesorero", r.json()["resource"]["id"], True).status == "APPROVED"
    assert portal.get(f"{SP}/profile").json()["contacts"] == {"email": "ventas@example.invalid"}
    bad = portal.post(f"{SP}/profile/change-requests", {"parameters": {"bank_account": "12ab"}})
    assert bad.status_code == 422


# --------------------------------------------------------------------------- T-PORTAL-SUP-04

FORBIDDEN_KEYS = {
    "notes",
    "landed_total",
    "score",
    "scores",
    "rank",
    "recommended_quote_id",
    "recommendation",
    "unit_cost",
    "cost",
    "margin",
    "invited_vendors",
    "party",
    "supplier_id",
    "award_reason",
    "scoring_weights",
    "explanation",
    "balance_cop",
    "fx_source",
    "disqualified_reason",
}


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def test_T_PORTAL_SUP_04_portal_never_leaks_other_vendors_prices_costs_or_internal_notes(
    w: P11bWorld, fakes: dict[str, Any]
) -> None:
    a, b = w.supplier("PROV-A"), w.supplier("PROV-B")
    api_a = onboard_supplier(w, fakes["notifications"], a, "prov-a")
    api_b = onboard_supplier(w, fakes["notifications"], b, "prov-b")
    item = w.item("SECRETO")
    items = [(item, "10")]
    rfq = rfq_sent(w, items, [a, b])
    rid = rfq["id"]
    with w.scope():
        Rfq.objects.filter(id=rid).update(notes="NOTA-INTERNA-RFQ techo 9999.77")
    assert (
        api_a.post(f"{SP}/rfqs/{rid}/quotes", {"currency": "COP", "lines": quote_lines(items, ["5000"])}).status_code
        == 201
    )
    assert (
        api_b.post(f"{SP}/rfqs/{rid}/quotes", {"currency": "COP", "lines": quote_lines(items, ["4321.55"])}).status_code
        == 201
    )
    order = (
        w.api("comprador")
        .post(
            f"{PUR}/orders",
            {
                "party": {"id": str(a.id)},
                "currency": "COP",
                "notes": "NOTA-INTERNA-OC margen 37%",
                "lines": [
                    {
                        "line_no": 1,
                        "item_id": str(item.id),
                        "quantity": {"value": "10", "uom": "UND"},
                        "unit_price": {"amount": "5000", "currency": "COP"},
                    }
                ],
            },
        )
        .json()
    )
    order_sent(w, order["id"])
    with w.scope():
        ApOpenItem.objects.create(
            tenant_id=w.world.tenant_id,
            company_id=w.company(),
            supplier=a,
            document_type="purchasing.vendor_invoice",
            document_id=ids.new_id(),
            document_number="FP-SYN-1",
            document_date=clock.business_date(),
            due_date=clock.business_date() + dt.timedelta(days=30),
            currency="COP",
            amount=D("50000"),
            balance=D("50000"),
            amount_cop=D("50000"),
            balance_cop=D("50000"),
        )

    urls = [
        f"{SP}/profile",
        f"{SP}/rfqs",
        f"{SP}/rfqs/{rid}",
        f"{SP}/rfqs/{rid}/quotes",
        f"{SP}/purchase-orders",
        f"{SP}/purchase-orders/{order['id']}",
        f"{SP}/shipments",
        f"{SP}/invoices",
        f"{SP}/payments",
        f"{SP}/documents",
        f"{SP}/kpis",
    ]
    for url in urls:
        r = api_a.get(url)
        assert r.status_code == 200, (url, r.content)
        text = r.content.decode()
        leaked = keys_of(r.json()) & FORBIDDEN_KEYS
        assert not leaked, (url, leaked)
        for secret in ("4321.55", "NOTA-INTERNA", "9999.77", "37%", str(b.id), str(b.party_id), "PROV-B"):
            assert secret not in text, (url, secret)

    # scope parameters naming another party are refused; internal tokens are not portal tokens
    r = api_a.get(f"{SP}/purchase-orders", party_id=str(b.party_id))
    assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation"
    assert w.api("comprador").get(f"{SP}/rfqs").status_code in (401, 403)
    # the internal comparison still has both quotes: nothing was hidden from the buyer
    quotes = w.api("comprador").get(f"{PUR}/rfqs/{rid}/quotes").json()["items"]
    assert len(quotes) == 2


# --------------------------------------------------------------------------- T-PORTAL-SUP-05


def test_T_PORTAL_SUP_05_dashboard_kpis_match_direct_queries(w: P11bWorld, fakes: dict[str, Any]) -> None:
    a, b = w.supplier("PROV-KPI"), w.supplier("PROV-OTRO")
    api_a = onboard_supplier(w, fakes["notifications"], a, "prov-kpi")
    item = w.item("KPI-ITEM")
    items = [(item, "1")]
    rfq_sent(w, items, [a], deadline=clock.now() + dt.timedelta(days=3))
    rfq_sent(w, items, [a, b], deadline=clock.now() + dt.timedelta(days=1))
    rfq_sent(w, items, [b])  # not invited
    for supplier, qty in ((a, "3"), (a, "4"), (b, "5")):
        r = w.api("comprador").post(
            f"{PUR}/orders",
            {
                "party": {"id": str(supplier.id)},
                "currency": "COP",
                "lines": [
                    {
                        "line_no": 1,
                        "item_id": str(item.id),
                        "quantity": {"value": qty, "uom": "UND"},
                        "unit_price": {"amount": "100", "currency": "COP"},
                    }
                ],
            },
        )
        order_sent(w, r.json()["id"])
    first_po = api_a.get(f"{SP}/purchase-orders").json()["items"][0]["id"]
    assert api_a.post(f"{SP}/purchase-orders/{first_po}:acknowledge").status_code == 200

    payments = FakePaymentStatus()
    docs = []
    with w.scope():
        for supplier, amount, balance, status in (
            (a, "1000000", "1000000", "OPEN"),
            (a, "500000", "200000", "PARTIAL"),
            (a, "70000", "0", "PAID"),
            (b, "999999", "999999", "OPEN"),
        ):
            item_row = ApOpenItem.objects.create(
                tenant_id=w.world.tenant_id,
                company_id=w.company(),
                supplier=supplier,
                document_type="purchasing.vendor_invoice",
                document_id=ids.new_id(),
                document_number=f"FP-SYN-{len(docs) + 1}",
                document_date=clock.business_date(),
                due_date=clock.business_date(),
                currency="COP",
                amount=D(amount),
                balance=D(balance),
                amount_cop=D(amount),
                balance_cop=D(balance),
                status=status,
            )
            docs.append(item_row)

    def voucher(doc: ApOpenItem, amount: str, posted_on: dt.date | None, state: str = "POSTED") -> None:
        ref = DocumentRef(doc.document_type, doc.document_id, doc.document_number)
        info = PaymentStatusInfo(
            DocumentRef("treasury.ce", ids.new_id(), f"CE-{amount}"), state, D(amount), "COP", posted_on
        )  # type: ignore[arg-type]
        payments.add(ref, info)

    voucher(docs[1], "300000", dt.date(2026, 11, 5))  # in November
    voucher(docs[2], "70000", dt.date(2026, 10, 30))  # previous month
    voucher(docs[2], "1234", None, "APPROVED")  # not posted
    voucher(docs[3], "999999", dt.date(2026, 11, 6))  # other supplier

    with services.override(PaymentStatus, payments):
        body = api_a.get(f"{SP}/kpis", period="2026-11").json()
        october = api_a.get(f"{SP}/kpis", period="2026-10").json()
    got = {k["id"]: k["value"] for k in body["kpis"]}

    # the same numbers straight from the database (independent queries)
    with w.scope():
        today_start = clock.local_midnight_utc(clock.business_date())
        rfqs = Rfq.objects.filter(
            company_id=w.company(), status="SENT", deadline__gte=today_start, invited_vendors__contains=[str(a.id)]
        ).count()
        to_confirm = PurchaseOrder.objects.filter(company_id=w.company(), supplier=a, status="SENT").count()
        payable = sum((x.balance_cop for x in ApOpenItem.objects.filter(supplier=a).exclude(status="PAID")), D(0))
    assert got == {
        "sp_rfq_abiertas": str(rfqs),
        "sp_oc_por_confirmar": str(to_confirm),
        "sp_facturas_por_pagar": f"{payable:.4f}",
        "sp_pagos_periodo": "300000.0000",
    }
    assert (rfqs, to_confirm, payable) == (2, 1, D("1200000"))
    assert {k["id"]: k["value"] for k in october["kpis"]}["sp_pagos_periodo"] == "70000.0000"
    flow = next(k for k in body["kpis"] if k["id"] == "sp_pagos_periodo")
    assert (flow["previous"], flow["delta"], flow["state"]) == ("70000.0000", "230000.0000", "ok")
    assert body["period"] == {"start": "2026-11-01", "end": "2026-11-30"}
