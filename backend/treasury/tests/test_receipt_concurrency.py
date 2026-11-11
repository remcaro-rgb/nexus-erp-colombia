"""T-TRE-RC-03 — 50 concurrent RC posts through the API (one DB connection per thread, real row locks).

Gap-free RC numbers, no double application of the same open item (row lock on the AR open item: the balance
never goes below zero and the losers get ``treasury_overapplication``), and an idempotent retry of ``:post``
returns the same voucher without a second number or journal entry.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections

from treasury.models import TreasuryVoucher
from treasury.testing.support import TODAY, ApiClient, World, money, treasury_world

D = Decimal
N = 50


@pytest.fixture
def tx_world(transactional_db: Any, settings: Any) -> Iterator[World]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "treasury.testing.support.resolve_context"
    with treasury_world() as w:
        w.configure(rc_prefix="RC-")
        yield w


def run_threads(n: int, target: Callable[[int], Any]) -> tuple[list[Any], list[BaseException]]:
    results: list[Any] = [None] * n
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def wrapper(i: int) -> None:
        try:
            barrier.wait(timeout=60)
            results[i] = target(i)
        except BaseException as exc:  # collected and asserted by the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    return results, errors


def draft(api: ApiClient, world: World, party: Any, item: Any, amount: str) -> str:
    res = api.post(
        "receipts",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": str(world.env["fund"].id),
            "payment_method": "TRANSFER",
            "party": {"id": str(party.id), "kind": "customer"},
            "lines": [{"line_no": 1, "open_item_id": str(item.id), "applied_amount": money(amount)}],
        },
    )
    assert res.status_code == 201, res.content
    return str(res.json()["id"])


def ar_balance(world: World, item: Any) -> Decimal:
    from sales.models import ArOpenItem

    with world.scope():
        return Decimal(ArOpenItem.objects.get(id=item.id).balance)


def numbers(world: World) -> list[str]:
    with world.scope():
        rows = TreasuryVoucher.objects.filter(company_id=world.company, voucher_type="RC").exclude(number="")
        return sorted(rows.values_list("number", flat=True))


def test_T_TRE_RC_03_fifty_concurrent_posts_gap_free_numbers_no_double_application(tx_world: World) -> None:
    world = tx_world
    api = ApiClient(world)
    customer = world.customer("C-CONC")
    shared = world.ar_item(customer, "SETT-9000", "5000000")  # 50 × 100,000 = exactly the balance
    ids_ = [draft(api, world, customer, shared, "100000") for _ in range(N)]
    keys = [f"rc03-post-{i}" for i in range(N)]

    def post(i: int) -> tuple[int, dict[str, Any], str]:
        client = ApiClient(world)
        res = client.post(f"receipts/{ids_[i]}:post", key=keys[i])
        return res.status_code, res.json(), res.get("Idempotent-Replayed", "")

    results, errors = run_threads(N, post)
    assert errors == [], errors
    assert all(code == 200 for code, _, _ in results), [r for r in results if r[0] != 200][:3]
    got = sorted(body["number"] for _, body, _ in results)
    assert got == [f"RC-{n:06d}" for n in range(1, N + 1)]  # gap-free, no duplicates
    assert numbers(world) == got
    assert ar_balance(world, shared) == D("0.0000")  # every application counted exactly once

    # Idempotent retry: the same key returns the same voucher, no new number, no new journal entry.
    entries = world.entry_count()
    status, first, _ = results[7]
    retry = ApiClient(world).post(f"receipts/{ids_[7]}:post", key=keys[7])
    assert retry.status_code == status and retry.json()["number"] == first["number"]
    assert retry.json()["id"] == first["id"] and retry["Idempotent-Replayed"] == "true"
    assert world.entry_count() == entries and numbers(world) == got


def test_T_TRE_RC_03_competing_receipts_never_overapply_one_open_item(tx_world: World) -> None:
    world = tx_world
    api = ApiClient(world)
    customer = world.customer("C-RACE")
    scarce = world.ar_item(customer, "SETT-9100", "300000")  # only 3 of the 10 receipts fit
    ids_ = [draft(api, world, customer, scarce, "100000") for _ in range(10)]

    def post(i: int) -> tuple[int, dict[str, Any]]:
        res = ApiClient(world).post(f"receipts/{ids_[i]}:post")
        return res.status_code, res.json()

    results, errors = run_threads(10, post)
    assert errors == [], errors
    ok = [body for code, body in results if code == 200]
    refused = [body for code, body in results if code != 200]
    assert len(ok) == 3 and len(refused) == 7
    assert {b["code"] for b in refused} == {"treasury_overapplication"}
    assert ar_balance(world, scarce) == D("0.0000")
    assert sorted(b["number"] for b in ok) == ["RC-000001", "RC-000002", "RC-000003"]  # losers returned their number
    assert numbers(world) == ["RC-000001", "RC-000002", "RC-000003"]
