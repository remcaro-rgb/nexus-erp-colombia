"""Treasury masters (bank account number masked), statement import + match by exact voucher id, KPI provider,
PaymentStatus for portals and the deny-by-default / tenant isolation of the module (synthetic data)."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest

from kernel.interfaces.common import DocumentRef
from kernel.testing.tenancy import bound
from treasury.interfaces import TreasuryPaymentStatus
from treasury.models import BankAccount, StatementLine
from treasury.testing.support import TODAY, ApiClient, World, money

D = Decimal
pytestmark = pytest.mark.django_db


def posted_receipt(world: World, api: ApiClient, amount: str) -> dict[str, Any]:
    customer = world.customer(f"C-{amount}")
    item = world.ar_item(customer, f"SETT-{amount}", amount)
    rc = api.post(
        "receipts",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": str(world.env["fund"].id),
            "payment_method": "TRANSFER",
            "party": {"id": str(customer.id), "kind": "customer"},
            "lines": [{"line_no": 1, "open_item_id": str(item.id), "applied_amount": money(amount)}],
        },
    ).json()
    out = api.post(f"receipts/{rc['id']}:post").json()
    out["_item"] = item
    return out


def test_bank_account_number_is_encrypted_and_masked_on_every_channel(world: World, api: ApiClient) -> None:
    admin = api.as_("admin")
    listing = admin.get("bank-accounts").json()["items"]
    assert [b["account_number"] for b in listing] == ["****6789"]
    with world.scope():
        row = BankAccount.objects.get(code="BCO-1")
    assert "123456789" not in row.account_number_enc and row.account_number_last4 == "6789"
    world.user("viewer", ("treasury.bank_account.read",), confidential=False)
    masked = api.as_("viewer").get(f"bank-accounts/{row.id}").json()
    assert "account_number" not in masked
    assert api.as_("outsider").get("bank-accounts").status_code == 403


def test_statement_import_and_match_by_exact_voucher_id(world: World, api: ApiClient) -> None:
    rc = posted_receipt(world, api, "750000")
    admin = api.as_("admin")
    csv = f"bank_account,date,description,reference,amount\nBCO-1,{TODAY},Consignación cliente,{rc['number']},750000\n"
    upload = io.BytesIO(csv.encode())
    upload.name = "extracto.csv"
    res = admin.client.post(
        "/api/v1/treasury/statements:import",
        {"file": upload},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="import-1",
    )
    assert res.status_code == 202, res.content
    lines = admin.get("statement-lines").json()["items"]
    assert len(lines) == 1 and lines[0]["matched"] is False
    snap = admin.get(f"funds/{world.env['fund'].id}/snapshot").json()["rows"][0]
    assert snap["unreconciled"] == "750000.0000"
    matched = admin.post(f"statement-lines/{lines[0]['id']}:match", {"parameters": {"voucher_id": rc["id"]}})
    assert matched.status_code == 200, matched.content
    assert matched.json()["matched"] is True
    snap = admin.get(f"funds/{world.env['fund'].id}/snapshot").json()["rows"][0]
    assert snap["unreconciled"] == "0.0000" and snap["book_balance"] == "750000.0000"
    twice = admin.post(f"statement-lines/{lines[0]['id']}:match", {"parameters": {"voucher_id": rc["id"]}})
    assert twice.status_code == 409 and twice.json()["code"] == "statement_line_already_matched"
    assert admin.post(f"statement-lines/{lines[0]['id']}:unmatch", {"reason": "error"}).status_code == 200
    auto = admin.post(f"statement-lines/{lines[0]['id']}:match")  # by reference = voucher number
    assert auto.status_code == 200 and auto.json()["matched"] is True
    with world.scope():
        assert StatementLine.objects.get(id=lines[0]["id"]).voucher_id is not None


def test_kpi_provider_is_scoped_and_policy_filtered(world: World, api: ApiClient) -> None:
    posted_receipt(world, api, "300000")
    world.user("boss", ("treasury.kpi.read", "treasury.reconcile"))
    body = api.as_("boss").client.get("/api/v1/kpis/tesoreria", {"period": "2026-11"}).json()
    by_id = {k["id"]: k for k in body["kpis"]}
    assert by_id["saldo_disponible"]["value"] == "300000.00"
    assert by_id["recaudos_periodo"]["value"] == "300000.00" and by_id["recaudos_periodo"]["previous"] == "0.00"
    assert by_id["pagos_periodo"]["state"] == "no_data"
    assert by_id["partidas_sin_conciliar"]["value"] == "0"
    assert "saldos_intercompania" not in by_id  # no consolidation.read
    panel = api.as_("boss").client.get("/api/v1/kpis/tesoreria/panels/flujo_caja", {"period": "2026-11"}).json()
    assert panel["points"][-1] == {"label": "2026-11", "values": {"recaudos": "300000.00", "pagos": "0.00"}}
    none = api.as_("outsider").client.get("/api/v1/kpis/tesoreria").json()
    assert none["kpis"] == []


def test_payment_status_for_portals_lists_the_vouchers_of_an_invoice(world: World, api: ApiClient) -> None:
    rc = posted_receipt(world, api, "120000")
    item = rc["_item"]
    with bound(world.ctx("cashier")):
        infos = TreasuryPaymentStatus().for_document(world.company, DocumentRef(item.document_type, item.document_id))
    assert [(i.voucher.number, i.state, i.amount) for i in infos] == [("RC-000001", "POSTED", D("120000.0000"))]


def test_other_tenant_and_company_never_see_treasury_rows(world: World, api: ApiClient) -> None:
    rc = posted_receipt(world, api, "50000")
    other = api.as_("other_admin")
    assert other.get("receipts").json()["items"] == []
    assert other.get(f"receipts/{rc['id']}").status_code == 404
    assert other.post(f"receipts/{rc['id']}:reverse", {"reason": "x"}).status_code == 404
    from rest_framework.test import APIClient

    assert APIClient().get("/api/v1/treasury/receipts").status_code == 401
