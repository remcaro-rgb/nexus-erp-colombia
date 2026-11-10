"""T-P22-FIN-01 / T-P22-STK-01 - all-or-nothing under failure injection (prompt P22 §2).

Introspection: every contract operation with a money effect (FIN-01) or a stock effect (STK-01) is
enumerated from the frozen OpenAPI (``x-money-effect`` / ``x-stock-effect`` tags, or the shared/07-derived
list until P02 adds them - CCR P22a-1). For each one:

* **implemented** (its route resolves) → it must have a sweep driver here, and the driver runs the real
  operation with a failure injected before every SQL statement: either everything commits (document +
  entry + sub-ledger + outbox + audit) or nothing does;
* **not implemented yet** → reported ``NOT_IMPLEMENTED`` in the evidence matrix (never PASSED); its
  posting *path* is swept through the reference flow of its family so the composition rule (C1-1/C1-2) is
  proven on the real ledger, sequences, outbox and audit today.

A module that lands a money/stock-effect route without a driver fails
``test_T_P22_FIN_01_every_implemented_operation_has_a_driver`` (P22 must then add the driver).
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from qa_harness import defects, openapi, refdocs, refstock, steps
from qa_harness.andina import item, party
from qa_harness.failure_injection import AtomicityProbe, fail_after, injected_domain_failure, sweep
from qa_harness.paths import REPORTS
from qa_harness.refdocs import Entry, StockOp
from qa_harness.routes import implemented_routes, match_contract

D = Decimal
DAY = dt.date(2026, 3, 9)
LEDGER_TABLES = ["ledger_journal_entry", "ledger_journal_line", "ledger_balance"]
PLATFORM_TABLES = ["kernel_outbox", "kernel_sequence", "kernel_idempotency", "workflow_audit_event"]
DOC_TABLES = ["qa_ref_document", *refstock.TABLES]
ALL_TABLES = LEDGER_TABLES + PLATFORM_TABLES + DOC_TABLES
pytestmark = pytest.mark.django_db

#: operation_id -> driver name, for operations whose route is implemented today.
API_DRIVERS = {"ledger_entries_by_reverse": "api_ledger_reverse"}

#: reference-flow family that sweeps the posting path of each not-yet-implemented operation.
FAMILY = {
    "sales_": "sales_invoice_deliver", "pos_": "sales_invoice_deliver", "purchasing_receipts": "goods_receipt",
    "purchasing_landed": "goods_receipt", "purchasing_vendor_credit": "goods_receipt", "purchasing_": "gl_only",
    "inventory_": "goods_receipt", "treasury_": "gl_only", "payroll_": "gl_only", "assets_": "gl_only",
    "ledger_": "gl_only", "onboarding_": "gl_only",
}  # fmt: skip


def family_of(op_id: str) -> str:
    return next(fam for prefix, fam in FAMILY.items() if op_id.startswith(prefix))


def effect_ops(kind: str) -> list[openapi.Operation]:
    attr = "money_effect" if kind == "money" else "stock_effect"
    return sorted((op for op in openapi.operations() if getattr(op, attr)), key=lambda o: o.operation_id)


def implemented_ids() -> set[str]:
    return {op.operation_id for ops in match_contract(implemented_routes()).values() for op in ops}


def test_T_P22_FIN_01_introspection_finds_every_tagged_operation():
    money, stock = effect_ops("money"), effect_ops("stock")
    assert len(money) >= 40 and len(stock) >= 15
    assert {op.method for op in money + stock} == {"post"}
    for op in money + stock:
        assert family_of(op.operation_id)
        assert "idempotency_conflict" in op.error_codes or op.idempotent, op.operation_id  # C2-6


@pytest.mark.req("UR-14")
def test_T_P22_FIN_01_every_implemented_operation_has_a_driver():
    live = implemented_ids()
    missing = [op.operation_id for op in effect_ops("money") + effect_ops("stock")
               if op.operation_id in live and op.operation_id not in API_DRIVERS]  # fmt: skip
    assert not missing, f"implemented money/stock-effect operations without a P22 sweep driver: {missing}"


# --------------------------------------------------------------------------- reference-flow sweeps


def _probe(world):
    return AtomicityProbe(ALL_TABLES, ctx=world.ctx)


def _commands(world):
    a, wh = world.a, world.warehouse["BOG-01"]
    it = item("SWEEP-1")
    steps.define_item(world, a, it, wh)
    steps.goods_receipt(world, a, it, wh, D(10), D(1000), DAY)
    stock = world.services["stock"]
    seq = iter(range(1, 10**6))

    def sales_invoice_deliver():
        steps.sales_invoice(world, a, party("Cliente barrido"), it, wh, D(2), D(1500), DAY,
                            idempotency_key=f"sweep-fv-{next(seq):07d}")  # fmt: skip

    def goods_receipt():
        steps.goods_receipt(world, a, it, wh, D(1), D(1000), DAY)

    def gl_only():
        world.run(lambda u: refdocs.post_document(
            u, doc_type="treasury.payment", company=a, day=DAY, total=D(500),
            entries=[Entry("payment", [("AP", "DEBIT", D(500)), ("BANK", "CREDIT", D(500))])]))  # fmt: skip

    def intercompany():
        world.run(lambda u: refdocs.post_document(
            u, doc_type="intercompany.sale", company=a, day=DAY, total=D(1190),
            stock=[StockOp("issue", [steps.stock_line("1", it, wh, D(1))])], stock_service=stock,
            entries=[Entry("issue", [("IC_RECEIVABLE", "DEBIT", D(1190)), ("REVENUE_IC", "CREDIT", D(1000)),
                                     ("VAT_PAYABLE", "CREDIT", D(190))]),
                     Entry("mirror", [("EXPENSE", "DEBIT", D(1190)), ("IC_PAYABLE", "CREDIT", D(1190))],
                           company=world.b)]))  # fmt: skip

    return {"sales_invoice_deliver": sales_invoice_deliver, "goods_receipt": goods_receipt, "gl_only": gl_only,
            "intercompany": intercompany}  # fmt: skip


EXPECT = {
    "sales_invoice_deliver": ["qa_ref_document", "qa_ref_move", "ledger_journal_entry", "ledger_journal_line",
                              "kernel_outbox", "workflow_audit_event", "kernel_idempotency"],
    "goods_receipt": ["qa_ref_document", "qa_ref_move", "qa_ref_layer", "ledger_journal_entry", "kernel_outbox"],
    "gl_only": ["qa_ref_document", "ledger_journal_entry", "ledger_journal_line", "kernel_outbox"],
    "intercompany": ["qa_ref_document", "qa_ref_move", "ledger_journal_entry", "kernel_outbox"],
}  # fmt: skip


@pytest.mark.parametrize("family", sorted(EXPECT))
def test_T_P22_FIN_01_reference_posting_paths_are_all_or_nothing(andina, family):
    command = _commands(andina)[family]
    result = sweep(command, _probe(andina), expect_changed=EXPECT[family], isolate=True)
    assert result.ok, result.violations
    assert result.statements >= 10 and result.points == list(range(1, result.statements + 1))


@pytest.mark.parametrize("family", ["sales_invoice_deliver", "goods_receipt", "intercompany"])
def test_T_P22_STK_01_stock_paths_are_all_or_nothing(andina, family):
    command = _commands(andina)[family]
    result = sweep(command, _probe(andina), expect_changed=["qa_ref_move", "qa_ref_valuation"], isolate=True)
    assert result.ok, result.violations


def test_T_P22_FIN_01_sweep_catches_a_non_atomic_document(andina):
    """Mutation: the journal is posted in a second unit of work → partial writes must be reported."""
    a = andina.a

    def broken():
        andina.run(lambda u: refdocs.post_document(u, doc_type="treasury.payment", company=a, day=DAY,
                                                   total=D(1)))  # fmt: skip
        andina.run(lambda u: refdocs.post_document(
            u, doc_type="treasury.payment", company=a, day=DAY, total=D(1),
            entries=[Entry("payment", [("AP", "DEBIT", D(1)), ("BANK", "CREDIT", D(1))])]))  # fmt: skip

    result = sweep(broken, _probe(andina), expect_changed=["ledger_journal_entry"], isolate=True)
    assert not result.ok and any("partial writes" in v for v in result.violations)


# --------------------------------------------------------------------------- implemented API: ledger reverse


@pytest.fixture
def ledger_api(andina, settings):
    from rest_framework.test import APIClient

    from ledger.testing import context as test_context

    settings.ERP_TENANT_CONTEXT_RESOLVER = "ledger.testing.context.resolve_context"
    andina.grant("ledger.entry.read", "ledger.entry.void", "ledger.entry.post")

    class _User:
        is_authenticated = True
        pk = andina.ctx.principal_id

    client = APIClient()
    client.force_authenticate(user=_User(), token=andina.ctx)
    client.credentials(HTTP_X_COMPANY_ID=str(andina.a), HTTP_X_TEST_CONTEXT=test_context.register(andina.ctx))
    return client


def _payment(andina, amount=D(700)):
    return andina.run(lambda u: refdocs.post_document(
        u, doc_type="treasury.payment", company=andina.a, day=DAY, total=amount,
        entries=[Entry("payment", [("AP", "DEBIT", amount), ("BANK", "CREDIT", amount)])]))  # fmt: skip


@pytest.mark.req("UR-14")
def test_T_P22_FIN_01_api_ledger_reverse_is_all_or_nothing(andina, ledger_api):
    """The real POST /api/v1/ledger/entries/{id}:reverse with a (handled) failure before every statement."""
    entry_id = _payment(andina).entries[0].entry_id
    statuses: list[int] = []

    def command():
        r = ledger_api.post(f"/api/v1/ledger/entries/{entry_id}:reverse", {"reason": "barrido de fallas P22"},
                            format="json", HTTP_IDEMPOTENCY_KEY="p22b-reverse-0001")  # fmt: skip
        statuses.append(r.status_code)

    result = sweep(command, _probe(andina), expect_changed=["ledger_journal_entry", "ledger_journal_line",
                                                            "kernel_outbox", "kernel_idempotency"],
                   isolate=True, error=injected_domain_failure())  # fmt: skip
    assert result.ok, result.violations
    assert statuses[0] == 200 and result.statements >= 10
    assert set(statuses[1:]) == {500}  # every injected failure surfaced as a problem response, nothing posted


@defects.xfail("DEF-P22-009")
@pytest.mark.req("UR-21")
def test_p22b_secured_view_releases_transaction_and_context_on_unhandled_error(andina, ledger_api):
    """An unexpected exception inside a ModuleSecuredView handler must not leave the request's tenant scope
    (transaction.atomic + bound tenant context) open: DRF skips finalize_response on unhandled errors.

    Probed in a separate thread with its own database connection and contextvars, so a leak can never
    reach the test transaction or another test."""
    import gc
    import threading

    from kernel import tenancy
    from qa_harness.failure_injection import InjectedFailure

    andina.grant("ledger.period.read")
    leaks: list[tuple[int, bool, bool]] = []

    def probe():
        import logging

        from django.db import connection

        # Django logs the uncaught error with exc_info; a captured log record would keep the traceback - and with
        # it the leaked scope generator - alive until the main thread collects it. No records during the probe.
        logging.disable(logging.CRITICAL)
        try:
            for k in range(1, 6):
                try:
                    with fail_after(k):
                        ledger_api.get("/api/v1/ledger/periods")
                except InjectedFailure:
                    pass
                leaks.append((k, connection.in_atomic_block, tenancy.current() is not None))
                if leaks[-1][1] or leaks[-1][2]:
                    break  # leaked: this thread's connection/context are unusable from here on
        finally:
            # The leaked scope is a suspended generator context manager; when the garbage collector
            # finalises it, its Atomic.__exit__ runs on the *collecting* thread's connection. Collect here,
            # on this thread, so it can never pop a savepoint of another thread's transaction.
            gc.collect()
            connection.close()
            logging.disable(logging.NOTSET)

    t = threading.Thread(target=probe)
    t.start()
    t.join(timeout=60)
    assert leaks and [k for k, in_tx, ctx in leaks if in_tx or ctx] == []


# --------------------------------------------------------------------------- evidence matrix


def test_T_P22_FIN_01_effect_matrix_evidence(tmp_path):
    live = implemented_ids()
    rows = []
    for kind in ("money", "stock"):
        for op in effect_ops(kind):
            implemented = op.operation_id in live
            rows.append({
                "test": "T-P22-FIN-01" if kind == "money" else "T-P22-STK-01",
                "operation_id": op.operation_id, "method": op.method.upper(), "path": op.path, "owner": op.owner,
                "effect_source": op.effect_source,
                "status": ("SWEPT-API" if op.operation_id in API_DRIVERS else "NO-DRIVER") if implemented
                else "NOT_IMPLEMENTED",
                "reference_family_swept": family_of(op.operation_id),
            })  # fmt: skip
    out = REPORTS / "p22b"
    out.mkdir(parents=True, exist_ok=True)
    path = Path(out / "effect-matrix.csv")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert not [r for r in rows if r["status"] == "NO-DRIVER"]
    assert any(r["status"] == "SWEPT-API" for r in rows)
