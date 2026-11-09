"""P22a harness self-tests: the OpenAPI index every generated suite starts from."""

from __future__ import annotations

from qa_harness import openapi
from qa_harness.openapi import Channel
from qa_harness.paths import openapi_bundle


def test_p22a_index_covers_every_contract_operation():
    doc = openapi_bundle()
    expected = sum(1 for item in doc["paths"].values() for m in openapi.METHODS if m in item)
    ops = openapi.operations()
    assert len(ops) == expected
    assert len({op.key for op in ops}) == expected
    assert all(op.permission.count(".") == 2 for op in ops), "every operation carries x-permission"


def test_p22a_channels_cover_invariant_12_read_channels():
    channels = {op.channel for op in openapi.operations()}
    for ch in (Channel.LIST, Channel.DETAIL, Channel.EXPORT, Channel.REPORT, Channel.AI, Channel.PORTAL, Channel.WRITE):
        assert ch in channels, ch
    assert all(op.method != "get" for op in openapi.select(channels=[Channel.WRITE]))


def test_p22a_money_and_stock_targets_are_posting_actions_and_labelled():
    money = openapi.select(money=True)
    stock = openapi.select(stock=True)
    assert len(money) >= 30 and len(stock) >= 10
    for op in money + stock:
        assert op.method == "post", op.key
        assert op.effect_source in ("contract", "derived-shared07")
    keys = {op.key for op in money}
    for must in (
        "POST /api/v1/sales/invoices/{id}:post",
        "POST /api/v1/purchasing/vendor-invoices/{id}:post",
        "POST /api/v1/payroll/runs/{id}:post",
        "POST /api/v1/assets/depreciation-runs/{id}:post",
        "POST /api/v1/pos/tickets:checkout",
        "POST /api/v1/treasury/receipts/{id}:post",
    ):
        assert must in keys, must
    skeys = {op.key for op in stock}
    assert "POST /api/v1/sales/deliveries/{id}:validate" in skeys
    assert "POST /api/v1/purchasing/receipts/{id}:validate" in skeys
    # Non-posting documents never touch the GL (C1-3): simulations, quotes and orders are not money targets.
    assert not any(op.action in ("simulate", "preview") for op in money)
    assert not any(op.path.startswith(("/api/v1/sales/quotes", "/api/v1/sales/orders")) for op in money)


def test_p22a_field_classifications_follow_the_contract():
    ops = openapi.by_key()
    party = ops["GET /api/v1/sales/parties/{id}"]
    classes = party.field_classes()
    assert classes["id"] == "public"
    assert classes["email"] == "restricted-pii"
    assert classes["legal_name"] == "confidential"
    listing = ops["GET /api/v1/sales/parties"]
    assert listing.response_is_page and listing.field_classes() == classes
    emp = ops["GET /api/v1/hr/employees/{id}"]
    assert "health_entity" in emp.fields_above("restricted-pii")
    assert "full_name" in emp.fields_above("confidential")
    assert not emp.fields_above("secret")


def test_p22a_secrets_never_appear_in_read_models():
    for op in openapi.operations():
        if op.method == "get":
            assert not [n for n, c in op.response_fields if c == "secret"], op.key


def test_p22a_webhooks_indexed_with_payload_fields():
    hooks = openapi.webhooks()
    assert len(hooks) == len(openapi_bundle()["webhooks"])
    assert all(h.event_type for h in hooks)


def test_p22a_path_regex_matches_one_segment_per_parameter():
    rx = openapi.path_regex("/api/v1/sales/parties/{id}:archive")
    assert rx.match("/api/v1/sales/parties/0190a1b2-0000-7000-8000-000000000001:archive")
    assert not rx.match("/api/v1/sales/parties/a/b:archive")
    assert not rx.match("/api/v1/sales/parties/x")
