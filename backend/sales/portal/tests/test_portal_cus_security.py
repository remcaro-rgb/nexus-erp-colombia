"""F-PORTAL-CUS: T-PORTAL-CUS-04 — no internal prices of other customers, cost, margin or price formula in any
portal response (every GET route of ``/api/portal/v1/customer/*`` is walked, JSON and files alike)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from sales.interfaces import PORTAL_FORBIDDEN
from sales.models import ArOpenItem
from sales.portal.tests.kit import C, P10bWorld, S, ok

pytestmark = pytest.mark.django_db

UNIT_COST = "123.4567"  # synthetic average cost of the item (confidential)
OTHER_PRICE = "777.77"  # internal price list of customer B's group
FORMULA_CODE = "precio_vip_p10b"
FORMULA_EXPR = "round(base_price * 0.8765, 2)"  # customer A's formula price: 876.50

FORBIDDEN_KEYS = PORTAL_FORBIDDEN | {
    "unit_cost",
    "cost_price",
    "average_cost",
    "margin_pct",
    "gross_margin",
    "price_formula",
    "formula_version",
    "evaluation_id",
    "override",
    "override_status",
    "discount_limit",
    "price_list",
    "price_list_id",
    "customer_group",
    "journal_entry_id",
    "internal_notes",
}


def _customer_routes() -> list[str]:
    out: list[str] = []

    def walk(patterns: Any, prefix: str = "") -> None:
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                route = prefix + str(p.pattern)
                if route.startswith("api/portal/v1/customer/"):
                    out.append(route)

    walk(get_resolver().url_patterns)
    return sorted(set(out))


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for v in value.values():
            found |= _keys(v)
        return found
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def _setup(w: P10bWorld, fakes: dict[str, Any]) -> dict[str, Any]:
    from sales.pricing.service import create_price_formula, create_price_list
    from workflow.formula.lifecycle import FormulaService

    item = w.item("A", "1000", cost=UNIT_COST)
    cid = w.company()
    # customer B's group has an internal price list; customer A's group is priced by a P07 formula
    w.run(
        lambda u: create_price_list(
            u,
            cid,
            {
                "code": "GRUPOB",
                "name": "Lista grupo B (SINTÉTICA)",
                "currency": "COP",
                "customer_group": "GRUPOB",
                "prices": [{"item_id": str(item.id), "min_qty": "0", "unit_price": OTHER_PRICE}],
            },
        ),
        "vendedor",
    )
    svc = FormulaService()
    row = svc.create(
        w.ctx("vendedor"),
        cid,
        {
            "code": FORMULA_CODE,
            "domain": "pricing",
            "expression": FORMULA_EXPR,
            "inputs": {"fields": {"base_price": "money"}},
            "result_type": "money",
            "tests": [{"inputs": {"base_price": "1000"}, "expected": "876.5"}],
            "effective_from": "2026-01-01",
        },
    )
    svc.submit(w.ctx("vendedor"), row.id)
    svc.approve(w.ctx("aprobador"), row.id, "aprobada (SINTÉTICO)")
    w.run(
        lambda u: create_price_formula(
            u,
            cid,
            {
                "code": "VIP-P10B",
                "name": "Fórmula VIP",
                "formula_code": FORMULA_CODE,
                "scope": {"customer_group": "VIP"},
                "priority": 1,
                "valid_from": "2026-01-01",
            },
        ),
        "vendedor",
    )
    a = w.customer("CA", "900100001", customer_group="VIP", credit_limit="99999999")
    b = w.customer("CB", "900100002", customer_group="GRUPOB")
    inv_a = w.sell(a, [("A", "2")])
    inv_b = w.sell(b, [("A", "3")])
    assert inv_a["lines"][0]["unit_price"] in (
        "876.500000",
        "876.5",
        {"amount": "876.500000", "currency": "COP"},
    ) or "876.5" in json.dumps(inv_a["lines"][0])
    assert OTHER_PRICE in json.dumps(inv_b["lines"][0])
    note_a = w.credit_note(inv_a, "1")
    w.credit_note(inv_b, "1")
    with w.scope():
        item_a = ArOpenItem.objects.get(document_id=inv_a["id"])
        item_b = ArOpenItem.objects.get(document_id=inv_b["id"])
    rc_a = w.receipt(a, str(item_a.id), "100.00")
    w.receipt(b, str(item_b.id), "100.00")
    api_a = w.onboard(a, "cli-a", fakes["notifications"])
    # portal artefacts of A: a quote request quoted + sent, a payment intent, a general request
    req = ok(
        api_a.post(
            f"{C}/requests",
            {"request_type": "QUOTE", "message": "", "items": [{"item_id": str(item.id), "quantity": "1"}]},
        ),
        201,
    )
    quoted = ok(w.api("vendedor").post(f"{S}/portal-requests/{req['id']}:quote", {"parameters": {"send": True}}))
    pi = ok(
        api_a.post(f"{C}/payment-intents", {"documents": [inv_a["id"]], "amount": {"amount": "50", "currency": "COP"}}),
        201,
    )
    return {
        "a": a,
        "b": b,
        "inv_a": inv_a,
        "inv_b": inv_b,
        "note_a": note_a,
        "rc_a": rc_a,
        "quote_a": quoted["resource"]["id"],
        "order_a": inv_a["order_id"],
        "req_a": req["id"],
        "pi_a": pi["id"],
        "api_a": api_a,
    }


def test_T_PORTAL_CUS_04_no_other_customer_prices_cost_margin_or_formula_in_any_portal_response(
    w: P10bWorld, fakes: dict[str, Any]
) -> None:
    env = _setup(w, fakes)
    api = env["api_a"]
    ids_for = {
        "quotes": env["quote_a"],
        "orders": env["order_a"],
        "invoices": env["inv_a"]["id"],
        "credit-notes": env["note_a"]["id"],
        "receipts": env["rc_a"]["id"],
        "payment-intents": env["pi_a"],
        "requests": env["req_a"],
    }
    secrets = {
        OTHER_PRICE,
        UNIT_COST,
        "246.91",  # cost of the 2 units sold to A (COGS)
        FORMULA_CODE,
        FORMULA_EXPR,
        "0.8765",
        "VIP-P10B",
        "GRUPOB",
        "99999999",  # A's credit limit (internal)
        str(env["b"].id),
        env["b"].legal_name,
        env["inv_b"]["id"],
        env["inv_b"]["number"],
    }
    bodies: dict[str, str] = {}
    for route in _customer_routes():
        if route.endswith((":accept", ":simulate")):
            continue  # writes (covered by T-PORTAL-CUS-02/03); their responses are checked below
        resource = route.removeprefix("api/portal/v1/customer/").split("/")[0]
        url = "/" + route.replace("<str:pk>", str(ids_for.get(resource, "")))
        variants: list[dict[str, str]] = [{}]
        if resource == "statement":
            variants = [{}, {"format": "csv"}, {"format": "pdf"}]
        for params in variants:
            r = api.get(url, **params)
            assert r.status_code == 200, (url, params, r.content)
            if r["Content-Type"].startswith("application/json"):
                data = r.json()
                leaked = _keys(data) & FORBIDDEN_KEYS
                assert not leaked, (url, leaked)
                text = json.dumps(data, ensure_ascii=False)
            else:
                text = r.content.decode("cp1252", errors="replace") + r.content.decode("utf-8", errors="replace")
                for word in ("cost", "margin", "formula", "price_source", "snapshot"):
                    assert word not in text.lower(), (url, word)
            for secret in secrets:
                assert secret not in text, (url, params, secret)
            bodies[url + json.dumps(params)] = text
    # every customer GET route was exercised (list, detail and file routes)
    assert len(bodies) >= 22, sorted(bodies)

    # write responses are allow-listed too
    accepted = ok(api.post(f"{C}/quotes/{env['quote_a']}:accept"))
    simulated = ok(api.post(f"{C}/payment-intents/{env['pi_a']}:simulate", {"parameters": {"outcome": "failure"}}))
    for data in (accepted, simulated):
        assert not (_keys(data) & FORBIDDEN_KEYS)
        text = json.dumps(data, ensure_ascii=False)
        assert all(s not in text for s in secrets)
    # the customer's own prices are not "internal prices of other customers": A sees its own invoice total
    own = ok(api.get(f"{C}/invoices/{env['inv_a']['id']}"))
    assert own["total"]["amount"] == env["inv_a"]["total"]["amount"]
