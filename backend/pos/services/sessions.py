"""Cash sessions (turnos), cash movements and the Z report (shared/07 §1 POS cash session close).

* Open: one non-closed session per terminal (partial unique index); opening float ≥ 0.
* Expected per tender = opening float (cash) + Σ posted tenders − Σ posted refunds + pay-ins − pay-outs.
* Close (count by denomination or by tender; blind count hides the expected amounts until the close commits):
  cash over/short is posted in-line — short: Dr CASH_DIFFERENCE / Cr CASH_POS; over: Dr CASH_POS / Cr CASH_DIFFERENCE;
  a deposit to the safe/bank: Dr BANK_TRANSIT / Cr CASH_POS — plus the Z report and ``pos.session.closed.v1``.
* Cash movements (pay-in/pay-out with a reason) post Dr/Cr CASH_POS vs CASH_TRANSFER in the same unit of work.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum

from kernel import clock, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.money import q_amount
from kernel.uow import UnitOfWork
from pos.errors import SessionAlreadyOpen, SessionNotOpen
from pos.models import TENDER_TYPES, CashMovement, CashSession, PosReturn, Tender, Terminal, Ticket
from pos.services.common import (
    ROLE_BANK_TRANSIT,
    ROLE_CASH_DIFFERENCE,
    ROLE_CASH_POS,
    ROLE_CASH_TRANSFER,
    is_supervisor,
    next_number,
)
from sales.services.common import audit, decimal_in, lock, money_in, require, text_in, unknown_keys, uuid_req

SESSION_SOURCE = "pos.session"


def open_session(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> CashSession:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.session.create", {"company_id": company_id})
    nested = data.get("parameters")
    params: Mapping[str, Any] = nested if isinstance(nested, Mapping) else data
    unknown_keys(params, ("terminal_id", "opening_cash", "counted_cash"))
    term = (
        Terminal.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, id=uuid_req(params.get("terminal_id"), "terminal_id")
        )
        .select_related("config")
        .first()
    )
    if term is None:
        raise ValidationFailed("terminal_id is not a terminal of the company", field="terminal_id")
    if term.status != Terminal.Status.ACTIVE or not term.config.active:
        raise ValidationFailed("the terminal or its POS configuration is not active", field="terminal_id")
    if CashSession.objects.filter(terminal=term).exclude(status=CashSession.Status.CLOSED).exists():
        raise SessionAlreadyOpen(f"terminal {term.code} already has an open session")
    opening = q_amount(money_in(params.get("opening_cash", "0"), "opening_cash", "COP"))
    now = clock.now()
    day = clock.business_date()
    try:
        with transaction.atomic():
            session = CashSession.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                number=next_number(uow, company_id, "session", day),
                terminal=term,
                config=term.config,
                cashier_id=ctx.principal_id,
                opened_at=now,
                business_date=day,
                opening_cash=opening,
                blind=term.config.blind_count,
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
            )
    except IntegrityError as exc:  # concurrent open on the same terminal (partial unique index)
        raise SessionAlreadyOpen(f"terminal {term.code} already has an open session") from exc
    audit(uow, "pos.session.open", SESSION_SOURCE, session.id, company_id, {"terminal": term.code})
    return session


def _sum(qs: Any, field: str = "amount") -> Decimal:
    return Decimal(qs.aggregate(s=Sum(field))["s"] or 0)


def expected(session: CashSession) -> dict[str, Decimal]:
    """Expected drawer content per tender from the posted documents of the session."""
    out = {t: Decimal(0) for t in TENDER_TYPES}
    out["CASH"] = Decimal(session.opening_cash)
    tenders = Tender.objects.filter(ticket__session=session, ticket__status=Ticket.Status.POSTED)
    for row in tenders.values("tender_type").annotate(s=Sum("amount")):
        out[row["tender_type"]] = out.get(row["tender_type"], Decimal(0)) + Decimal(row["s"])
    refunds = PosReturn.objects.filter(session=session, status=PosReturn.Status.POSTED)
    for row in refunds.values("refund_tender").annotate(s=Sum("total")):
        out[row["refund_tender"]] = out.get(row["refund_tender"], Decimal(0)) - Decimal(row["s"])
    moves = CashMovement.objects.filter(session=session, status="POSTED")
    out["CASH"] += _sum(moves.filter(movement_type="IN")) - _sum(moves.filter(movement_type="OUT"))
    return {k: q_amount(v) for k, v in out.items()}


def _counted(params: Mapping[str, Any]) -> tuple[dict[str, Decimal], dict[str, int]]:
    counted: dict[str, Decimal] = {}
    denominations: dict[str, int] = {}
    raw_den = params.get("denominations")
    if raw_den not in (None, {}):
        if not isinstance(raw_den, Mapping):
            raise ValidationFailed("denominations must be an object {face value: count}", field="denominations")
        total = Decimal(0)
        for face, count in raw_den.items():
            value = decimal_in(face, "denominations", positive=True)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValidationFailed("denomination counts must be integers ≥ 0", field="denominations")
            denominations[f"{value:f}"] = count
            total += value * count
        counted["CASH"] = q_amount(total)
    raw = params.get("counted")
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise ValidationFailed("counted must be an object {tender: amount}", field="counted")
        for tender, amount in raw.items():
            if tender not in TENDER_TYPES:
                raise ValidationFailed(f"counted: unknown tender {tender}", field="counted")
            value = q_amount(money_in(amount, f"counted.{tender}", "COP"))
            if tender == "CASH" and "CASH" in counted and counted["CASH"] != value:
                raise ValidationFailed("counted.CASH differs from the denomination count", field="counted")
            counted[tender] = value
    if "counted_cash" in params:
        counted["CASH"] = q_amount(money_in(params["counted_cash"], "counted_cash", "COP"))
    if "CASH" not in counted:
        raise ValidationFailed(
            "the cash count is required (counted.CASH, counted_cash or denominations)", field="counted"
        )
    return counted, denominations


def z_report(session: CashSession) -> dict[str, Any]:
    tickets = Ticket.objects.filter(session=session, status=Ticket.Status.POSTED)
    agg = tickets.aggregate(n=Count("id"), sub=Sum("subtotal"), tax=Sum("tax_total"), tot=Sum("total"))
    numbers = sorted(n for n in tickets.values_list("number", flat=True) if n)
    by_tender = {
        r["tender_type"]: f"{Decimal(r['s']):f}"
        for r in Tender.objects.filter(ticket__in=tickets).values("tender_type").annotate(s=Sum("amount"))
    }
    returns = PosReturn.objects.filter(session=session, status=PosReturn.Status.POSTED)
    moves = CashMovement.objects.filter(session=session, status="POSTED")
    exp = expected(session)
    return {
        "report": "pos.z",
        "session_id": str(session.id),
        "session_number": session.number,
        "terminal": session.terminal.code,
        "cashier_id": str(session.cashier_id),
        "business_date": session.business_date.isoformat(),
        "opened_at": session.opened_at.isoformat(),
        "closed_at": session.closed_at.isoformat() if session.closed_at else None,
        "tickets": int(agg["n"] or 0),
        "first_number": numbers[0] if numbers else None,
        "last_number": numbers[-1] if numbers else None,
        "subtotal": f"{q_amount(Decimal(agg['sub'] or 0)):f}",
        "tax_total": f"{q_amount(Decimal(agg['tax'] or 0)):f}",
        "total": f"{q_amount(Decimal(agg['tot'] or 0)):f}",
        "by_tender": by_tender,
        "returns": {"count": returns.count(), "total": f"{q_amount(_sum(returns, 'total')):f}"},
        "cash_in": f"{q_amount(_sum(moves.filter(movement_type='IN'))):f}",
        "cash_out": f"{q_amount(_sum(moves.filter(movement_type='OUT'))):f}",
        "opening_cash": f"{session.opening_cash:f}",
        "expected": {k: f"{v:f}" for k, v in exp.items() if v or k == "CASH"},
        "counted": dict(session.counted),
        "difference": f"{session.difference:f}" if session.difference is not None else None,
        "deposit_amount": f"{session.deposit_amount:f}",
        "currency": "COP",
    }


def close_session(uow: UnitOfWork, session: CashSession, reason: str, params: Mapping[str, Any]) -> CashSession:
    ctx = uow.ctx
    require(ctx, "pos.session.update", session)
    unknown_keys(params, ("counted", "counted_cash", "denominations", "deposit_amount"))
    session = lock(CashSession.objects.filter(tenant_id=ctx.tenant_id).select_related("terminal", "config"), session.id)
    if session.cashier_id != ctx.principal_id and not is_supervisor(ctx):
        raise NotFound("session not found")
    if session.status != CashSession.Status.OPEN:
        raise SessionNotOpen(f"session {session.number} is {session.status}", status_now=session.status)
    if Ticket.objects.filter(session=session, status=Ticket.Status.DRAFT).exists():
        raise ValidationFailed("checkout or delete the parked tickets before closing the session")
    counted, denominations = _counted(params)
    exp = expected(session)
    diff = q_amount(counted["CASH"] - exp["CASH"])
    deposit = q_amount(money_in(params.get("deposit_amount", "0"), "deposit_amount", "COP"))
    if deposit > counted["CASH"]:
        raise ValidationFailed("the deposit exceeds the counted cash", field="deposit_amount")
    lines: list[PostingLine] = []
    memo = f"{session.number}"
    if diff < 0:
        lines += [
            PostingLine(ROLE_CASH_DIFFERENCE, "DEBIT", -diff, memo=f"{memo} faltante"),
            PostingLine(ROLE_CASH_POS, "CREDIT", -diff, memo=f"{memo} faltante"),
        ]
    elif diff > 0:
        lines += [
            PostingLine(ROLE_CASH_POS, "DEBIT", diff, memo=f"{memo} sobrante"),
            PostingLine(ROLE_CASH_DIFFERENCE, "CREDIT", diff, memo=f"{memo} sobrante"),
        ]
    if deposit > 0:
        lines += [
            PostingLine(ROLE_BANK_TRANSIT, "DEBIT", deposit, memo=f"{memo} consignación"),
            PostingLine(ROLE_CASH_POS, "CREDIT", deposit, memo=f"{memo} consignación"),
        ]
    entries: list[str] = []
    if lines:
        entry = services.get(PostingService).post(
            uow,
            PostingRequest(
                company_id=session.company_id,
                posting_date=clock.business_date(),
                source=DocumentRef(SESSION_SOURCE, session.id, session.number),
                purpose="pos_session_close",
                lines=tuple(lines),
                journal_code="SALES",
                branch_id=session.config.branch_id,
            ),
        )
        entries.append(str(entry.entry_id))
    session.status = CashSession.Status.CLOSED
    session.closed_at = clock.now()
    session.expected = {k: f"{v:f}" for k, v in exp.items()}
    session.counted = {k: f"{v:f}" for k, v in counted.items()}
    session.denominations = denominations
    session.expected_cash, session.counted_cash, session.difference = exp["CASH"], counted["CASH"], diff
    session.deposit_amount = deposit
    session.journal_entry_ids = entries
    session.version += 1
    session.updated_by = ctx.principal_id
    session.save()
    session.z_report = z_report(session)
    session.save(update_fields=["z_report"])
    uow.emit(
        "pos.session.closed.v1",
        {"session_id": str(session.id), "difference": {"amount": f"{diff:f}", "currency": "COP"}},
        company_id=session.company_id,
    )
    audit(uow, "pos.session.close", SESSION_SOURCE, session.id, session.company_id, {"difference": f"{diff:f}"}, reason)
    return session


# --------------------------------------------------------------------------- cash movements


def create_movement(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> CashMovement:
    from pos.services.checkout import writable_session

    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.cash_movement.create", {"company_id": company_id})
    unknown_keys(data, ("session_id", "movement_type", "amount", "reason"))
    session = writable_session(uow, company_id, data.get("session_id"))
    kind = str(data.get("movement_type") or "")
    if kind not in CashMovement.Type.values:
        raise ValidationFailed("movement_type must be IN or OUT", field="movement_type")
    amount = q_amount(money_in(data.get("amount"), "amount", "COP"))
    if amount <= 0:
        raise ValidationFailed("amount must be positive", field="amount")
    reason = text_in(data.get("reason"), "reason", 255).strip()
    if not reason:
        raise ValidationFailed("a reason is required", field="reason")
    if kind == "OUT" and amount > expected(session)["CASH"]:
        raise ValidationFailed("the pay-out exceeds the cash in the drawer", field="amount")
    day = clock.business_date()
    move = CashMovement(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        session=session,
        number=next_number(uow, company_id, "movement", day),
        movement_type=kind,
        amount=amount,
        reason=reason,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    debit, credit = (ROLE_CASH_POS, ROLE_CASH_TRANSFER) if kind == "IN" else (ROLE_CASH_TRANSFER, ROLE_CASH_POS)
    entry = services.get(PostingService).post(
        uow,
        PostingRequest(
            company_id=company_id,
            posting_date=day,
            source=DocumentRef("pos.cash_movement", move.id, move.number),
            purpose="pos_cash_movement",
            lines=(
                PostingLine(debit, "DEBIT", amount, memo=f"{move.number} {reason[:60]}"),
                PostingLine(credit, "CREDIT", amount, memo=f"{move.number} {reason[:60]}"),
            ),
            journal_code="SALES",
            branch_id=session.config.branch_id,
        ),
    )
    move.journal_entry_id = entry.entry_id
    move.posted = True
    move.save()
    audit(
        uow, "pos.cash_movement.create", "pos.cash_movement", move.id, company_id, {"type": kind, "number": move.number}
    )
    return move
