"""The frozen purchasing contract for P11b (``purchasing.interfaces``): D-011 vendor price, portal hooks."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from kernel.errors import NotFound, PermissionDenied
from kernel.uow import run_in_unit_of_work
from purchasing import interfaces
from purchasing.testing.support import ApiClient, World
from purchasing.tests.test_rfq_cycle import quote_body, run_to_evaluated


def test_valid_vendor_price_is_the_last_approved_award_within_90_days(api: ApiClient, world: World) -> None:
    rfq, _vendors, quotes = run_to_evaluated(api, world)
    rid = rfq["id"]
    item = uuid.UUID(rfq["lines"][0]["item_id"])
    buyer = world.ctx("buyer")
    assert run_in_unit_of_work(lambda u: interfaces.valid_vendor_price(u, world.company, item), buyer) is None
    recommended = api.get(f"rfqs/{rid}/comparison").json()["recommended_quote_id"]
    api.as_("manager").post(f"rfqs/{rid}:award", {"parameters": {"quote_id": recommended}})
    api.as_("approver").post(f"rfqs/{rid}:approve-award", {"reason": "ok"})
    price = run_in_unit_of_work(lambda u: interfaces.valid_vendor_price(u, world.company, item), buyer)
    assert price is not None and price.uom == "KG" and price.currency == "COP"
    assert price.unit_price == Decimal("2900.00000000") and str(quotes["PROV-B"]) == recommended
    later = price.awarded_on + dt.timedelta(days=91)
    assert run_in_unit_of_work(lambda u: interfaces.valid_vendor_price(u, world.company, item, later), buyer) is None


def test_portal_hooks_use_the_portal_party_scope(api: ApiClient, world: World) -> None:
    from purchasing.tests.test_rfq_cycle import create_rfq, suppliers

    vendors = suppliers(world)
    rid = create_rfq(api, vendors)["id"]
    api.as_("buyer").post(f"rfqs/{rid}:send")
    portal = world.user("portal_prov_a", ("portal.supplier_quote.create", "portal.supplier_purchase_order.update"))
    body = quote_body(vendors[1], "1", "1", lead=1)  # the body names another vendor: ignored
    quote, warnings = run_in_unit_of_work(
        lambda u: interfaces.portal_submit_quote(u, uuid.UUID(rid), vendors[0].id, body), portal
    )
    assert quote.supplier_id == vendors[0].id and quote.channel == "supplier_portal" and warnings == []
    stranger = world.supplier("PROV-Z", "No invitado (SINTÉTICO)")
    with pytest.raises(NotFound):
        run_in_unit_of_work(lambda u: interfaces.portal_submit_quote(u, uuid.UUID(rid), stranger.id, body), portal)
    with pytest.raises(PermissionDenied):
        run_in_unit_of_work(
            lambda u: interfaces.portal_submit_quote(u, uuid.UUID(rid), vendors[2].id, body), world.ctx("outsider")
        )
    with pytest.raises(NotFound):
        run_in_unit_of_work(lambda u: interfaces.portal_acknowledge_order(u, uuid.uuid4(), vendors[0].id), portal)
