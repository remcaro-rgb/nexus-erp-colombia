"""POS returns referencing the original ticket (T-P12-08).

* Create (cashier, ``pos.return.create``): DRAFT with lines ``{line_no (of the original ticket), quantity}``; the
  quantity never exceeds sold − already returned (``pos_return_exceeds_sold``).
* Post (supervisor, ``pos.return.post``; denied to ``cajero_pos``): one unit of work — internal number, stock
  received back into the POS bin at the ORIGINAL issue cost (presentation lines restore packaged stock),
  Dr REVENUE / Dr VAT_PAYABLE / Cr refund tender (CASH_POS…) and Dr INVENTORY / Cr COGS at that original cost,
  with the POS branch. The refund is paid from the session's drawer (it enters the expected cash).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from iam.branches.services import assert_postable_branch
from kernel import clock, services
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine, StockService
from kernel.money import q_amount, q_qty, round_half_up
from kernel.uow import UnitOfWork
from pos.errors import ReturnExceedsSold
from pos.models import PosReturn, ReturnLine, Ticket, TicketLine
from pos.services.checkout import writable_session
from pos.services.common import TENDER_ROLES, next_number
from pos.services.sessions import expected
from sales.errors import InvalidStateTransition
from sales.services.common import (
    ROLE_COGS,
    ROLE_INVENTORY,
    ROLE_REVENUE,
    ROLE_VAT_PAYABLE,
    audit,
    lock,
    qty_in,
    require,
    text_in,
    unknown_keys,
    uuid_req,
)

FIELDS = (
    "original",
    "session_id",
    "lines",
    "refund_tender",
    "reason",
    "notes",
    "document_date",
    "currency",
    "party",
    "posting_date",
)
SOURCE = "pos.return"


@dataclass
class PlannedLine:
    source: TicketLine
    quantity: Decimal
    base_qty: Decimal
    amount: Decimal
    tax_amount: Decimal
    cost_value: Decimal


def _returned(line: TicketLine, exclude: uuid.UUID | None = None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    qs = ReturnLine.objects.filter(ticket_line=line, pos_return__status=PosReturn.Status.POSTED)
    if exclude is not None:
        qs = qs.exclude(pos_return_id=exclude)
    agg = qs.aggregate(q=Sum("quantity"), a=Sum("amount"), t=Sum("tax_amount"), c=Sum("cost_value"))
    return tuple(Decimal(agg[k] or 0) for k in ("q", "a", "t", "c"))  # type: ignore[return-value]  # 4-tuple of Decimal


def _plan(ticket: Ticket, raw: Any, exclude: uuid.UUID | None = None) -> list[PlannedLine]:
    lines = {x.line_no: x for x in ticket.lines.all()}
    if raw in (None, []):
        raw = [
            {"line_no": n, "quantity": f"{x.quantity - _returned(x, exclude)[0]:f}"} for n, x in sorted(lines.items())
        ]
        raw = [r for r in raw if Decimal(r["quantity"]) > 0]
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("lines must list the ticket lines to return", field="lines")
    out: list[PlannedLine] = []
    seen: set[int] = set()
    for i, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        unknown_keys(
            row,
            (
                "line_no",
                "quantity",
                "item_id",
                "description",
                "unit_price",
                "tax_code",
                "amount",
                "account_code",
                "dimensions",
            ),
            f" in lines[{i}]",
        )
        src = lines.get(row.get("line_no"))  # type: ignore[arg-type]  # JSON int
        if src is None or src.line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no is not a line of the original ticket", field="lines")
        seen.add(src.line_no)
        qty, _ = qty_in(row.get("quantity"), f"lines[{i}].quantity")
        done_q, done_a, done_t, done_c = _returned(src, exclude)
        left = Decimal(src.quantity) - done_q
        if qty > left:
            raise ReturnExceedsSold(f"line {src.line_no}: {qty:f} > {left:f} still returnable", field="lines")
        if src.presentation_id is not None and qty != round_half_up(qty, 0):
            raise ValidationFailed(
                f"lines[{i}]: packages are returned whole", field="lines", code="uom_precision_violation"
            )
        if qty == left:  # last units: the remainder (no rounding residue left on the ticket)
            amount = Decimal(src.amount) - done_a
            tax = Decimal(src.tax_amount) - done_t
            cost = Decimal(src.cost_value) - done_c
        else:
            ratio = qty / Decimal(src.quantity)
            amount = q_amount(round_half_up(Decimal(src.amount) * ratio, 2))
            tax = q_amount(round_half_up(Decimal(src.tax_amount) * ratio, 2))
            cost = q_amount(round_half_up(Decimal(src.cost_value) * ratio, 2))
        out.append(PlannedLine(src, qty, q_qty(qty * Decimal(src.factor)), amount, tax, cost))
    return out


def _original(uow: UnitOfWork, company_id: uuid.UUID, raw: Any) -> Ticket:
    if isinstance(raw, Mapping):
        if raw.get("type") not in (None, "pos.ticket"):
            raise ValidationFailed("original must reference a POS ticket", field="original")
        raw = raw.get("id")
    tid = uuid_req(raw, "original")
    ticket = Ticket.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=tid).first()
    if ticket is None or ticket.status != Ticket.Status.POSTED:
        raise ValidationFailed("original must be a posted POS ticket of the company", field="original")
    return ticket


def create_return(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PosReturn:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.return.create", {"company_id": company_id})
    unknown_keys(data, FIELDS)
    ticket = _original(uow, company_id, data.get("original"))
    session_id = data.get("session_id")
    if session_id in (None, ""):
        session_id = ticket.session_id
    session = writable_session(uow, company_id, session_id)
    if not session.config.allow_returns:
        raise ValidationFailed("returns are not allowed at this POS", field="session_id")
    tender = str(data.get("refund_tender") or "CASH")
    if tender not in session.config.allowed_tenders or tender not in TENDER_ROLES:
        raise ValidationFailed(f"refund_tender {tender} is not accepted at this POS", field="refund_tender")
    plan = _plan(ticket, data.get("lines"))
    day = clock.business_date()
    ret = PosReturn.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "return", day),
        session=session,
        original=ticket,
        document_date=day,
        refund_tender=tender,
        reason=text_in(data.get("reason"), "reason", 255),
        notes=text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _write_lines(ret, plan)
    audit(uow, "pos.return.create", SOURCE, ret.id, company_id, {"original": ticket.number})
    return ret


def _write_lines(ret: PosReturn, plan: list[PlannedLine]) -> None:
    ret.lines.all().delete()
    for n, p in enumerate(plan, start=1):
        ReturnLine.objects.create(
            tenant_id=ret.tenant_id,
            company_id=ret.company_id,
            pos_return=ret,
            line_no=n,
            ticket_line=p.source,
            quantity=p.quantity,
            base_qty=p.base_qty,
            amount=p.amount,
            tax_amount=p.tax_amount,
            cost_value=p.cost_value,
        )
    ret.subtotal = sum((p.amount for p in plan), Decimal(0))
    ret.tax_total = sum((p.tax_amount for p in plan), Decimal(0))
    ret.total = ret.subtotal + ret.tax_total
    ret.cost_total = sum((p.cost_value for p in plan), Decimal(0))
    ret.save(update_fields=["subtotal", "tax_total", "total", "cost_total"])


def update_return(uow: UnitOfWork, ret: PosReturn, data: Mapping[str, Any]) -> PosReturn:
    require(uow.ctx, "pos.return.update", ret)
    unknown_keys(data, FIELDS)
    ret = lock(PosReturn.objects.filter(tenant_id=uow.ctx.tenant_id), ret.id)
    if ret.status != PosReturn.Status.DRAFT:
        raise InvalidStateTransition("a posted return is immutable", status_now=ret.status)
    if "reason" in data:
        ret.reason = text_in(data["reason"], "reason", 255)
    if "notes" in data:
        ret.notes = text_in(data["notes"], "notes")
    ret.version += 1
    ret.save()
    if "lines" in data:
        _write_lines(ret, _plan(ret.original, data["lines"], exclude=ret.id))
    return ret


def delete_return(uow: UnitOfWork, ret: PosReturn) -> None:
    require(uow.ctx, "pos.return.delete", ret)
    if ret.status != PosReturn.Status.DRAFT:
        raise InvalidStateTransition("only a DRAFT return can be deleted", status_now=ret.status)
    ret.lines.all().delete()
    ret.delete()


def _request(ret: PosReturn, plan: list[PlannedLine], ticket: Ticket) -> PostingRequest:
    memo = ret.number
    party = PartyRef(ticket.party_id, "customer") if ticket.party_id else None
    lines: list[PostingLine] = []
    for p in plan:
        lines.append(
            PostingLine(
                ROLE_REVENUE,
                "DEBIT",
                p.amount,
                party=party,
                item_id=p.source.item_id,
                memo=f"{memo} L{p.source.line_no}",
            )
        )
    taxes: dict[str, Decimal] = {}
    for p in plan:
        if p.tax_amount and p.source.tax_code:
            taxes[p.source.tax_code] = taxes.get(p.source.tax_code, Decimal(0)) + p.tax_amount
    for code, amount in sorted(taxes.items()):
        lines.append(PostingLine(ROLE_VAT_PAYABLE, "DEBIT", amount, tax_code=code, memo=memo))
    total = sum((p.amount + p.tax_amount for p in plan), Decimal(0))
    lines.append(PostingLine(TENDER_ROLES[ret.refund_tender], "CREDIT", total, party=party, memo=memo))
    for p in plan:
        if p.cost_value > 0:
            lines.append(
                PostingLine(
                    ROLE_INVENTORY,
                    "DEBIT",
                    p.cost_value,
                    item_id=p.source.item_id,
                    warehouse_id=ticket.warehouse_id,
                    memo=memo,
                )
            )
            lines.append(PostingLine(ROLE_COGS, "CREDIT", p.cost_value, item_id=p.source.item_id, memo=memo))
    return PostingRequest(
        company_id=ret.company_id,
        posting_date=ret.document_date,
        source=DocumentRef(SOURCE, ret.id, memo),
        purpose="pos_return",
        lines=tuple(lines),
        journal_code="SALES",
        branch_id=ticket.branch_id,
    )


def simulate_return(uow: UnitOfWork, ret: PosReturn) -> dict[str, Any]:
    require(uow.ctx, "pos.return.read", ret)
    plan = _plan(
        ret.original,
        [
            {"line_no": x.ticket_line.line_no, "quantity": f"{x.quantity:f}"}
            for x in ret.lines.select_related("ticket_line")
        ],
        exclude=ret.id,
    )
    req = _request(ret, plan, ret.original)
    res = services.get(PostingService).simulate(uow, req)
    return {
        "persisted": False,
        "documents": [{"type": SOURCE, "id": str(ret.id), "status": ret.status}],
        "journal_entries": [
            {
                "lines": [
                    {"account_role": x.account_role, "side": x.side, "amount": f"{x.amount:f}", "memo": x.memo}
                    for x in req.lines
                ],
                "total_debit_cop": f"{res.total_debit_cop:f}",
                "total_credit_cop": f"{res.total_credit_cop:f}",
            }
        ],
        "stock_moves": [{"line_no": p.source.line_no, "base_qty": f"{p.base_qty:f}"} for p in plan],
        "totals": {"total": f"{sum((p.amount + p.tax_amount for p in plan), Decimal(0)):f}", "currency": "COP"},
        "warnings": [],
    }


def post_return(uow: UnitOfWork, ret: PosReturn) -> PosReturn:
    ctx = uow.ctx
    require(ctx, "pos.return.post", ret)
    ret = lock(PosReturn.objects.filter(tenant_id=ctx.tenant_id), ret.id)
    if ret.status != PosReturn.Status.DRAFT:
        raise InvalidStateTransition(f"a {ret.status} return cannot be posted", status_now=ret.status)
    ticket = lock(Ticket.objects.filter(tenant_id=ctx.tenant_id), ret.original_id)  # serializes returns of a ticket
    session = writable_session(uow, ret.company_id, ret.session_id)
    assert_postable_branch(ctx, ret.company_id, ticket.branch_id)
    rows = list(ret.lines.select_related("ticket_line").order_by("line_no"))
    plan = _plan(
        ticket, [{"line_no": x.ticket_line.line_no, "quantity": f"{x.quantity:f}"} for x in rows], exclude=ret.id
    )
    total = sum((p.amount + p.tax_amount for p in plan), Decimal(0))
    if ret.refund_tender == "CASH" and total > expected(session)["CASH"]:
        raise ValidationFailed("the cash refund exceeds the cash in the drawer", field="refund_tender")
    # stock back at the original issue cost, into the bin the sale came from
    stock = [
        StockLine(
            str(p.source.line_no),
            p.source.item_id,
            ticket.warehouse_id,
            p.base_qty,
            location_id=session.config.location_id,
            presentation_id=p.source.presentation_id,
            unit_cost=Decimal(p.source.unit_cost),
        )
        for p in plan
        if p.cost_value > 0 or Decimal(p.source.unit_cost) > 0
    ]
    source = DocumentRef(SOURCE, ret.id, ret.number)
    if stock:
        services.get(StockService).receive(uow, ret.company_id, source, stock, ret.document_date)
    entry = services.get(PostingService).post(uow, _request(ret, plan, ticket))
    _write_lines(ret, plan)
    for line in ret.lines.all():
        line.posted = True
        line.save(update_fields=["posted"])
    ret.status, ret.posted = PosReturn.Status.POSTED, True
    ret.journal_entry_id = entry.entry_id
    ret.posted_at, ret.posted_by = clock.now(), ctx.principal_id
    ret.version += 1
    ret.save()
    audit(
        uow,
        "pos.return.post",
        SOURCE,
        ret.id,
        ret.company_id,
        {"original": ticket.number, "journal_entry": str(entry.entry_id)},
    )
    return ret
