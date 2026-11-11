"""F-TRE-CE — comprobantes de egreso and payment batches through the API, the real P05 ledger, P11's real AP
open-item table and the real P07 approvals (synthetic data).

T-TRE-CE-01 three partial AP payments with withholding · T-TRE-CE-02 direct concept CE with cost center ·
T-TRE-CE-03 batch execution → one CE per payee + FIXTURE_NOT_CERTIFIED file · T-TRE-CE-04 dual approval / SoD ·
T-TRE-CE-05 reversal re-opens AP items.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection

from treasury.interfaces import BANK_FILE_LABEL
from treasury.models import PaymentBatch, TreasuryVoucher
from treasury.testing.support import TODAY, ApiClient, World, money

D = Decimal
pytestmark = pytest.mark.django_db


def fund_bank(world: World, api: ApiClient, amount: str) -> None:
    """Capital contribution RC so the bank fund (no overdraft) has money to pay with."""
    capital = world.concept(f"APORTE-{amount}", "IN", "CAPITAL")
    rc = api.post(
        "receipts",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": str(world.env["fund"].id),
            "payment_method": "TRANSFER",
            "lines": [{"line_no": 1, "concept_id": str(capital.id), "applied_amount": money(amount)}],
        },
    ).json()
    assert api.post(f"receipts/{rc['id']}:post").status_code == 200


def ce_body(world: World, supplier: Any, lines: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_date": TODAY.isoformat(),
        "fund_id": str(world.env["fund"].id),
        "payment_method": "TRANSFER",
        "currency": "COP",
        "lines": lines,
        **extra,
    }
    if supplier is not None:
        body["party"] = {"id": str(supplier.party_id), "kind": "vendor"}
    return body


def ap_balance(world: World, item: Any) -> tuple[Decimal, str]:
    from purchasing.models import ApOpenItem

    with world.scope():
        row = ApOpenItem.objects.get(id=item.id)
        return row.balance, row.status


def three_bills(world: World, code: str = "P-CE01") -> tuple[Any, list[Any]]:
    supplier = world.vendor(code)
    bills = [
        world.ap_item(supplier, f"FP-{code}-{n}", amount)
        for n, amount in ((1, "1000000"), (2, "2000000"), (3, "3000000"))
    ]
    return supplier, bills


def ce01_lines(bills: list[Any]) -> list[dict[str, Any]]:
    return [
        {"line_no": 1, "open_item_id": str(bills[0].id), "applied_amount": money("500000")},
        {"line_no": 2, "open_item_id": str(bills[1].id), "applied_amount": money("1200000")},
        {"line_no": 3, "open_item_id": str(bills[2].id), "applied_amount": money("3000000")},
        {"line_no": 4, "tax_code": "RTE25", "unit_price": money("4700000"), "description": "Retefuente"},
    ]


def test_T_TRE_CE_01_three_partial_bills_with_withholding_exact_journal(world: World, api: ApiClient) -> None:
    world.configure(ce_approval_threshold=None)
    supplier, bills = three_bills(world)
    created = api.post("disbursements", ce_body(world, supplier, ce01_lines(bills)))
    assert created.status_code == 201, created.content
    ce = created.json()
    # paid = 500,000 + 1,200,000 + 3,000,000 − 2.5 % × 4,700,000 (117,500) = 4,582,500
    assert ce["total"] == money("4582500.0000")
    # the fund forbids overdraft and is empty → blocking form of treasury_insufficient_funds_warning
    broke = api.post(f"disbursements/{ce['id']}:post")
    assert broke.status_code == 422 and broke.json()["code"] == "treasury_insufficient_funds_warning"
    assert ap_balance(world, bills[0]) == (D("1000000.0000"), "OPEN")
    fund_bank(world, api, "10000000")
    posted = api.post(f"disbursements/{ce['id']}:post")
    assert posted.status_code == 200, posted.content
    assert posted.json()["number"] == "CE-000001"
    with world.scope():
        v = TreasuryVoucher.objects.get(id=ce["id"])
    assert v.fund_snapshot["book_balance"] == "10000000.0000"
    assert world.entry_lines(v.posted_entry_id) == [
        ("111005", D("0.0000"), D("4582500.0000")),
        ("220505", D("500000.0000"), D("0.0000")),
        ("220505", D("1200000.0000"), D("0.0000")),
        ("220505", D("3000000.0000"), D("0.0000")),
        ("236540", D("0.0000"), D("117500.0000")),
    ]
    assert ap_balance(world, bills[0]) == (D("500000.0000"), "PARTIAL")
    assert ap_balance(world, bills[1]) == (D("800000.0000"), "PARTIAL")
    assert ap_balance(world, bills[2]) == (D("0.0000"), "PAID")
    from purchasing.models import ApOpenItem

    with world.scope():
        cop = {i.id: i.balance_cop for i in ApOpenItem.objects.filter(id__in=[b.id for b in bills])}
    assert cop == {bills[0].id: D("500000.0000"), bills[1].id: D("800000.0000"), bills[2].id: D("0.0000")}
    # a payable of another vendor, and paying past the balance, are refused
    other, other_bills = three_bills(world, "P-OTRO")
    wrong = api.post(
        "disbursements",
        ce_body(
            world, supplier, [{"line_no": 1, "open_item_id": str(other_bills[0].id), "applied_amount": money("1")}]
        ),
    )
    assert wrong.status_code == 422 and wrong.json()["code"] == "treasury_open_item_mismatch_party"
    over = api.post(
        "disbursements",
        ce_body(world, supplier, [{"line_no": 1, "open_item_id": str(bills[2].id), "applied_amount": money("1")}]),
    )
    assert over.status_code == 422 and over.json()["code"] == "treasury_overapplication"


def test_T_TRE_CE_02_direct_concept_disbursement_with_cost_center(world: World, api: ApiClient) -> None:
    world.configure(ce_approval_threshold=None)
    cc = world.cost_center("CC-VENTAS")
    expense = world.concept("PAPELERIA", "OUT", "EXPENSE", requires_dimension=True)
    fund_bank(world, api, "1000000")
    body = ce_body(
        world,
        None,
        [
            {
                "line_no": 1,
                "concept_id": str(expense.id),
                "unit_price": money("85000"),
                "quantity": {"value": "2", "uom": "UND"},
                "dimensions": {"cost_center_id": str(cc.id)},
                "description": "Resmas de papel",
            }
        ],
        payment_method="CASH",
    )
    ce = api.post("disbursements", body).json()
    assert ce["total"] == money("170000.0000")
    posted = api.post(f"disbursements/{ce['id']}:post")
    assert posted.status_code == 200, posted.content
    from ledger.models import JournalLine

    with world.scope():
        v = TreasuryVoucher.objects.get(id=ce["id"])
        lines = list(
            JournalLine.objects.filter(entry_id=v.posted_entry_id, entry__book__primary=True)
            .select_related("account")
            .order_by("line_no")
        )
    assert [(x.account.code, x.debit, x.credit) for x in lines] == [
        ("111005", D("0.0000"), D("170000.0000")),
        ("519595", D("170000.0000"), D("0.0000")),
    ]
    assert lines[1].cost_center_id == cc.id
    assert "party" not in posted.json()
    # IN-only concepts cannot be disbursed; a missing dimension is refused
    loan = world.concept("PRESTAMO-IN", "IN", "LOAN_PAYABLE")
    bad = api.post(
        "disbursements", {**body, "lines": [{"line_no": 1, "concept_id": str(loan.id), "applied_amount": money("1")}]}
    )
    assert bad.status_code == 422
    nodim = api.post(
        "disbursements",
        {**body, "lines": [{"line_no": 1, "concept_id": str(expense.id), "applied_amount": money("1")}]},
    )
    assert nodim.status_code == 422 and nodim.json()["code"] == "dimension_required"


def test_T_TRE_CE_02_overdraft_fund_posts_with_warning(world: World, api: ApiClient) -> None:
    from treasury.services.masters import update_fund

    world.configure(ce_approval_threshold=None)
    expense = world.concept("FLETE", "OUT", "EXPENSE")
    fund = world.env["fund"]
    world.run("admin", lambda u: update_fund(u, fund, {"allow_overdraft": True}))
    ce = api.post(
        "disbursements",
        ce_body(world, None, [{"line_no": 1, "concept_id": str(expense.id), "applied_amount": money("5000")}]),
    ).json()
    sim = api.post(f"disbursements/{ce['id']}:simulate").json()
    assert [w["code"] for w in sim["warnings"]] == ["treasury_insufficient_funds_warning"]
    assert api.post(f"disbursements/{ce['id']}:post").status_code == 200
    with world.scope():
        assert TreasuryVoucher.objects.get(id=ce["id"]).warnings == ["treasury_insufficient_funds_warning"]


# --------------------------------------------------------------------------- T-TRE-CE-03 payment batches


def test_T_TRE_CE_03_batch_execution_one_ce_per_payee_gap_free_fixture_file(world: World, api: ApiClient) -> None:
    world.configure(ce_approval_threshold="0")
    world.approval_policies(["approver", "approver2"])
    fund_bank(world, api, "20000000")
    v1, bills1 = three_bills(world, "P-LOTE1")
    v2, bills2 = three_bills(world, "P-LOTE2")
    body = {
        "document_date": TODAY.isoformat(),
        "payment_date": TODAY.isoformat(),
        "fund_id": str(world.env["fund"].id),
        "currency": "COP",
        "lines": [
            {"line_no": 1, "party": {"id": str(v1.party_id)}, "open_item_id": str(bills1[0].id), "applied_amount": money("1000000")},
            {"line_no": 2, "party": {"id": str(v2.party_id)}, "open_item_id": str(bills2[1].id), "applied_amount": money("2000000"),
             "tax_code": "RTE25"},
            {"line_no": 3, "party": {"id": str(v1.party_id)}, "open_item_id": str(bills1[2].id), "applied_amount": money("1500000")},
        ],
    }  # fmt: skip
    created = api.post("payment-batches", body)
    assert created.status_code == 201, created.content
    batch = created.json()
    assert batch["total"] == money("4500000.0000") and batch["number"] == "LP-000001"
    early = api.post(f"payment-batches/{batch['id']}:execute")
    assert early.status_code == 409
    assert api.post(f"payment-batches/{batch['id']}:submit").json()["status"] == "SUBMITTED"
    blocked = api.post(f"payment-batches/{batch['id']}:execute")
    assert blocked.status_code == 422 and blocked.json()["code"] == "treasury_approval_required"
    sod = api.post(f"payment-batches/{batch['id']}:approve")
    assert sod.status_code == 403  # the cashier holds no approval permission
    approved = api.as_("approver").post(f"payment-batches/{batch['id']}:approve")
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    preview = api.as_("cashier").post(f"payment-batches/{batch['id']}:generate-file")
    assert preview.status_code == 202 and preview.json()["file"]["label"] == BANK_FILE_LABEL
    executed = api.post(f"payment-batches/{batch['id']}:execute")
    assert executed.status_code == 202, executed.content
    op = executed.json()
    assert op["status"] == "SUCCEEDED"
    assert op["file"]["label"] == BANK_FILE_LABEL and op["file"]["content"].startswith(f"# {BANK_FILE_LABEL}")
    with world.scope():
        ces = list(TreasuryVoucher.objects.filter(source_id=batch["id"]).order_by("number"))
        status = PaymentBatch.objects.get(id=batch["id"]).status
    # one CE per payee, consecutive numbers, in payee order of first appearance
    assert [(c.number, c.party_id) for c in ces] == [("CE-000001", v1.party_id), ("CE-000002", v2.party_id)]
    assert [c.total for c in ces] == [D("2500000.0000"), D("1950000.0000")]  # 2,000,000 − 2.5 % retefuente
    assert all(c.status == "POSTED" and c.approved_by == [str(world.ctx("approver").principal_id)] for c in ces)
    assert status == "POSTED"
    assert ap_balance(world, bills1[0]) == (D("0.0000"), "PAID")
    assert ap_balance(world, bills1[2]) == (D("1500000.0000"), "PARTIAL")
    assert ap_balance(world, bills2[1]) == (D("0.0000"), "PAID")
    assert world.entry_lines(ces[1].posted_entry_id) == [
        ("111005", D("0.0000"), D("1950000.0000")),
        ("220505", D("2000000.0000"), D("0.0000")),
        ("236540", D("0.0000"), D("50000.0000")),
    ]
    rows = op["file"]["content"].splitlines()
    assert rows[1] == "lote,linea,comprobante,beneficiario,valor,referencia"
    assert rows[2].startswith("LP-000001,1,CE-000001,") and ",2500000.0000," in rows[2]
    assert rows[3].startswith("LP-000001,2,CE-000002,") and ",1950000.0000," in rows[3]
    again = api.post(f"payment-batches/{batch['id']}:execute")
    assert again.status_code == 409  # executed once
    sent = api.post(f"payment-batches/{batch['id']}:mark-sent")
    assert sent.status_code == 200
    # a later single CE continues the same gap-free sequence
    single = api.post(
        "disbursements",
        ce_body(world, v1, [{"line_no": 1, "open_item_id": str(bills1[1].id), "applied_amount": money("1")}]),
    ).json()
    assert api.post(f"disbursements/{single['id']}:submit").status_code == 200
    api.as_("approver").post(f"disbursements/{single['id']}:approve")
    assert api.as_("cashier").post(f"disbursements/{single['id']}:post").json()["number"] == "CE-000003"


# --------------------------------------------------------------------------- T-TRE-CE-04 dual approval


def test_T_TRE_CE_04_dual_approval_above_threshold_with_sod(world: World, api: ApiClient) -> None:
    from workflow.models import ApprovalRequest

    world.configure(ce_approval_threshold="0", dual_approval_threshold="20000000")
    world.approval_policies(["approver", "approver2", "cashier"])
    fund_bank(world, api, "30000000")
    supplier = world.vendor("P-DUAL")
    bill = world.ap_item(supplier, "FP-DUAL-1", "25000000")
    ce = api.post(
        "disbursements",
        ce_body(world, supplier, [{"line_no": 1, "open_item_id": str(bill.id), "applied_amount": money("25000000")}]),
    ).json()
    assert api.post(f"disbursements/{ce['id']}:post").json()["code"] == "treasury_approval_required"
    assert api.post(f"disbursements/{ce['id']}:submit").json()["status"] == "SUBMITTED"
    with world.scope():
        assert ApprovalRequest.objects.get(document_id=ce["id"]).policy.code == "TES-DUAL"
    # SoD: the creator (even an assigned approver with the permission) cannot approve
    world.policy.grant(world.ctx("cashier").principal_id, "treasury.disbursement.approve", "workflow.approval.approve")
    own = api.post(f"disbursements/{ce['id']}:approve")
    assert own.status_code == 403 and own.json()["code"] == "sod_violation"
    first = api.as_("approver").post(f"disbursements/{ce['id']}:approve")
    assert first.status_code == 200 and first.json()["status"] == "SUBMITTED"  # one of two approvals
    assert api.as_("cashier").post(f"disbursements/{ce['id']}:post").json()["code"] == "treasury_approval_required"
    twice = api.as_("approver").post(f"disbursements/{ce['id']}:approve")
    assert twice.status_code == 403 and twice.json()["code"] == "sod_violation"
    second = api.as_("approver2").post(f"disbursements/{ce['id']}:approve")
    assert second.status_code == 200 and second.json()["status"] == "APPROVED"
    posted = api.as_("cashier").post(f"disbursements/{ce['id']}:post")
    assert posted.status_code == 200 and posted.json()["status"] == "POSTED"
    with world.scope():
        v = TreasuryVoucher.objects.get(id=ce["id"])
    assert v.dual_approval and v.approved_by == [
        str(world.ctx("approver").principal_id),
        str(world.ctx("approver2").principal_id),
    ]
    # below the dual threshold a single approver (TES-CE) is enough
    small_bill = world.ap_item(supplier, "FP-DUAL-2", "100000")
    small = api.post(
        "disbursements",
        ce_body(
            world, supplier, [{"line_no": 1, "open_item_id": str(small_bill.id), "applied_amount": money("100000")}]
        ),
    ).json()
    api.post(f"disbursements/{small['id']}:submit")
    with world.scope():
        assert ApprovalRequest.objects.get(document_id=small["id"]).policy.code == "TES-CE"
    assert api.as_("approver2").post(f"disbursements/{small['id']}:approve").json()["status"] == "APPROVED"


def test_T_TRE_CE_04_dual_approval_of_a_payment_batch(world: World, api: ApiClient) -> None:
    world.configure(ce_approval_threshold="0", dual_approval_threshold="20000000")
    world.approval_policies(["approver", "approver2"])
    fund_bank(world, api, "30000000")
    supplier = world.vendor("P-LDUAL")
    bill = world.ap_item(supplier, "FP-LD-1", "21000000")
    batch = api.post(
        "payment-batches",
        {
            "document_date": TODAY.isoformat(),
            "payment_date": TODAY.isoformat(),
            "fund_id": str(world.env["fund"].id),
            "lines": [{"line_no": 1, "party": {"id": str(supplier.party_id)}, "open_item_id": str(bill.id),
                       "applied_amount": money("21000000")}],
        },
    ).json()  # fmt: skip
    api.post(f"payment-batches/{batch['id']}:submit")
    one = api.as_("approver").post(f"payment-batches/{batch['id']}:approve")
    assert one.json()["status"] == "SUBMITTED"
    assert api.as_("approver").post(f"payment-batches/{batch['id']}:approve").json()["code"] == "sod_violation"
    assert (
        api.as_("cashier").post(f"payment-batches/{batch['id']}:execute").json()["code"] == "treasury_approval_required"
    )
    assert api.as_("approver2").post(f"payment-batches/{batch['id']}:approve").json()["status"] == "APPROVED"
    assert api.as_("cashier").post(f"payment-batches/{batch['id']}:execute").status_code == 202


# --------------------------------------------------------------------------- T-TRE-CE-05 reversal


def test_T_TRE_CE_05_reversal_reopens_ap_items_and_keeps_the_original(world: World, api: ApiClient) -> None:
    world.configure(ce_approval_threshold=None)
    fund_bank(world, api, "10000000")
    supplier, bills = three_bills(world, "P-REV")
    ce = api.post("disbursements", ce_body(world, supplier, ce01_lines(bills))).json()
    assert api.post(f"disbursements/{ce['id']}:post").status_code == 200
    with world.scope():
        original = TreasuryVoucher.objects.get(id=ce["id"])
    assert ap_balance(world, bills[2]) == (D("0.0000"), "PAID")
    rev = api.post(f"disbursements/{ce['id']}:reverse", {"reason": "Transferencia devuelta por el banco"})
    assert rev.status_code == 200, rev.content
    assert rev.json()["status"] == "REVERSED" and rev.json()["number"] == "CE-000001"
    assert ap_balance(world, bills[0]) == (D("1000000.0000"), "OPEN")
    assert ap_balance(world, bills[1]) == (D("2000000.0000"), "OPEN")
    assert ap_balance(world, bills[2]) == (D("3000000.0000"), "OPEN")
    with world.scope():
        mirror = TreasuryVoucher.objects.get(reversal_of_id=ce["id"])
        refreshed = TreasuryVoucher.objects.get(id=ce["id"])
        from purchasing.models import ApOpenItem

        assert ApOpenItem.objects.get(id=bills[2].id).balance_cop == D("3000000.0000")
    assert mirror.number == "CE-000002"
    orig_lines = world.entry_lines(original.posted_entry_id)
    assert world.entry_lines(mirror.posted_entry_id) == [(c, cr, dr) for c, dr, cr in orig_lines]
    assert (refreshed.total, refreshed.posted_entry_id, refreshed.number, refreshed.posted) == (
        original.total,
        original.posted_entry_id,
        original.number,
        True,
    )
    # immutable in the database too: no update other than POSTED → REVERSED, no delete
    with pytest.raises(Exception, match="immutable"), world.scope():
        TreasuryVoucher.objects.filter(id=ce["id"]).update(notes="cambio")
    with pytest.raises(Exception, match="immutable"), world.scope(), connection.cursor() as cur:
        cur.execute("DELETE FROM treasury_voucher WHERE id = %s", [ce["id"]])
    assert api.post(f"disbursements/{ce['id']}:reverse", {"reason": "otra"}).status_code == 409
    # the fund balance is back to the contribution
    snap = api.get(f"funds/{world.env['fund'].id}/snapshot").json()
    assert snap["rows"][0]["book_balance"] == "10000000.0000"
