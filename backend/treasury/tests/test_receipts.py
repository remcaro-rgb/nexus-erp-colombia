"""F-TRE-RC — recibos de caja through the API, the real P05 ledger and P10's real AR sub-ledger (synthetic data).

T-TRE-RC-01 two partial AR applications + advance + customer withholding · T-TRE-RC-02 concept RC with dimensions ·
T-TRE-RC-04 failure injection (withholding parameter, ledger) rolls back everything · T-TRE-RC-05 reversal ·
T-TRE-RC-06 approval threshold through P07 with SoD.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from kernel import services
from kernel.errors import DomainError
from kernel.interfaces.posting import PostingService
from kernel.models import OutboxMessage, Sequence
from treasury.models import TreasuryVoucher
from treasury.testing.support import TODAY, ApiClient, World, money

D = Decimal
pytestmark = pytest.mark.django_db


def receipt_body(world: World, party: Any, lines: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "document_date": TODAY.isoformat(),
        "fund_id": str(world.env["fund"].id),
        "payment_method": "TRANSFER",
        "currency": "COP",
        "party": {"id": str(party.id), "kind": "customer"} if party is not None else None,
        "lines": lines,
        **extra,
    }


def ar_balance(world: World, item: Any) -> tuple[Decimal, str]:
    from sales.models import ArOpenItem

    with world.scope():
        row = ArOpenItem.objects.get(id=item.id)
        return row.balance, row.status


def two_invoices(world: World) -> tuple[Any, Any, Any]:
    customer = world.customer("C-RC01")
    inv1 = world.ar_item(customer, "SETT-1001", "1000000")
    inv2 = world.ar_item(customer, "SETT-1002", "2000000")
    return customer, inv1, inv2


def rc01_lines(inv1: Any, inv2: Any) -> list[dict[str, Any]]:
    return [
        {"line_no": 1, "open_item_id": str(inv1.id), "applied_amount": money("600000")},
        {"line_no": 2, "open_item_id": str(inv2.id), "applied_amount": money("1500000")},
        {"line_no": 3, "description": "Anticipo saldo a favor", "applied_amount": money("400000")},
        {"line_no": 4, "tax_code": "RTE25", "unit_price": money("2100000"), "description": "Retefuente practicada"},
    ]


def test_T_TRE_RC_01_two_partial_invoices_advance_and_withholding_exact_journal(world: World, api: ApiClient) -> None:
    customer, inv1, inv2 = two_invoices(world)
    created = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2)))
    assert created.status_code == 201, created.content
    rc = created.json()
    assert rc["status"] == "DRAFT" and rc["number"] == ""
    # received = 600,000 + 1,500,000 + 400,000 − 2.5 % × 2,100,000 (52,500) = 2,447,500
    assert rc["total"] == money("2447500.0000")
    assert rc["withholdings"]["total"] == money("52500.0000")
    assert ar_balance(world, inv1) == (D("1000000.0000"), "OPEN")  # a draft applies nothing

    preview = api.post(f"receipts/{rc['id']}:simulate")
    assert preview.status_code == 200, preview.content
    assert preview.json()["persisted"] is False
    before_entries = world.entry_count()

    posted = api.post(f"receipts/{rc['id']}:post")
    assert posted.status_code == 200, posted.content
    body = posted.json()
    assert body["status"] == "POSTED"
    assert body["number"] == "RC-000001"
    with world.scope():
        v = TreasuryVoucher.objects.get(id=rc["id"])
        assert v.posted and v.posted_entry_id is not None
        assert v.fund_snapshot["book_balance"] == "0.0000"
        events = list(
            OutboxMessage.objects.filter(event_type="treasury.receipt.posted.v1").values_list("envelope", flat=True)
        )
    assert world.entry_count() == before_entries + 1
    # Exact journal lines (primary book, in order): Dr bank, Cr AR × 2, Cr customer advances, Dr withholding.
    assert world.entry_lines(v.posted_entry_id) == [
        ("111005", D("2447500.0000"), D("0.0000")),
        ("130505", D("0.0000"), D("600000.0000")),
        ("130505", D("0.0000"), D("1500000.0000")),
        ("280505", D("0.0000"), D("400000.0000")),
        ("135515", D("52500.0000"), D("0.0000")),
    ]
    # the simulation previewed exactly these lines
    sim = preview.json()["journal_entries"][0]["lines"]
    assert [(x["debit"]["amount"], x["credit"]["amount"]) for x in sim] == [
        ("2447500.0000", "0.0000"),
        ("0.0000", "600000.0000"),
        ("0.0000", "1500000.0000"),
        ("0.0000", "400000.0000"),
        ("52500.0000", "0.0000"),
    ]
    # Open items updated in the same unit of work.
    assert ar_balance(world, inv1) == (D("400000.0000"), "PARTIAL")
    assert ar_balance(world, inv2) == (D("500000.0000"), "PARTIAL")
    payload = next(e for e in events if e["payload"]["receipt_id"] == rc["id"])["payload"]
    assert payload["number"] == "RC-000001" and payload["amount"] == money("2447500.0000")
    assert payload["journal_entry_id"] == str(v.posted_entry_id)
    # Posted: immutable through the API (PATCH refused) and in the database (trigger).
    detail = api.get(f"receipts/{rc['id']}")
    patched = api.send("patch", f"receipts/{rc['id']}", {"notes": "x"}, if_match=detail["ETag"])
    assert patched.status_code == 409 and patched.json()["code"] == "document_immutable"
    with pytest.raises(Exception, match="immutable"), world.scope():
        TreasuryVoucher.objects.filter(id=rc["id"]).update(total=D(1))


def test_T_TRE_RC_01_overapplication_and_other_party_items_are_refused(world: World, api: ApiClient) -> None:
    customer, inv1, _ = two_invoices(world)
    other = world.customer("C-OTRO")
    foreign = world.ar_item(other, "SETT-2001", "500000")
    over = api.post(
        "receipts",
        receipt_body(
            world, customer, [{"line_no": 1, "open_item_id": str(inv1.id), "applied_amount": money("1000001")}]
        ),
    )
    assert over.status_code == 422 and over.json()["code"] == "treasury_overapplication"
    mismatch = api.post(
        "receipts",
        receipt_body(world, customer, [{"line_no": 1, "open_item_id": str(foreign.id), "applied_amount": money("1")}]),
    )
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "treasury_open_item_mismatch_party"
    typed = api.post(
        "receipts",
        receipt_body(world, customer, [{"line_no": 1, "account_code": "130505", "applied_amount": money("1")}]),
    )
    assert typed.status_code == 422 and typed.json()["code"] == "field_forbidden"


def test_T_TRE_RC_02_concept_receipt_without_ar_posts_to_concept_accounts_with_dims(
    world: World, api: ApiClient
) -> None:
    cc = world.cost_center("CC-ADM")
    loan = world.concept("PRESTAMO", "IN", "LOAN_PAYABLE")
    refund = world.concept("REINTEGRO", "BOTH", "REIMBURSEMENT", requires_dimension=True)
    body = receipt_body(
        world,
        None,
        [
            {"line_no": 1, "concept_id": str(loan.id), "applied_amount": money("5000000"), "description": "Crédito"},
            {
                "line_no": 2,
                "concept_id": str(refund.id),
                "unit_price": money("125000"),
                "quantity": {"value": "2", "uom": "UND"},
                "dimensions": {"cost_center_id": str(cc.id)},
            },
        ],
        payment_method="CASH",
        fund_id=str(world.env["cash_fund"].id),
    )
    body.pop("party")
    created = api.post("receipts", body)
    assert created.status_code == 201, created.content
    rc = created.json()
    assert rc["total"] == money("5250000.0000")
    posted = api.post(f"receipts/{rc['id']}:post")
    assert posted.status_code == 200, posted.content
    from ledger.models import JournalLine

    with world.scope():
        v = TreasuryVoucher.objects.get(id=rc["id"])
        lines = list(
            JournalLine.objects.filter(entry_id=v.posted_entry_id, entry__book__primary=True)
            .select_related("account")
            .order_by("line_no")
        )
    assert [(ln.account.code, ln.debit, ln.credit) for ln in lines] == [
        ("110505", D("5250000.0000"), D("0.0000")),  # cash box (journal CASH)
        ("210505", D("0.0000"), D("5000000.0000")),
        ("138095", D("0.0000"), D("250000.0000")),
    ]
    assert lines[2].cost_center_id == cc.id
    assert lines[1].cost_center_id is None
    # a concept requiring a dimension refuses a line without one
    missing = api.post(
        "receipts",
        {**body, "lines": [{"line_no": 1, "concept_id": str(refund.id), "applied_amount": money("1000")}]},
    )
    assert missing.status_code == 422 and missing.json()["code"] == "dimension_required"
    # an OUT-only concept cannot be received
    out = world.concept("GASTO", "OUT", "EXPENSE")
    wrong = api.post(
        "receipts", {**body, "lines": [{"line_no": 1, "concept_id": str(out.id), "applied_amount": money("1")}]}
    )
    assert wrong.status_code == 422


# --------------------------------------------------------------------------- T-TRE-RC-04 failure injection


class ExplodingPosting:
    """PostingService that validates then fails (after the open items were applied in the same UoW)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def post(self, uow: Any, request: Any) -> Any:
        self.inner.simulate(uow, request)
        raise DomainError("injected ledger failure", code="ledger_unavailable")

    def simulate(self, uow: Any, request: Any) -> Any:
        return self.inner.simulate(uow, request)

    def reverse(self, uow: Any, entry_id: Any, reversal_date: dt.date, reason: str) -> Any:
        return self.inner.reverse(uow, entry_id, reversal_date, reason)


