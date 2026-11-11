"""T-SAL-PRICING-04 (security): customer-facing pricing never exposes cost or the formula text.

The customer-portal projections (``sales.interfaces``, frozen for P10b) are allowlists of the contract schemas;
internal users without the cost role get the confidential parts of the snapshot (``cost`` input, trace) masked.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from kernel.errors import NotFound, PermissionDenied
from sales import interfaces as portal
from sales.services import documents
from sales.testing.support import TODAY, ApiClient, World

pytestmark = pytest.mark.django_db
EXPRESSION = "round(cost * 1.35 + 17, 0)"


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.fixture
def priced(world: World) -> dict[str, Any]:
    from inventory import masters

    def items(u: Any) -> Any:
        masters.create_uom(u, world.company, {"code": "UND", "name": "Unidad", "precision": 0})
        return masters.create_item(
            u,
            world.company,
            {"code": "SKU-1", "name": "Producto (SINTÉTICO)", "base_uom": "UND", "standard_cost": "600"},
        )

    item = world.run("admin", items)
    world.base_price(item.id, "1000")
    world.formula("precio_costo", EXPRESSION, {"cost": "money"}, [{"inputs": {"cost": "600"}, "expected": "827"}])
    world.bind_formula("COSTO-135", "precio_costo")
    mine, other = world.customer("C-MINE"), world.customer("C-OTHER")
    data = {
        "party": str(mine.id),
        "document_date": TODAY.isoformat(),
        "lines": [{"item_id": str(item.id), "quantity": "3"}],
    }
    quote = world.run("admin", lambda u: documents.create_quote(u, world.company, data))
    world.run("admin", lambda u: documents.quote_action(u, quote, "send", "", {}))
    data_other = {**data, "party": str(other.id)}
    other_quote = world.run("admin", lambda u: documents.create_quote(u, world.company, data_other))
    world.run("admin", lambda u: documents.quote_action(u, other_quote, "send", "", {}))
    return {"item": item, "mine": mine, "other": other, "quote": quote, "other_quote": other_quote}


def _assert_clean(payload: Any) -> None:
    text = json.dumps(payload)
    assert not (_keys(payload) & portal.PORTAL_FORBIDDEN), _keys(payload) & portal.PORTAL_FORBIDDEN
    assert EXPRESSION not in text and "1.35" not in text and "600" not in text and "precio_costo" not in text


def test_T_SAL_PRICING_04_portal_quote_never_exposes_cost_or_formula(world: World, priced: dict[str, Any]) -> None:
    ctx = world.ctx("portal")
    mine = priced["mine"]
    rows = world.run("portal", lambda u: portal.portal_quotes(ctx, world.company, mine.id))
    assert [r["id"] for r in rows] == [str(priced["quote"].id)]
    one = world.run("portal", lambda u: portal.portal_quote(ctx, world.company, mine.id, priced["quote"].id))
    assert set(one) == set(portal.PORTAL_FIELDS)
    assert one["total"]["amount"].startswith("2481")  # 3 × 827 (formula price), the customer's own total
    _assert_clean(rows)
    _assert_clean(one)
    # another customer's quote answers 404 (no existence oracle)
    with pytest.raises(NotFound):
        world.run("portal", lambda u: portal.portal_quote(ctx, world.company, mine.id, priced["other_quote"].id))
    # the portal price preview returns the final price only
    prices = world.run(
        "portal",
        lambda u: portal.portal_price(
            u, world.company, mine.id, [{"item_id": str(priced["item"].id), "quantity": "1"}]
        ),
    )
    assert set(prices[0]) == {"item_id", "quantity", "unit_price"}
    assert set(prices[0]["unit_price"]) == {"unit_price", "currency"}
    assert Decimal(prices[0]["unit_price"]["unit_price"]) == 827
    _assert_clean(prices)
    # deny by default: no portal grant → refused
    with pytest.raises(PermissionDenied):
        world.run("outsider", lambda u: portal.portal_quotes(world.ctx("outsider"), world.company, mine.id))


def test_T_SAL_PRICING_04_internal_api_masks_cost_without_the_cost_role(
    api: ApiClient, world: World, priced: dict[str, Any]
) -> None:
    body = {
        "parameters": {"party": str(priced["mine"].id), "items": [{"item_id": str(priced["item"].id), "quantity": "1"}]}
    }
    seller = api.as_("seller").post("pricing:quote", body)
    assert seller.status_code == 200, seller.content
    explain = seller.json()["data"]["items"][0]["explain"]
    assert "cost" not in json.dumps(explain["detail"].get("inputs", {})) and "trace" not in explain["detail"]
    assert EXPRESSION not in seller.content.decode()
    # quote GET as seller: no line cost/margin, snapshot without the cost input
    got = api.as_("seller").get(f"quotes/{priced['quote'].id}")
    assert got.status_code == 200, got.content
    line = got.json()["lines"][0]
    assert "cost_value" not in line and "margin" not in line
    assert "cost" not in (line["price_snapshot"]["detail"].get("inputs") or {})
    assert EXPRESSION not in got.content.decode()
    # the cost role (internal, confidential grant) sees the cost input and the explain trace
    admin = api.as_("admin").post("pricing:quote", body)
    detail = admin.json()["data"]["items"][0]["explain"]["detail"]
    assert Decimal(detail["inputs"]["cost"]) == 600 and detail["trace"]
