"""Reconciliation job over shared/07 §9 invariants (prompt P22 §2 "nightly reconciliation").

``check(world)`` reads the real ledger / sequence / outbox tables of the demo tenant (and the stock and
document doubles while P09/P10/P11 are not merged) and returns the violations; an empty list means:

1. every journal entry balances in COP and per transaction currency;
2. every posted reference document with a money effect has at least one active entry, and every entry
   references a document;
3. GL INVENTORY = Σ valuation (per company);
4. GL AR = Σ open sales items (invoices − receipts), GL GRNI = receipts not yet invoiced (per company);
7. on-hand = Σ moves and never negative (negative stock disallowed in the demo tenant, D-032);
9. document numbers are gap-free per company and document type;
11. every idempotency record maps to exactly one document (no duplicate execution).

The checks are written against the database, not against the flows that wrote it.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)


def _rows(sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def check(world: Any) -> list[str]:
    violations: list[str] = []
    with world.scope():
        violations += _inv1()
        violations += _inv2()
        for company in (world.a, world.b):
            violations += _inv3(world, company)
            violations += _inv4(world, company)
            violations += _inv7(company)
            violations += _inv9(company)
        violations += _inv11()
    return violations


def _inv1() -> list[str]:
    out = []
    for entry, d, c in _rows(
        "SELECT entry_id, sum(debit), sum(credit) FROM ledger_journal_line GROUP BY entry_id HAVING sum(debit) <> "
        "sum(credit)",
        [],
    ):
        out.append(f"inv1: entry {entry} unbalanced in COP ({d} vs {c})")
    for entry, cur, diff in _rows(
        "SELECT entry_id, currency, sum(CASE WHEN debit > 0 THEN amount_currency ELSE -amount_currency END) "
        "FROM ledger_journal_line GROUP BY entry_id, currency",
        [],
    ):
        if diff != 0 and cur != "COP":
            # Mixed-currency entries balance in COP (FX difference lines are COP); single-currency must net 0.
            currencies = _rows("SELECT DISTINCT currency FROM ledger_journal_line WHERE entry_id = %s", [entry])
            if len(currencies) == 1:
                out.append(f"inv1: entry {entry} unbalanced in {cur} ({diff})")
    return out


def _inv2() -> list[str]:
    out = []
    for doc_id, doc_type, number in _rows(
        "SELECT d.id, d.doc_type, d.number FROM qa_ref_document d WHERE d.total <> 0 AND d.doc_type <> "
        "'pos.session_close' AND NOT EXISTS (SELECT 1 FROM ledger_journal_entry e WHERE e.source_id = d.id)",
        [],
    ):
        if doc_type.split(".")[0] in {"sales", "purchase", "treasury", "payroll", "assets", "intercompany",
                                       "onboarding"}:  # fmt: skip
            out.append(f"inv2: {doc_type} {number} ({doc_id}) has no journal entry")
    for (entry,) in _rows(
        "SELECT e.id FROM ledger_journal_entry e WHERE e.source_type NOT LIKE 'ledger.%%' AND NOT EXISTS "
        "(SELECT 1 FROM qa_ref_document d WHERE d.id = e.source_id)",
        [],
    ):
        out.append(f"inv2: entry {entry} references no document")
    return out


def _inv3(world: Any, company: Any) -> list[str]:
    stock = world.services["stock"]
    gl = world.balance("INVENTORY", company)
    valuation = stock.company_value(world.ctx, company)
    return [] if gl == valuation else [f"inv3: company {company} GL INVENTORY {gl} ≠ valuation {valuation}"]


def _inv4(world: Any, company: Any) -> list[str]:
    out = []
    sales = defaultdict(lambda: ZERO)
    for doc_type, total, payload in _rows(
        "SELECT doc_type, total, payload FROM qa_ref_document WHERE company_id = %s AND doc_type IN "
        "('sales.invoice', 'sales.receipt')",
        [company],
    ):
        if doc_type == "sales.invoice":
            sales["open"] += total
        else:
            import json

            data = payload if isinstance(payload, dict) else json.loads(payload)
            sales["open"] -= total + sum((Decimal(v) for v in data.get("withholdings", {}).values()), ZERO)
    gl_ar = world.balance("AR", company)
    if gl_ar != sales["open"]:
        out.append(f"inv4: company {company} GL AR {gl_ar} ≠ open sales items {sales['open']}")
    received = sum((r[0] for r in _rows(
        "SELECT total FROM qa_ref_document WHERE company_id = %s AND doc_type = 'purchase.receipt'", [company])),
        ZERO)  # fmt: skip
    invoiced = sum((r[0] for r in _rows(
        "SELECT coalesce(sum(l.debit), 0) FROM ledger_journal_line l JOIN ledger_journal_entry e ON e.id = l.entry_id "
        "JOIN ledger_account_determination ad ON ad.account_id = l.account_id AND ad.role_code = 'GRNI' "
        "JOIN ledger_book b ON b.id = e.book_id AND b.code = 'NIIF' WHERE l.company_id = %s", [company])), ZERO)  # fmt: skip
    gl_grni = world.balance("GRNI", company)
    if gl_grni != -(received - invoiced):
        out.append(f"inv4: company {company} GL GRNI {gl_grni} ≠ receipts not invoiced {received - invoiced}")
    return out


def _inv7(company: Any) -> list[str]:
    out = []
    for item, wh, qty, moves in _rows(
        "SELECT v.item_id, v.warehouse_id, v.qty, coalesce((SELECT sum(m.qty) FROM qa_ref_move m WHERE "
        "m.tenant_id = v.tenant_id AND m.company_id = v.company_id AND m.item_id = v.item_id AND "
        "m.warehouse_id = v.warehouse_id), 0) FROM qa_ref_valuation v WHERE v.company_id = %s",
        [company],
    ):
        if qty != moves:
            out.append(f"inv7: item {item}/{wh} on hand {qty} ≠ Σ moves {moves}")
        if qty < 0:
            out.append(f"inv7: item {item}/{wh} negative stock {qty}")
    return out


def _inv9(company: Any) -> list[str]:
    out = []
    by_type: dict[str, list[int]] = defaultdict(list)
    for doc_type, number in _rows(
        "SELECT doc_type, number FROM qa_ref_document WHERE company_id = %s", [company]
    ):
        by_type[f"{doc_type}:{number.rsplit('-', 1)[0]}"].append(int(number.rsplit("-", 1)[1]))
    shared: dict[str, list[int]] = defaultdict(list)
    for key, numbers in by_type.items():
        shared[key.split(":")[1]] += numbers  # several doc types may share one sequence (e.g. NC)
    for prefix, numbers in shared.items():
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            out.append(f"inv9: company {company} sequence {prefix} has gaps or duplicates: {sorted(numbers)[:20]}")
    return out


def _inv11() -> list[str]:
    out = []
    for key, n in _rows(
        "SELECT i.key, (SELECT count(*) FROM qa_ref_document d WHERE d.id::text = (i.response->>'id')) "
        "FROM kernel_idempotency i WHERE jsonb_exists(i.response, 'id')",
        [],
    ):
        if n != 1:
            out.append(f"inv11: idempotency key {key} maps to {n} documents")
    return out
