"""Receipts (recaudos), write-offs and returns (RMA) — shared/07 §1.

Receipt posted: Dr BANK (cash) / Dr WHT_RECEIVABLE (withholdings certified by the customer) / Cr AR at the
open items' historical COP; a foreign-currency difference goes to FX_REALIZED_GAIN/LOSS. Write-off: Dr
BAD_DEBT_EXPENSE / Cr AR after an approval (SoD: the requester never approves). Return: an approved RMA posts
a credit note with the goods back at the original issue cost.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.workflow import WorkflowService
from kernel.money import minor_units, q_amount, round_half_up
from kernel.uow import UnitOfWork
from sales.errors import InvalidStateTransition, TreasuryOverapplication
from sales.models import ArOpenItem, Invoice, Receipt, SalesNote, SalesReturn, WriteOff
from sales.party.service import get_customer
from sales.pricing import engine
from sales.services import ar
from sales.services import documents as D
from sales.services import posting as P
from sales.services.common import (
    ROLE_AR,
    ROLE_BAD_DEBT,
    ROLE_FX_GAIN,
    ROLE_FX_LOSS,
    ROLE_WHT_RECEIVABLE,
    audit,
    currency_in,
    date_in,
    decimal_in,
    lock,
    next_number,
    require,
    text_in,
    unknown_keys,
    uuid_req,
)

RECEIPT_FIELDS = (
    "party",
    "document_date",
    "currency",
    "fx_rate",
    "amount",
    "bank_role",
    "withholdings",
    "applications",
)
WRITE_OFF_POLICY = "sales.write_off"
RETURN_POLICY = "sales.return"


def _amount(raw: Any, field: str, currency: str) -> Decimal:
    if isinstance(raw, Mapping):
        if raw.get("currency") not in (None, currency):
            raise ValidationFailed(f"{field}.currency must be {currency}", field=field)
        raw = raw.get("amount")
    return round_half_up(decimal_in(raw, field, minimum=Decimal(0)), minor_units(currency))


def create_receipt(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Receipt:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.receipt.create", {"company_id": company_id})
    unknown_keys(data, RECEIPT_FIELDS)
    party = get_customer(uow, company_id, data.get("party"))
    currency = currency_in(data.get("currency"))
    day = date_in(data.get("document_date"), "document_date", clock.business_date())
    given = decimal_in(data["fx_rate"], "fx_rate", positive=True) if data.get("fx_rate") not in (None, "") else None
    whts = []
    for i, w in enumerate(data.get("withholdings") or []):
        if not isinstance(w, Mapping):
            raise ValidationFailed(f"withholdings[{i}] must be an object", field="withholdings")
        unknown_keys(w, ("tax_code", "amount", "certificate"), f" in withholdings[{i}]")
        code = text_in(w.get("tax_code"), "tax_code", 40)
        if not code:
            raise ValidationFailed(f"withholdings[{i}].tax_code is required", field="withholdings")
        whts.append(
            {
                "tax_code": code,
                "amount": f"{_amount(w.get('amount'), 'amount', currency):f}",
                "certificate": text_in(w.get("certificate"), "certificate", 80),
            }
        )
    apps = []
    for i, a in enumerate(data.get("applications") or []):
        if not isinstance(a, Mapping):
            raise ValidationFailed(f"applications[{i}] must be an object", field="applications")
        unknown_keys(a, ("open_item_id", "amount"), f" in applications[{i}]")
        apps.append(
            {
                "open_item_id": str(uuid_req(a.get("open_item_id"), "open_item_id")),
                "amount": f"{_amount(a.get('amount'), 'amount', currency):f}",
            }
        )
    receipt = Receipt.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "receipt", day),
        document_date=day,
        party=party,
        currency=currency,
        fx_rate=engine.document_fx(uow, company_id, currency, day, given),
        amount=_amount(data.get("amount"), "amount", currency),
        bank_role=text_in(data.get("bank_role") or "BANK", "bank_role", 40),
        withholdings=whts,
        applications=apps,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "sales.receipt.create", "sales.receipt", receipt.id, company_id, {"number": receipt.number})
    return receipt


def post_receipt(uow: UnitOfWork, receipt: Receipt) -> Receipt:
    ctx = uow.ctx
    require(ctx, "sales.receipt.post", receipt)
    receipt = lock(Receipt.objects.filter(tenant_id=ctx.tenant_id), receipt.id)
    if receipt.status != Receipt.Status.DRAFT:
        raise InvalidStateTransition(f"a {receipt.status} receipt cannot be posted", status_now=receipt.status)
    wht_total = sum((Decimal(w["amount"]) for w in receipt.withholdings), Decimal(0))
    applied = sum((Decimal(a["amount"]) for a in receipt.applications), Decimal(0))
    if not receipt.applications or applied != receipt.amount + wht_total:
        raise TreasuryOverapplication("applications must equal the cash received plus the certified withholdings")
    items = {
        str(i.id): i
        for i in ArOpenItem.objects.filter(
            tenant_id=ctx.tenant_id,
            company_id=receipt.company_id,
            id__in=[a["open_item_id"] for a in receipt.applications],
        )
    }
    for a in receipt.applications:
        item = items.get(a["open_item_id"])
        if item is None or item.party_id != receipt.party_id or item.currency != receipt.currency:
            raise ValidationFailed(
                "every application must be an open item of the customer in the receipt currency", field="applications"
            )
    source = DocumentRef("sales.receipt", receipt.id, receipt.number)
    done = ar.apply_amounts(
        uow,
        receipt.company_id,
        source,
        [(uuid.UUID(a["open_item_id"]), Decimal(a["amount"])) for a in receipt.applications],
        receipt.document_date,
    )
    fx, party = receipt.fx_rate, PartyRef(receipt.party_id, "customer")
    lines: list[PostingLine] = []
    debit = Decimal(0)
    if receipt.amount > 0:
        cash = q_amount(receipt.amount * fx)
        lines.append(PostingLine(receipt.bank_role, "DEBIT", cash, memo=receipt.number))
        debit += cash
    for w in receipt.withholdings:
        cop = q_amount(Decimal(w["amount"]) * fx)
        lines.append(
            PostingLine(ROLE_WHT_RECEIVABLE, "DEBIT", cop, tax_code=w["tax_code"], party=party, memo=receipt.number)
        )
        debit += cop
    credit = sum((cop for _, _, cop in done), Decimal(0))
    lines.append(PostingLine(ROLE_AR, "CREDIT", credit, party=party, memo=receipt.number))
    if debit != credit:
        diff = debit - credit
        role = ROLE_FX_GAIN if diff > 0 else ROLE_FX_LOSS
        lines.append(PostingLine(role, "CREDIT" if diff > 0 else "DEBIT", abs(diff), memo=receipt.number))
    entry = services.get(PostingService).post(
        uow,
        PostingRequest(receipt.company_id, receipt.document_date, source, "customer_receipt", tuple(lines), "RECEIPTS"),
    )
    receipt.status, receipt.posted, receipt.posted_at, receipt.journal_entry_id = (
        Receipt.Status.POSTED,
        True,
        clock.now(),
        entry.entry_id,
    )
    receipt.version += 1
    receipt.save()
    uow.emit(
        "sales.receipt.posted.v1",
        {
            "receipt_id": str(receipt.id),
            "party": {"id": str(receipt.party_id), "kind": "customer"},
            "amount": {"amount": f"{q_amount(receipt.amount):f}", "currency": receipt.currency},
        },
        company_id=receipt.company_id,
    )
    audit(
        uow,
        "sales.receipt.post",
        "sales.receipt",
        receipt.id,
        receipt.company_id,
        {"journal_entry": str(entry.entry_id)},
    )
    return receipt


# --------------------------------------------------------------------------- write-offs


def create_write_off(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> WriteOff:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.write_off.create", {"company_id": company_id})
    unknown_keys(data, ("open_item_id", "amount", "reason", "document_date"))
    item = ArOpenItem.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, id=uuid_req(data.get("open_item_id"), "open_item_id")
    ).first()
    if item is None:
        raise ValidationFailed("open_item_id is not an open item of the company", field="open_item_id")
    reason = text_in(data.get("reason"), "reason", 2000).strip()
    if not reason:
        raise ValidationFailed("a write-off needs a reason", field="reason", code="reason_required")
    amount = _amount(data.get("amount", f"{item.balance:f}"), "amount", item.currency)
    if amount <= 0 or amount > item.balance:
        raise TreasuryOverapplication("the write-off must be positive and not exceed the open balance")
    wo = WriteOff.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "write_off", clock.business_date()),
        document_date=date_in(data.get("document_date"), "document_date", clock.business_date()),
        open_item=item,
        party=item.party,
        amount=amount,
        reason=reason,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "sales.write_off.create", "sales.write_off", wo.id, company_id, {"number": wo.number})
    return wo


def _write_off_request(wo: WriteOff) -> PostingRequest:
    item = wo.open_item
    cop = item.balance_cop if wo.amount == item.balance else q_amount(wo.amount * item.fx_rate)
    party = PartyRef(wo.party_id, "customer")
    return PostingRequest(
        wo.company_id,
        wo.document_date,
        DocumentRef("sales.write_off", wo.id, wo.number),
        "write_off",
        (
            PostingLine(ROLE_BAD_DEBT, "DEBIT", cop, party=party, memo=wo.number),
            PostingLine(ROLE_AR, "CREDIT", cop, party=party, memo=wo.number),
        ),
        "GENERAL",
    )


def write_off_action(uow: UnitOfWork, wo: WriteOff, action: str) -> Any:
    ctx = uow.ctx
    if action == "simulate":
        require(ctx, "sales.write_off.read", wo)
        req = _write_off_request(wo)
        res = services.get(PostingService).simulate(uow, req)
        return {
            "documents": [{"type": "sales.write_off", "id": str(wo.id), "status": wo.status}],
            "journal_entries": [
                {
                    "lines": [
                        {"account_role": x.account_role, "side": x.side, "amount": f"{x.amount:f}"} for x in req.lines
                    ],
                    "total_debit_cop": f"{res.total_debit_cop:f}",
                    "total_credit_cop": f"{res.total_credit_cop:f}",
                }
            ],
        }
    if action != "post":
        raise ValidationFailed(f"unknown action {action}")
    require(ctx, "sales.write_off.post", wo)
    wo = lock(WriteOff.objects.filter(tenant_id=ctx.tenant_id), wo.id)
    if wo.status == WriteOff.Status.POSTED:
        raise InvalidStateTransition("the write-off is already posted", status_now=wo.status)
    wf = services.get(WorkflowService)
    state = wf.state(wo.approval_request_id) if wo.approval_request_id else None
    if state is None or state.state == "REJECTED":
        approval = wf.request_approval(
            uow, wo.company_id, DocumentRef("sales.write_off", wo.id, wo.number), WRITE_OFF_POLICY, wo.reason[:200]
        )
        wo.approval_request_id, wo.status = approval.id, WriteOff.Status.PENDING_APPROVAL
        wo.version += 1
        wo.save()
        return wo
    if state.state != "APPROVED":
        return wo  # still pending: nothing posts before the approval (C1-1)
    request = _write_off_request(wo)
    entry = services.get(PostingService).post(uow, request)
    ar.apply_amounts(uow, wo.company_id, request.source, [(wo.open_item_id, wo.amount)], wo.document_date)
    wo.status, wo.posted, wo.journal_entry_id = WriteOff.Status.POSTED, True, entry.entry_id
    wo.version += 1
    wo.save()
    audit(
        uow,
        "sales.write_off.post",
        "sales.write_off",
        wo.id,
        wo.company_id,
        {"journal_entry": str(entry.entry_id)},
        wo.reason,
    )
    return wo


# --------------------------------------------------------------------------- returns (RMA)


def create_return(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> SalesReturn:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "sales.return.create", {"company_id": company_id})
    unknown_keys(data, ("original", "invoice_id", "warehouse_id", "lines", "reason", "document_date"))
    inv = Invoice.objects.filter(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        id=uuid_req(data.get("original", data.get("invoice_id")), "original"),
    ).first()
    if inv is None or inv.status != Invoice.Status.POSTED:
        raise ValidationFailed("original must be a posted invoice of the company", field="original")
    ids = {str(x.id): x for x in inv.lines.all()}
    lines = []
    for i, row in enumerate(data.get("lines") or []):
        if not isinstance(row, Mapping) or str(row.get("invoice_line_id")) not in ids:
            raise ValidationFailed(f"lines[{i}] must reference a line of the invoice", field="lines")
        qty = decimal_in(row.get("quantity"), "quantity", positive=True)
        if qty > ids[str(row["invoice_line_id"])].quantity:
            raise ValidationFailed(f"lines[{i}] returns more than invoiced", field="lines")
        lines.append({"invoice_line_id": str(row["invoice_line_id"]), "quantity": f"{qty:f}"})
    if not lines:
        raise ValidationFailed("a return needs at least one line", field="lines")
    rma = SalesReturn.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "return", clock.business_date()),
        document_date=date_in(data.get("document_date"), "document_date", clock.business_date()),
        invoice=inv,
        warehouse_id=uuid_req(data.get("warehouse_id"), "warehouse_id"),
        lines=lines,
        reason=text_in(data.get("reason"), "reason", 2000),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "sales.return.create", "sales.return", rma.id, company_id, {"number": rma.number})
    return rma


def return_action(uow: UnitOfWork, rma: SalesReturn, action: str, reason: str) -> SalesReturn:
    ctx = uow.ctx
    rma = lock(SalesReturn.objects.filter(tenant_id=ctx.tenant_id), rma.id)
    wf = services.get(WorkflowService)
    S = SalesReturn.Status
    if action == "submit":
        require(ctx, "sales.return.update", rma)
        D.transition(rma, (S.DRAFT,), "submit")
        req = wf.request_approval(
            uow, rma.company_id, DocumentRef("sales.return", rma.id, rma.number), RETURN_POLICY, rma.reason[:200]
        )
        rma.status, rma.approval_request_id = S.SUBMITTED, req.id
    elif action == "approve":
        require(ctx, "sales.return.approve", rma)
        D.transition(rma, (S.SUBMITTED,), "approve")
        if rma.approval_request_id is None:
            raise InvalidStateTransition("the return has no approval request", status_now=rma.status)
        decided = wf.decide(uow, rma.approval_request_id, True, reason)
        if decided.state != "APPROVED":
            raise InvalidStateTransition("the approval was not granted", status_now=rma.status)
        rma.status = S.APPROVED
    elif action == "post":
        require(ctx, "sales.return.post", rma)
        D.transition(rma, (S.APPROVED,), "post")
        note = P.create_note(
            uow,
            rma.company_id,
            SalesNote.Kind.CREDIT,
            {
                "original": str(rma.invoice_id),
                "reason": "devolucion",
                "reason_text": rma.reason,
                "with_return": True,
                "warehouse_id": str(rma.warehouse_id),
                "document_date": rma.document_date.isoformat(),
                "lines": rma.lines,
            },
        )
        P.post_note(uow, note)
        rma.status, rma.credit_note_id = S.POSTED, note.id
    else:
        raise ValidationFailed(f"unknown action {action}")
    rma.version += 1
    rma.updated_by = ctx.principal_id
    rma.save()
    audit(uow, f"sales.return.{action}", "sales.return", rma.id, rma.company_id, {"status": rma.status}, reason)
    return rma