def _seq_value(world: World) -> int:
    with world.scope():
        row = Sequence.objects.filter(company_id=world.company, sequence_code="RC").first()
        return 0 if row is None else int(row.next_number)


def test_T_TRE_RC_04_ledger_failure_rolls_back_number_open_items_and_entry(world: World, api: ApiClient) -> None:
    customer, inv1, inv2 = two_invoices(world)
    rc = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2))).json()
    seq_before, entries_before = _seq_value(world), world.entry_count()
    with services.override(PostingService, ExplodingPosting(services.get(PostingService))):
        failed = api.post(f"receipts/{rc['id']}:post")
    assert failed.status_code >= 400 and failed.json()["code"] == "ledger_unavailable"
    assert _seq_value(world) == seq_before  # number returned
    assert world.entry_count() == entries_before  # no partial ledger
    assert ar_balance(world, inv1) == (D("1000000.0000"), "OPEN")  # open items untouched
    assert ar_balance(world, inv2) == (D("2000000.0000"), "OPEN")
    with world.scope():
        v = TreasuryVoucher.objects.get(id=rc["id"])
        assert v.status == "DRAFT" and v.number == "" and not v.posted
    # after the failure the same voucher posts with the first number (gap-free)
    ok = api.post(f"receipts/{rc['id']}:post")
    assert ok.status_code == 200 and ok.json()["number"] == "RC-000001"


