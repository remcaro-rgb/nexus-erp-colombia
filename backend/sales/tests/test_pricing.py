"""F-SAL-PRICING (D-024): precedence, quantity breaks, scopes, snapshots, repricing and override approvals."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from kernel.models import Sequence
from kernel.testing.tenancy import bound
from sales.errors import PricingFormulaMissingInput, PricingOverrideRequiresApproval, PricingPostedDocumentImmutable
from sales.models import Invoice, OverrideStatus, QuoteLine
from sales.pricing import engine
from sales.services import documents, posting
from sales.testing.support import TODAY, World

pytestmark = pytest.mark.django_db

ITEM = uuid.UUID("018f0000-0000-7000-8000-00000000a001")
ITEM_B = uuid.UUID("018f0000-0000-7000-8000-00000000a002")


def price(world: World, item: uuid.UUID, qty: str, party: Any = None, **kw: Any) -> engine.PriceResult:
    req = engine.PriceRequest(item_id=item, quantity=Decimal(qty), on=TODAY, party=party, **kw)
    return world.run("admin", lambda u: engine.resolve(u, world.company, req))


@pytest.fixture
def catalog(world: World) -> dict[str, Any]:
    world.base_price(ITEM, "1000")
    world.base_price(ITEM_B, "500")
    world.price_list(
        "MAY",
        [
            {"item_id": str(ITEM), "min_qty": "0", "unit_price": "900"},
            {"item_id": str(ITEM), "min_qty": "10", "unit_price": "850"},
        ],
        customer_group="MAYORISTA",
    )
    world.price_list("VIPL", [{"item_id": str(ITEM), "min_qty": "0", "unit_price": "950"}], customer_group="VIP")
    world.formula(
        "precio_vip",
        "round(base_price * 0.8, 0)",
        {"base_price": "money"},
        [{"inputs": {"base_price": "1000"}, "expected": "800"}],
    )
    world.bind_formula("VIP-80", "precio_vip", scope={"customer_group": "VIP"})
    return {
        "retail": world.customer("C-RET"),
        "wholesale": world.customer("C-MAY", group="MAYORISTA"),
        "vip": world.customer("C-VIP", group="VIP"),
    }


def test_T_SAL_PRICING_01_precedence_breaks_and_group_scopes(world: World, catalog: dict[str, Any]) -> None:
    retail, wholesale, vip = catalog["retail"], catalog["wholesale"], catalog["vip"]
    # base: no list or formula applies to the retail customer
    r = price(world, ITEM, "5", retail)
    assert (r.source, r.unit_price) == (engine.BASE, Decimal("1000"))
    # list beats base; quantity breaks pick the highest min_qty ≤ qty
    r = price(world, ITEM, "5", wholesale)
    assert (r.source, r.unit_price) == (engine.LIST, Decimal("900"))
    r = price(world, ITEM, "12", wholesale)
    assert (r.source, r.unit_price) == (engine.LIST, Decimal("850"))
    assert Decimal(r.snapshot["detail"]["min_qty"]) == 10
    # the list is scoped to its customer group: an item without a list line falls back to base
    r = price(world, ITEM_B, "12", wholesale)
    assert (r.source, r.unit_price) == (engine.BASE, Decimal("500"))
    # formula beats list (VIP has both a list at 950 and a formula at base × 0.8)
    r = price(world, ITEM, "1", vip)
    assert (r.source, r.unit_price) == (engine.FORMULA, Decimal("800"))
    assert r.snapshot["resolved_source"] == engine.FORMULA
    # the formula scope (customer_group VIP) does not reach the wholesale customer
    assert price(world, ITEM, "1", wholesale).source == engine.LIST
    # override beats everything
    r = price(world, ITEM, "1", vip, override_price=Decimal("790"), override_reason="negociación")
    assert (r.source, r.unit_price, r.resolved_price) == (engine.OVERRIDE, Decimal("790"), Decimal("800"))
    assert r.snapshot["precedence"] == ["OVERRIDE", "FORMULA", "LIST", "BASE"]


def test_T_SAL_PRICING_01_missing_formula_input_is_refused(world: World) -> None:
    world.base_price(ITEM, "1000")
    world.formula(
        "precio_lista",
        "round(list_price * 0.9, 0)",
        {"list_price": "money"},
        [{"inputs": {"list_price": "1000"}, "expected": "900"}],
    )
    world.bind_formula("ALL-LIST", "precio_lista")
    with pytest.raises(PricingFormulaMissingInput) as exc:
        price(world, ITEM, "1", world.customer("C-X"))
    assert exc.value.code == "pricing_formula_missing_input"


def _quote(world: World, party: Any, lines: list[dict[str, Any]]) -> Any:
    data = {"party": str(party.id), "document_date": TODAY.isoformat(), "lines": lines}
    return world.run("admin", lambda u: documents.create_quote(u, world.company, data))


def test_T_SAL_PRICING_02_snapshot_stored_and_draft_reprice_diff(world: World, catalog: dict[str, Any]) -> None:
    quote = _quote(world, catalog["wholesale"], [{"item_id": str(ITEM), "quantity": "12"}])
    with bound(world.ctx("admin")):
        line = QuoteLine.objects.get(document_id=quote.id)
    snap = line.price_snapshot
    assert line.price_source == engine.LIST and line.unit_price == Decimal("850")
    assert snap["source"] == engine.LIST and snap["hash"] and snap["detail"]["price_list_code"] == "MAY"
    assert snap["rounding"] == "ROUND_HALF_UP:2"  # COP minor unit of kernel.money
    # the list changes: a preview shows the diff and stores nothing
    from sales.models import PriceListLine

    with bound(world.ctx("admin")):
        PriceListLine.objects.filter(price_list__code="MAY", min_qty=Decimal(10)).update(unit_price=Decimal("820"))
    diff = world.run("admin", lambda u: documents.quote_action(u, quote, "reprice", "", {}))["diff"]
    assert (Decimal(diff[0]["old"]["unit_price"]), Decimal(diff[0]["new"]["unit_price"])) == (850, 820)
    assert diff[0]["changed"] is True and Decimal(diff[0]["delta_amount"]) == -360
    with bound(world.ctx("admin")):
        assert QuoteLine.objects.get(id=line.id).price_snapshot == snap
    # applying the reprice stores the new price and snapshot
    world.run("admin", lambda u: documents.quote_action(u, quote, "reprice", "", {"apply": True}))
    with bound(world.ctx("admin")):
        fresh = QuoteLine.objects.get(id=line.id)
    assert fresh.unit_price == Decimal("820") and fresh.price_snapshot["hash"] != snap["hash"]


def _fiscal_range(world: World) -> None:
    with bound(world.ctx("admin")):
        Sequence.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.company,
            sequence_code="FV",
            prefix="SETT",
            next_number=1,
            is_fiscal=True,
            range_from=1,
            range_to=5000,
            valid_from=dt.date(2026, 1, 1),
            valid_to=dt.date(2027, 12, 31),
            resolution_number="SINTETICA-0001",
        )


def test_T_SAL_PRICING_02_posted_invoice_is_never_repriced(world: World, catalog: dict[str, Any]) -> None:
    _fiscal_range(world)
    data = {
        "party": str(catalog["retail"].id),
        "document_date": TODAY.isoformat(),
        "lines": [{"item_id": str(ITEM), "quantity": "2", "tax_code": "IVA19"}],
    }
    inv = world.run("admin", lambda u: posting.create_invoice(u, world.company, data))
    # repricing the DRAFT invoice works
    world.base_price(ITEM, "1100")
    out = world.run("admin", lambda u: posting.invoice_action(u, inv, "reprice", "", {"apply": True}))
    assert Decimal(out["diff"][0]["new"]["unit_price"]) == 1100
    posted = world.run("admin", lambda u: posting.invoice_action(u, inv, "post", "", {}))
    assert posted.status == Invoice.Status.POSTED and posted.number.startswith("SETT")
    with bound(world.ctx("admin")):
        snapshot_before = posted.lines.get().price_snapshot
    world.base_price(ITEM, "1300")
    with pytest.raises(PricingPostedDocumentImmutable) as exc:
        world.run("admin", lambda u: posting.invoice_action(u, posted, "reprice", "", {"apply": True}))
    assert exc.value.code == "pricing_posted_document_immutable"
    with pytest.raises(PricingPostedDocumentImmutable):
        world.run("admin", lambda u: posting.update_invoice(u, posted, {"notes": "x"}))
    with bound(world.ctx("admin")):
        line = posted.lines.get()
        assert line.unit_price == Decimal("1100") and line.price_snapshot == snapshot_before


def test_T_SAL_PRICING_03_override_beyond_limit_routes_to_approval(world: World, catalog: dict[str, Any]) -> None:
    from sales.models import SalesSettings

    retail = catalog["retail"]
    with bound(world.ctx("admin")):  # without settings the limit is 0 % (every manual discount is approved)
        SalesSettings.objects.create(
            tenant_id=world.tenant_id, company_id=world.company, default_discount_limit_pct=Decimal(10)
        )
    # 5 % below the resolved base price: within the company's 10 % limit
    q1 = _quote(
        world, retail, [{"item_id": str(ITEM), "quantity": "1", "unit_price": "950", "override_reason": "cliente fiel"}]
    )
    with bound(world.ctx("admin")):
        assert q1.lines.get().override_status == OverrideStatus.WITHIN_LIMIT
    assert q1.approval_request_id is None
    world.run("admin", lambda u: documents.quote_action(u, q1, "send", "", {}))
    # 30 % below: beyond the limit → approval request, and the quote cannot be sent while pending
    q2 = _quote(
        world, retail, [{"item_id": str(ITEM), "quantity": "1", "unit_price": "700", "override_reason": "competencia"}]
    )
    with bound(world.ctx("admin")):
        line = q2.lines.get()
    assert line.override_status == OverrideStatus.PENDING_APPROVAL
    assert Decimal(line.price_snapshot["override"]["effective_discount_pct"]) == 30
    assert q2.approval_request_id is not None
    request = world.fakes.workflow.state(q2.approval_request_id)
    assert request.state == "PENDING" and request.document.type == "sales.quote"
    with pytest.raises(PricingOverrideRequiresApproval) as exc:
        world.run("admin", lambda u: documents.quote_action(u, q2, "send", "", {}))
    assert exc.value.code == "pricing_override_requires_approval"
    # a discount line beyond the limit is routed the same way
    q3 = _quote(world, retail, [{"item_id": str(ITEM), "quantity": "1", "discount_pct": "25"}])
    assert q3.approval_request_id is not None
    # the approver (not the requester: SoD) approves → the quote can be sent
    world.run("approver", lambda u: world.fakes.workflow.decide(u, q2.approval_request_id, True, "ok"))
    sent = world.run("admin", lambda u: documents.quote_action(u, q2, "send", "", {}))
    assert sent.status == "SENT"
    with bound(world.ctx("admin")):
        assert sent.lines.get().override_status == OverrideStatus.APPROVED


def test_T_SAL_PRICING_03_per_user_discount_limit(world: World, catalog: dict[str, Any]) -> None:
    from sales.models import DiscountLimit

    with bound(world.ctx("admin")):
        DiscountLimit.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.company,
            principal_id=world.ctx("admin").principal_id,
            max_discount_pct=Decimal(35),
        )
    q = _quote(
        world,
        catalog["retail"],
        [{"item_id": str(ITEM), "quantity": "1", "unit_price": "700", "override_reason": "gerencia"}],
    )
    with bound(world.ctx("admin")):
        assert q.lines.get().override_status == OverrideStatus.WITHIN_LIMIT


def test_T_SAL_PRICING_05_multicurrency_formula_uses_document_date_fx_and_rounds(world: World) -> None:
    from django.apps import apps

    rate_model = apps.get_model("ledger", "ExchangeRate")
    with bound(world.ctx("admin")):
        for day, rate in ((dt.date(2026, 11, 2), "4000.00"), (TODAY, "4100.00"), (dt.date(2026, 11, 10), "4300.00")):
            rate_model.objects.create(
                tenant_id=world.tenant_id,
                company_id=world.company,
                currency="USD",
                date=day,
                rate=Decimal(rate),
                source="SINTETICO",
            )
    world.base_price(ITEM, "100000")  # COP
    world.formula(
        "precio_margen",
        "base_price * 1.2345",
        {"base_price": "money"},
        [{"inputs": {"base_price": "100"}, "expected": "123.45"}],
    )
    world.bind_formula("ALL-MARGIN", "precio_margen")
    party = world.customer("C-USD")
    r = price(world, ITEM, "1", party, currency="USD")
    # 100000 × 1.2345 = 123450 COP ÷ 4100 (TRM of the document date, not the later 4300) = 30.1097… → 30.11 USD
    assert r.fx_rate == Decimal("4100")
    assert r.unit_price == Decimal("30.11") and r.source == engine.FORMULA
    assert r.snapshot["rate_date"] == TODAY.isoformat() and r.snapshot["rounding"] == "ROUND_HALF_UP:2"
    assert r.snapshot["detail"]["result_cop"].startswith("123450")
    # COP has no decimals in the price: rounding to the minor unit of the currency
    cop = price(world, ITEM, "1", party)
    assert cop.unit_price == Decimal("123450")
