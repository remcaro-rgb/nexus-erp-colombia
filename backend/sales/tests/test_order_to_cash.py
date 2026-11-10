"""Order to cash through the API on the REAL stock service and ledger (synthetic data):
quote → order (reservation) → delivery (COGS) → invoice (fiscal numbering, AR, VAT) → receipt → aging;
credit note against the invoice; idempotent replay; field security on AR reports."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kernel.testing.tenancy import bound
from sales.models import ArOpenItem
from sales.testing.support import TODAY, ApiClient, World

pytestmark = pytest.mark.django_db


@pytest.fixture
def o2c(kit_world: tuple[World, dict[str, Any]]) -> tuple[World, dict[str, Any], ApiClient]:
    world, env = kit_world
    world.base_price(env["a"].id, "300")
    return world, env, ApiClient(world)


def amt(value: Any) -> Decimal:
    return Decimal(value["amount"] if isinstance(value, dict) else value)


def _ok(resp: Any, code: int = 200) -> dict[str, Any]:
    assert resp.status_code == code, resp.content
    return resp.json()


def test_order_to_cash_api(o2c: tuple[World, dict[str, Any], ApiClient]) -> None:
    world, env, api = o2c
    wh, item, party = env["wh"], env["a"], env["customer"]
    line = {"item_id": str(item.id), "quantity": "4", "tax_code": "IVA19", "warehouse_id": str(wh.id)}
    quote = _ok(api.post("quotes", {"party": str(party.id), "document_date": TODAY.isoformat(), "lines": [line]}), 201)
    assert quote["document_status"] == "DRAFT" and quote["lines"][0]["price_source"] == "BASE"
    _ok(api.post(f"quotes/{quote['id']}:send", {}))
    order = _ok(api.post(f"quotes/{quote['id']}:to-order", {}), 201)
    confirmed = _ok(api.post(f"orders/{order['id']}:confirm", {}))
    assert (confirmed["status"], confirmed["document_status"]) == ("APPROVED", "CONFIRMED")
    delivery = _ok(api.post("deliveries", {"order": order["id"], "warehouse_id": str(wh.id)}), 201)
    done = _ok(api.post(f"deliveries/{delivery['id']}:validate", {}))
    assert amt(done["cost_total"]) == Decimal(400)  # 4 × average cost 100
    invoice = _ok(api.post(f"orders/{order['id']}:to-invoice", {}), 201)
    sim = _ok(api.post(f"invoices/{invoice['id']}:simulate", {}))
    assert sim["persisted"] is False
    key = "post-invoice-1"
    posted = _ok(api.post(f"invoices/{invoice['id']}:post", {}, key=key))
    assert posted["document_status"] == "POSTED" and posted["number"].startswith("SETT")
    assert amt(posted["total"]) == Decimal("1428.00")
    replay = api.post(f"invoices/{invoice['id']}:post", {}, key=key)
    assert replay.status_code == 200 and replay["Idempotent-Replayed"] == "true"
    items = _ok(api.get("ar/open-items", party=str(party.id)))["items"]
    assert len(items) == 1
    open_item = items[0]
    # partial credit note (price adjustment) then the receipt for the rest
    note = _ok(
        api.post(
            "credit-notes",
            {
                "original": invoice["id"],
                "reason": "rebaja",
                "reason_text": "descuento comercial",
                "lines": [{"invoice_line_id": posted["lines"][0]["id"], "quantity": "1"}],
            },
        ),
        201,
    )
    _ok(api.post(f"credit-notes/{note['id']}:post", {}))
    with bound(world.ctx("admin")):
        balance = ArOpenItem.objects.get(id=open_item["id"]).balance
    assert balance == Decimal("1071.00")  # 1428 − (300 + 19 %)
    receipt = _ok(
        api.post(
            "receipts",
            {
                "party": str(party.id),
                "amount": "1071.00",
                "applications": [{"open_item_id": open_item["id"], "amount": "1071.00"}],
            },
        ),
        201,
    )
    _ok(api.post(f"receipts/{receipt['id']}:post", {}))
    with bound(world.ctx("admin")):
        assert ArOpenItem.objects.get(id=open_item["id"]).status == ArOpenItem.Status.PAID
    aging = _ok(api.get("ar/aging", as_of=TODAY.isoformat()))
    assert aging is not None
    # AR balances are confidential: a principal without the field grant is refused (fail closed)
    world.fakes.policy.grant(world.ctx("seller").principal_id, "sales.ar_aging.read")
    denied = api.as_("seller").get("ar/aging")
    assert denied.status_code == 403 and denied.json()["code"] == "field_forbidden"


def test_security_deny_by_default_and_company_isolation(o2c: tuple[World, dict[str, Any], ApiClient]) -> None:
    _world, env, api = o2c
    assert api.as_("outsider").get("quotes").status_code == 403
    party = env["customer"]
    other = api.as_("other_admin").get(f"parties/{party.id}")
    assert other.status_code in (403, 404)
    seller_view = _ok(api.as_("seller").get(f"parties/{party.id}"))
    assert "credit_limit" not in seller_view and "nit" not in seller_view
    admin_view = _ok(api.as_("admin").get(f"parties/{party.id}"))
    assert "credit_limit" in admin_view and admin_view["nit"]