def test_T_TRE_RC_04_unknown_withholding_parameter_fails_closed_and_rolls_back(world: World, api: ApiClient) -> None:
    customer, inv1, inv2 = two_invoices(world)
    rc = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2))).json()
    world.params.values.pop("tax.rate.RTE25")  # the parameter is withdrawn before posting → UNKNOWN
    seq_before, entries_before = _seq_value(world), world.entry_count()
    failed = api.post(f"receipts/{rc['id']}:post")
    assert failed.status_code == 422 and failed.json()["code"] == "tax_parameter_unknown"
    assert _seq_value(world) == seq_before and world.entry_count() == entries_before
    assert ar_balance(world, inv1) == (D("1000000.0000"), "OPEN")
    with world.scope():
        assert TreasuryVoucher.objects.get(id=rc["id"]).number == ""
    unknown = api.post(
        "receipts",
        receipt_body(world, customer, [{"line_no": 1, "tax_code": "RTE-NO-EXISTE", "unit_price": money("1000")},
                                        {"line_no": 2, "applied_amount": money("5000")}]),
    )  # fmt: skip
    assert unknown.status_code == 422 and unknown.json()["code"] == "tax_parameter_unknown"


# --------------------------------------------------------------------------- T-TRE-RC-05 reversal


def test_T_TRE_RC_05_reversal_reopens_items_mirrors_entry_and_keeps_original(world: World, api: ApiClient) -> None:
    from ledger import periods
    from ledger.models import Period

    customer, inv1, inv2 = two_invoices(world)
    rc = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2))).json()
    assert api.post(f"receipts/{rc['id']}:post").status_code == 200
    with world.scope():
        original = TreasuryVoucher.objects.get(id=rc["id"])
    no_reason = api.post(f"receipts/{rc['id']}:reverse", {})
    assert no_reason.status_code == 422

    # A reversal dated in a closed period is rejected and changes nothing.
    def close_october(u: Any) -> None:
        period = Period.objects.get(company_id=world.company, start=dt.date(2026, 10, 1))
        periods.change_status(u, period, "CLOSED")

    world.run("admin", close_october)
    closed = api.post(
        f"receipts/{rc['id']}:reverse", {"reason": "Consignación rechazada", "effective_date": "2026-10-31"}
    )
    assert closed.status_code == 422 and closed.json()["code"] == "treasury_period_closed"
    assert ar_balance(world, inv1) == (D("400000.0000"), "PARTIAL")

    reversed_ = api.post(f"receipts/{rc['id']}:reverse", {"reason": "Consignación rechazada por el banco"})
    assert reversed_.status_code == 200, reversed_.content
    assert reversed_.json()["status"] == "REVERSED"
    assert reversed_.json()["number"] == "RC-000001"  # the original number stays used
    assert ar_balance(world, inv1) == (D("1000000.0000"), "OPEN")
    assert ar_balance(world, inv2) == (D("2000000.0000"), "OPEN")
    with world.scope():
        mirror = TreasuryVoucher.objects.get(reversal_of_id=rc["id"])
        refreshed = TreasuryVoucher.objects.get(id=rc["id"])
    assert mirror.number == "RC-000002" and mirror.status == "POSTED"
    assert refreshed.reversed_by_id == mirror.id
    # The original voucher is unchanged except its status (immutable), its entry is mirrored exactly.
    assert (refreshed.total, refreshed.posted_entry_id, refreshed.number) == (
        original.total,
        original.posted_entry_id,
        original.number,
    )
    original_lines = world.entry_lines(original.posted_entry_id)
    mirror_lines = world.entry_lines(mirror.posted_entry_id)
    assert mirror_lines == [(code, credit, debit) for code, debit, credit in original_lines]
    again = api.post(f"receipts/{rc['id']}:reverse", {"reason": "otra vez"})
    assert again.status_code == 409
    # the re-opened items can be collected again
    rc2 = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2))).json()
    assert api.post(f"receipts/{rc2['id']}:post").json()["number"] == "RC-000003"


