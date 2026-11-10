"""S1 - Sale with VAT and customer withholdings (WAC item) [P10, P09, P05, P06, P15] (shared/11 S1).

Real: P05 ledger, P06 tax engine (FIX rates, labelled NOT LEGALLY VERIFIED), sequences, outbox, P07 audit,
the frozen OpenAPI + role templates for the leak checks (step 7). Doubles: stock valuation and the sales
documents (P09a/P10a not merged). Fiscal (step 6) is a module leg pending P15.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, openapi, oracle, principals, refdocs, steps
from qa_harness.andina import item, party

D = Decimal
SALE_DAY, PAY_DAY = dt.date(2026, 3, 4), dt.date(2026, 3, 20)
CUSTOMER = party("Hoteles Sabana S.A.S.")


@pytest.fixture
def s1(andina):
    a, wh, it = andina.a, andina.warehouse["BOG-01"], item("CAF-500")
    steps.define_item(andina, a, it, wh, "WAC")
    steps.goods_receipt(andina, a, it, wh, D(100), D(10000), dt.date(2026, 3, 2))  # S2 R1
    steps.goods_receipt(andina, a, it, wh, D(50), D(13000), dt.date(2026, 3, 3))  # S2 R2
    before = andina.counts()
    inv = steps.sales_invoice(andina, a, CUSTOMER, it, wh, D(120), D(20000), SALE_DAY)
    return andina, inv, before


@pytest.mark.django_db
def test_p22b_S01_invoice_journal_stock_and_audit(s1, stock):
    w, inv, before = s1
    a = w.a
    assert w.lines(inv.ref, "issue") == [("AR", "Dr", D(2856000)), ("REVENUE", "Cr", D(2400000)),
                                         ("VAT_PAYABLE", "Cr", D(456000))]  # fmt: skip
    assert w.lines(inv.ref, "cogs") == [("COGS", "Dr", D(1320000)), ("INVENTORY", "Cr", D(1320000))]
    # Both books carry the entries (mirror), each balanced (invariant 1).
    for book in ("NIIF", "FISCAL"):
        for e in w.entries(inv.ref, book):
            assert e.total_debit == e.total_credit
    assert len(w.entries(inv.ref, "NIIF")) == 2  # invariant 2: exactly the documented set (issue + cogs)
    with w.scope():
        st = stock.state(w.ctx, a, item("CAF-500"), w.warehouse["BOG-01"])
    assert (st["qty"], st["value"]) == (D(30), D(330000))
    assert w.balance("INVENTORY", a) == st["value"]  # invariant 3
    doc = refdocs.document(w.ctx, inv.ref)
    assert doc["number"] == "FV-1" and doc["status"] == "posted" and doc["total"] == D(2856000)
    assert any("NOT LEGALLY VERIFIED" in x for x in doc["payload"]["tax_warnings"])  # C1-9 label travels
    after = w.counts()
    # One document: 1 number, 1 stock move, 2 entries × 2 books, 1 document event + 4 ledger events, audit rows.
    assert after["numbers_consumed"] - before["numbers_consumed"] == 1 + 4  # FV + GL numbers (2 entries × 2 books)
    assert after["stock_moves"] - before["stock_moves"] == 1
    assert after["journal_entries"] - before["journal_entries"] == 4
    assert after["outbox"] - before["outbox"] >= 1
    _assert_audit(w, inv.ref)


def _assert_audit(w, ref):
    from kernel.models import OutboxMessage
    from workflow.models import AuditEvent

    with w.scope():
        actions = set(AuditEvent.objects.filter(resource_id=ref.id).values_list("action", flat=True))
        envelopes = list(
            OutboxMessage.objects.filter(event_type="sales.invoice.posted.v1").values_list("envelope", flat=True)
        )
        chain = list(AuditEvent.objects.order_by("seq").values_list("seq", flat=True))
    assert "sales.invoice.post" in actions
    assert [e["payload"]["number"] for e in envelopes] == ["FV-1"]
    assert chain == list(range(1, len(chain) + 1))  # hash-chained audit log without gaps (P07)


@pytest.mark.django_db
def test_p22b_S01_customer_receipt_with_withholdings(s1):
    w, inv, _ = s1
    receipt, tax = steps.customer_receipt(w, w.a, CUSTOMER, inv, D(2856000), D(2400000), PAY_DAY)
    assert w.lines(receipt.ref) == [
        ("BANK", "Dr", D(2704416)), ("WHT_RECEIVABLE_ICA", "Dr", D(23184)), ("WHT_RECEIVABLE_IVA", "Dr", D(68400)),
        ("WHT_RECEIVABLE_RENTA", "Dr", D(60000)), ("AR", "Cr", D(2856000)),
    ]  # fmt: skip
    assert w.balance("AR", w.a) == 0  # invariant 4: the only open item is settled
    snap_codes = {s["code"] for s in tax.parameter_snapshots}
    assert {"fix.iva.general", "fix.retefuente.ventas", "fix.reteiva", "fix.reteica.bogota"} <= snap_codes
    assert all(s["synthetic"] for s in tax.parameter_snapshots)
    t = golden.sale_totals(D(120), D(20000))
    assert refdocs.document(w.ctx, receipt.ref)["total"] == t.receipt


@pytest.mark.django_db
def test_p22b_S01_no_withholding_when_customer_is_not_an_agent(andina):
    from qa_harness import taxes

    result = taxes.compute(andina.a, SALE_DAY, [("1", D(2400000), ("FIX-IVA19", "FIX-RF-VENTAS", "FIX-RETEIVA",
                                                                "FIX-RETEICA"))], buyer_agent=False)  # fmt: skip
    assert taxes.by_type(result) == {"IVA": D(456000)}


# --------------------------------------------------------------------------- step 7: leak checks (contract)

COST_MARKERS = ("cost", "margin", "cogs", "valuation")


def _cost_fields_visible_to(kind: principals.Kind) -> list[tuple[str, str, str]]:
    p = principals.matrix().of("T1", kind)
    out = []
    for op in openapi.operations():
        if op.method != "get" or not p.allows(op.permission):
            continue
        hidden = oracle.forbidden_fields(p, op)
        for name, _cls in op.response_fields:
            leaf = name.rsplit(".", 1)[-1].lower()
            if "cost_center" in leaf or name in hidden:
                continue
            if any(m in leaf for m in COST_MARKERS):
                out.append((op.path, op.permission, name))
    return out


@defects.xfail("DEF-P22-005")
@pytest.mark.req("UR-21")
def test_p22b_S01_step7_vendedor_sees_no_cost_fields_on_any_channel():
    """API list/detail, export, report, AI and MCP read the same contract operations; the vendedor role must
    not reach any cost/margin/valuation field through any of them."""
    assert _cost_fields_visible_to(principals.Kind.SALESPERSON) == []


def test_p22b_S01_step7_cost_field_audit_is_not_vacuous():
    """Guard for the check above: the contract does expose cost fields to roles that may see them."""
    assert _cost_fields_visible_to(principals.Kind.ADMIN)


@defects.xfail("DEF-P22-006")
@pytest.mark.req("UR-21")
def test_p22b_S01_step7_vendedor_reads_invoices_of_own_customers():
    p = principals.matrix().of("T1", principals.Kind.SALESPERSON)
    ops = [op for op in openapi.operations() if op.path == "/api/v1/sales/invoices/{id}" and op.method == "get"]
    assert ops and p.allows(ops[0].permission)


def test_p22b_S01_step7_ai_and_mcp_never_exceed_the_vendedor():
    """AI delegated / MCP limited principals act with ⊆ the delegating user's permissions (P22 §4)."""
    m = principals.matrix()
    ai, mcp = m.of("T1", principals.Kind.AI_DELEGATED), m.of("T1", principals.Kind.MCP_LIMITED)
    for op in openapi.operations():
        for agent in (ai, mcp):
            user = oracle.effective_user(agent)
            if agent.allows(op.permission):
                assert user.allows(op.permission), (agent.key, op.permission)


# --------------------------------------------------------------------------- module legs


@pytest.mark.django_db
@pytest.mark.req("UR-09")
def test_p22b_S01_module_leg_fiscal_document_accepted_in_sandbox(andina):
    """Step 6: FE (UBL) generated, CUFE computed, sandbox accepted, AttachedDocument, fiscal status accepted."""
    defects.require("PEND-P10", "PEND-P15")
    from kernel import services
    from kernel.interfaces.fiscal import FiscalGateway

    assert services.optional(FiscalGateway) is not None


@pytest.mark.django_db
@pytest.mark.req("UR-09")
def test_p22b_S01_module_leg_sales_api(andina):
    defects.require("PEND-P10", "PEND-P09")
    from qa_harness.routes import resolves

    assert resolves("/api/v1/sales/invoices")
