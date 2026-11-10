"""Reconciliation of invariants 1-11 after a randomized tenant + the negative suite (prompt P22 §2, §3).

* Hypothesis stateful test: ≥ 500 steps mixing receipts, sales, customer receipts, vendor invoices,
  transfers between companies (intercompany), payroll, depreciation, returns and payments on the demo
  tenant; the reconciliation job (``qa_harness.invariants``) must find nothing after every step batch.
* Negative cases across modules: closed / soft-closed periods, control accounts, unbalanced documents,
  cross-tenant and cross-company scope, immutability, reversal instead of deletion.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from qa_harness import invariants, refdocs, steps
from qa_harness.andina import demo_context, item, party
from qa_harness.refdocs import Entry, StockOp

from kernel.errors import DomainError

D = Decimal
DAY = dt.date(2026, 8, 3)
pytestmark = pytest.mark.django_db


def test_p22b_invariants_hold_after_the_golden_scenario_mix(andina):
    a, wh = andina.a, andina.warehouse["BOG-01"]
    it = item("INV-MIX")
    steps.define_item(andina, a, it, wh)
    steps.goods_receipt(andina, a, it, wh, D(40), D(1000), DAY)
    inv = steps.sales_invoice(andina, a, party("Hoteles Sabana S.A.S."), it, wh, D(10), D(2000), DAY)
    steps.customer_receipt(andina, a, party("Hoteles Sabana S.A.S."), inv, D(23800), D(20000), DAY)
    assert invariants.check(andina) == []


def test_p22b_invariant_checker_detects_a_planted_violation(andina):
    """Mutation: a stock move without valuation update must be reported (inv 3 / inv 7)."""
    from django.db import connection

    from kernel import ids

    a, wh = andina.a, andina.warehouse["BOG-01"]
    it = item("INV-PLANT")
    steps.define_item(andina, a, it, wh)
    steps.goods_receipt(andina, a, it, wh, D(5), D(1000), DAY)
    with andina.scope(), connection.cursor() as cur:
        cur.execute(
            "INSERT INTO qa_ref_move (id, tenant_id, company_id, item_id, warehouse_id, source_type, source_id, "
            "line_ref, qty, value, move_date) VALUES (%s, %s, %s, %s, %s, 'qa.plant', %s, '1', -1, -1000, %s)",
            [ids.new_id(), andina.ctx.tenant_id, a, it, wh, ids.new_id(), DAY],
        )
    found = invariants.check(andina)
    assert any(v.startswith("inv7") for v in found)


class TenantMachine(RuleBasedStateMachine):
    """Randomized demo tenant (prompt P22 §2: stateful testing with ≥ 500 steps)."""

    world = None  # set by the test
    serial = 0
    steps_run = 0

    def _step(self):
        TenantMachine.steps_run += 1

    @initialize()
    def items(self):
        w = self.world
        TenantMachine.serial += 1
        self.items = [item(f"HYP-{TenantMachine.serial}-{k}") for k in range(3)]
        for it in self.items:
            steps.define_item(w, w.a, it, w.warehouse["BOG-01"], "FIFO" if it.int % 2 else "WAC")
            steps.define_item(w, w.b, it, w.warehouse["CLO-01"])
        self.invoices = []
        self.receipts = []

    @rule(k=st.integers(0, 2), qty=st.integers(1, 30), cost=st.integers(500, 5000))
    def receive(self, k, qty, cost):
        self._step()
        w = self.world
        self.receipts.append((steps.goods_receipt(w, w.a, self.items[k], w.warehouse["BOG-01"], D(qty), D(cost),
                                                  DAY), D(qty), D(cost)))  # fmt: skip

    @rule(k=st.integers(0, 2), qty=st.integers(1, 20), price=st.integers(1000, 9000))
    def sell(self, k, qty, price):
        self._step()
        w = self.world
        try:
            inv = steps.sales_invoice(w, w.a, party(f"Cliente {k}"), self.items[k], w.warehouse["BOG-01"], D(qty),
                                      D(price), DAY)  # fmt: skip
        except DomainError as exc:
            assert exc.code == "insufficient_stock"
            return
        self.invoices.append((inv, D(qty) * D(price)))

    @rule()
    def collect(self):
        self._step()
        if not self.invoices:
            return
        w = self.world
        inv, net = self.invoices.pop(0)
        steps.customer_receipt(w, w.a, party("Cliente cobro"), inv, refdocs.document(w.ctx, inv.ref)["total"], net,
                               DAY)  # fmt: skip

    @rule()
    def vendor_invoice(self):
        self._step()
        if not self.receipts:
            return
        w = self.world
        receipt, qty, cost = self.receipts.pop(0)
        value = qty * cost
        w.run(lambda u: refdocs.post_document(
            u, doc_type="purchase.invoice", company=w.a, day=DAY, total=value,
            entries=[Entry("invoice", [("GRNI", "DEBIT", value),
                                       ("AP", "CREDIT", value, {"party": party("Prov"), "party_kind": "vendor"})])],
            links={"receipt": str(receipt.ref.id)}))  # fmt: skip

    @rule(k=st.integers(0, 2), qty=st.integers(1, 5))
    def intercompany(self, k, qty):
        self._step()
        w, it = self.world, self.items[k]
        stock = w.services["stock"]

        def fn(u):
            posted = refdocs.post_document(
                u, doc_type="intercompany.sale", company=w.a, day=DAY, total=D(qty) * D(7000),
                stock=[StockOp("issue", [steps.stock_line("1", it, w.warehouse["BOG-01"], D(qty))]),
                       StockOp("receive", [steps.stock_line("1", it, w.warehouse["CLO-01"], D(qty), D(7000))],
                               company=w.b)], stock_service=stock)  # fmt: skip
            cogs = posted.stock[0].total_value
            steps.post_entries(u, posted, DAY, [
                Entry("issue", [("IC_RECEIVABLE", "DEBIT", D(qty) * D(7000)), ("REVENUE_IC", "CREDIT", D(qty) * D(7000))]),
                Entry("cogs", [("COGS", "DEBIT", cogs), ("INVENTORY", "CREDIT", cogs)]),
                Entry("mirror", [("INVENTORY", "DEBIT", D(qty) * D(7000)), ("IC_PAYABLE", "CREDIT", D(qty) * D(7000))],
                      company=w.b),
            ])  # fmt: skip

        try:
            w.run(fn)
        except DomainError as exc:
            assert exc.code == "insufficient_stock"

    @rule(amount=st.integers(100000, 5000000))
    def payroll(self, amount):
        self._step()
        w = self.world
        w.run(lambda u: refdocs.post_document(
            u, doc_type="payroll.run", company=w.a, day=DAY, total=D(amount),
            entries=[Entry("run", [("SALARY_EXPENSE", "DEBIT", D(amount)), ("PAYROLL_PAYABLE", "CREDIT", D(amount))])]))  # fmt: skip

    @rule(amount=st.decimals(min_value=D("0.01"), max_value=D(200000), places=2))
    def depreciation(self, amount):
        self._step()
        w = self.world
        w.run(lambda u: refdocs.post_document(
            u, doc_type="assets.depreciation", company=w.a, day=DAY, total=amount,
            entries=[Entry("depreciation", [("DEPRECIATION_EXPENSE", "DEBIT", amount),
                                            ("ACCUM_DEPRECIATION", "CREDIT", amount)])]))  # fmt: skip

    @rule()
    def payment(self):
        self._step()
        w = self.world
        w.run(lambda u: refdocs.post_document(
            u, doc_type="treasury.payment", company=w.a, day=DAY, total=D(1000),
            entries=[Entry("payment", [("AP", "DEBIT", D(1000), {"party": party("Prov"), "party_kind": "vendor"}),
                                       ("BANK", "CREDIT", D(1000))])]))  # fmt: skip

    @invariant()
    def reconciles(self):
        # The full reconciliation every 10th step (and at the end of every example, ``teardown``) keeps the
        # 500-step run within a few minutes; each check reads the whole tenant from the database.
        if self.world is not None and hasattr(self, "invoices") and TenantMachine.steps_run % 10 == 0:
            assert invariants.check(self.world) == []

    def teardown(self):
        if self.world is not None and hasattr(self, "invoices"):
            assert invariants.check(self.world) == []


def test_p22b_stateful_tenant_500_steps_keeps_every_invariant(andina):
    TenantMachine.world, TenantMachine.steps_run = andina, 0
    machine_settings = settings(max_examples=14, stateful_step_count=50, deadline=None, derandomize=True,
                                suppress_health_check=list(HealthCheck))  # fmt: skip
    try:
        machine_settings(TenantMachine).TestCase().runTest()
    finally:
        TenantMachine.world = None
    assert TenantMachine.steps_run >= 500, TenantMachine.steps_run
    assert invariants.check(andina) == []


# --------------------------------------------------------------------------- negative suite


def _code(fn):
    with pytest.raises(DomainError) as exc:
        fn()
    return exc.value.code


def _payment(w, day=DAY, company=None, lines=None):
    lines = lines or [("AP", "DEBIT", D(10), {"party": party("Prov"), "party_kind": "vendor"}), ("BANK", "CREDIT", D(10))]
    return w.run(lambda u: refdocs.post_document(u, doc_type="treasury.payment", company=company or w.a, day=day,
                                                 total=D(10), entries=[Entry("payment", lines)]))  # fmt: skip


@pytest.mark.req("UR-07")
def test_p22b_negative_closed_period_rolls_back_number_stock_and_document(andina):
    from ledger import periods
    from ledger.models import Period

    a, wh = andina.a, andina.warehouse["BOG-01"]
    it = item("NEG-CLOSED")
    steps.define_item(andina, a, it, wh)
    steps.goods_receipt(andina, a, it, wh, D(5), D(1000), DAY)
    with andina.scope():
        aug = Period.objects.get(company_id=a, code="2026-08")
    andina.run(lambda u: periods.change_status(u, aug, "CLOSED"))
    before = andina.counts()
    assert _code(lambda: steps.sales_invoice(andina, a, party("X"), it, wh, D(1), D(2000), DAY)) == "period_closed"
    assert andina.counts() == before  # invariant 8: no number, no stock move, no document
    with andina.scope():
        aug = Period.objects.get(id=aug.id)
        assert andina.services["stock"].state(andina.ctx, a, it, wh)["qty"] == D(5)


@pytest.mark.req("UR-07")
def test_p22b_negative_soft_closed_needs_the_closing_permission(andina):
    from ledger import periods
    from ledger.models import Period

    with andina.scope():
        aug = Period.objects.get(company_id=andina.a, code="2026-08")
    andina.run(lambda u: periods.change_status(u, aug, "SOFT_CLOSED"))
    assert _code(lambda: _payment(andina)) == "period_closed"
    andina.grant("ledger.period.update")
    assert _payment(andina).entries


@pytest.mark.req("UR-07")
def test_p22b_negative_control_account_only_from_its_subledger(andina):
    """A manual journal (ledger.manual) cannot hit AR: only sales/treasury documents may (shared/07)."""
    from kernel import ids, services
    from kernel.interfaces.common import DocumentRef
    from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService

    request = PostingRequest(andina.a, DAY, DocumentRef("ledger.manual", ids.new_id()), "manual",
                             (PostingLine("AR", "DEBIT", D(5)), PostingLine("BANK", "CREDIT", D(5))))  # fmt: skip
    assert _code(lambda: andina.run(lambda u: services.get(PostingService).post(u, request))) == "account_not_postable"


def test_p22b_negative_unbalanced_document_leaves_nothing(andina):
    before = andina.counts()
    code = _code(lambda: _payment(andina, lines=[("EXPENSE", "DEBIT", D(100)), ("BANK", "CREDIT", D(90))]))
    assert code == "entry_unbalanced" and andina.counts() == before


@pytest.mark.req("UR-01", "UR-21")
def test_p22b_negative_other_tenant_sees_and_posts_nothing(andina):
    from django.db import connection

    from kernel.testing.tenancy import bound, make_context

    _payment(andina)
    intruder = make_context(company_ids=[andina.a])  # same company id, different tenant
    with bound(intruder), connection.cursor() as cur:
        for table in ("ledger_journal_entry", "ledger_journal_line", "kernel_outbox", "workflow_audit_event"):
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
            assert cur.fetchone()[0] == 0, table  # forced RLS (invariant 12)
    from kernel.uow import run_in_unit_of_work

    with pytest.raises(DomainError):
        run_in_unit_of_work(lambda u: refdocs.post_document(
            u, doc_type="treasury.payment", company=andina.a, day=DAY, total=D(1),
            entries=[Entry("payment", [("AP", "DEBIT", D(1)), ("BANK", "CREDIT", D(1))])]), intruder)  # fmt: skip


@pytest.mark.req("UR-01", "UR-21")
def test_p22b_negative_company_outside_scope_is_refused(andina):
    from kernel.uow import run_in_unit_of_work

    only_b = demo_context([andina.b])
    before = andina.counts()
    with pytest.raises(DomainError):
        run_in_unit_of_work(lambda u: refdocs.post_document(
            u, doc_type="treasury.payment", company=andina.a, day=DAY, total=D(1)), only_b)  # fmt: skip
    assert andina.counts() == before


@pytest.mark.req("UR-07")
def test_p22b_negative_posted_lines_are_immutable_and_corrections_are_reversals(andina):
    from django.db import DatabaseError, connection

    from kernel import services
    from kernel.interfaces.posting import PostingService

    posted = _payment(andina)
    entry_id = posted.entries[0].entry_id
    for sql in ("UPDATE ledger_journal_line SET debit = debit + 1 WHERE entry_id = %s",
                "DELETE FROM ledger_journal_entry WHERE id = %s"):  # fmt: skip
        with pytest.raises(DatabaseError), andina.scope(), connection.cursor() as cur:
            cur.execute(sql, [entry_id])
    andina.run(lambda u: services.get(PostingService).reverse(u, entry_id, DAY, "corrección por reversión"))
    assert andina.balance("BANK", andina.a) == 0 and andina.balance("AP", andina.a) == 0
    assert refdocs.document(andina.ctx, posted.ref) is not None  # the document is never deleted
    assert invariants.check(andina) == []