# --------------------------------------------------------------------------- T-TRE-RC-06 approvals


def test_T_TRE_RC_06_threshold_routes_to_p07_and_creator_cannot_approve(world: World, api: ApiClient) -> None:
    from workflow.models import ApprovalRequest

    world.configure(rc_approval_threshold="1000000")
    world.approval_policies(["approver", "cashier2"])
    customer, inv1, inv2 = two_invoices(world)
    inv3 = world.ar_item(customer, "SETT-1003", "999999")
    small = api.post(
        "receipts",
        receipt_body(
            world, customer, [{"line_no": 1, "open_item_id": str(inv3.id), "applied_amount": money("999999")}]
        ),
    ).json()
    assert api.post(f"receipts/{small['id']}:post").status_code == 200  # below the threshold: no approval

    big = api.post("receipts", receipt_body(world, customer, rc01_lines(inv1, inv2))).json()
    blocked = api.post(f"receipts/{big['id']}:post")
    assert blocked.status_code == 422 and blocked.json()["code"] == "treasury_approval_required"
    submitted = api.post(f"receipts/{big['id']}:submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "SUBMITTED"
    with world.scope():
        req = ApprovalRequest.objects.get(document_id=big["id"])
        assert (req.state, req.policy.code, req.requested_by) == (
            "PENDING",
            "TES-RC",
            world.ctx("cashier").principal_id,
        )
    still = api.post(f"receipts/{big['id']}:post")
    assert still.status_code == 422 and still.json()["code"] == "treasury_approval_required"
    # SoD: the creator cannot approve, even holding the approval permission
    world.policy.grant(world.ctx("cashier").principal_id, "treasury.receipt.approve", "workflow.approval.approve")
    own = api.post(f"receipts/{big['id']}:approve")
    assert own.status_code == 403 and own.json()["code"] == "sod_violation"
    no_perm = api.as_("cashier2").post(f"receipts/{big['id']}:approve")
    assert no_perm.status_code == 403  # an assignee without treasury.receipt.approve is denied
    approved = api.as_("approver").post(f"receipts/{big['id']}:approve", {"comment": "ok"})
    assert approved.status_code == 200, approved.content
    assert approved.json()["status"] == "APPROVED"
    with world.scope():
        assert ApprovalRequest.objects.get(document_id=big["id"]).state == "APPROVED"
    posted = api.as_("cashier").post(f"receipts/{big['id']}:post")
    assert posted.status_code == 200 and posted.json()["status"] == "POSTED"


def test_T_TRE_RC_06_rejection_needs_reason_and_missing_policy_fails_closed(world: World, api: ApiClient) -> None:
    world.configure(rc_approval_threshold="0")
    customer, inv1, _ = two_invoices(world)
    rc = api.post(
        "receipts",
        receipt_body(world, customer, [{"line_no": 1, "open_item_id": str(inv1.id), "applied_amount": money("1000")}]),
    ).json()
    no_policy = api.post(f"receipts/{rc['id']}:submit")
    assert no_policy.status_code == 422 and no_policy.json()["code"] == "treasury_approval_required"
    world.approval_policies(["approver"])
    assert api.post(f"receipts/{rc['id']}:submit").json()["status"] == "SUBMITTED"
    no_reason = api.as_("approver").post(f"receipts/{rc['id']}:reject", {})
    assert no_reason.status_code == 422
    rejected = api.as_("approver").post(f"receipts/{rc['id']}:reject", {"reason": "Soporte ilegible"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert api.as_("cashier").post(f"receipts/{rc['id']}:post").status_code == 422


def test_T_TRE_RC_01_party_is_a_confidential_field_and_foreign_ids_are_not_found(world: World, api: ApiClient) -> None:
    customer, inv1, _ = two_invoices(world)
    rc = api.post(
        "receipts",
        receipt_body(world, customer, [{"line_no": 1, "open_item_id": str(inv1.id), "applied_amount": money("1000")}]),
    ).json()
    assert rc["party"]["id"] == str(customer.id)
    world.user("reader", ("treasury.receipt.read",), confidential=False)
    masked = api.as_("reader").get(f"receipts/{rc['id']}").json()
    assert "party" not in masked
    assert api.as_("outsider").get(f"receipts/{rc['id']}").status_code == 403
    assert api.as_("other_admin").get(f"receipts/{rc['id']}").status_code == 404
    listing = api.as_("cashier").get("receipts", status="DRAFT").json()
    assert [x["id"] for x in listing["items"]] == [rc["id"]]
