"""``POST /pos/tickets:checkout`` — the ONLY way a POS sale becomes final (ADR-015, C1-1, C1-2, C1-12).

One unit of work: recompute prices/taxes from current data (ADR-015 reconnect rule: a difference from the
client draft raises ``pos_price_changed`` and needs the cashier's confirmation), validate tenders, allocate the DEE
POS number from the DIAN range (``kernel.sequences``), issue stock (presentation-aware, from the POS bin), post the
balanced GL entry (Dr tenders / Cr REVENUE / Cr VAT_PAYABLE; Dr COGS / Cr INVENTORY) with the POS branch, write the
ticket, lines and tenders, audit, and the outbox event ``pos.ticket.checked_out.v1`` for the DEE transmission (P15).
Any failure rolls everything back: no number, no move, no journal entry, no event. The client UUID is the
Idempotency-Key (the HTTP layer replays the stored response); a second checkout of an already posted client UUID
returns that ticket.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from iam.branches.services import assert_postable_branch
from kernel import clock, flags, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine, StockService
from kernel.money import q_amount, q_unit_cost
from kernel.uow import UnitOfWork
from pos.errors import OfflineFiscalSellingRefused, PriceChanged, SessionNotOpen, TenderMismatch, TenderNotAllowed
from pos.models import CashSession, InvoiceRequest, Tender, Ticket, TicketLine
from pos.services import pricing
from pos.services.common import TENDER_ROLES, fiscal_number, is_supervisor
from sales.errors import InvalidStateTransition
from sales.kits import _values_by_ref, components, is_stocked, item_is_kit
from sales.models import Party
from sales.services.common import (
    ROLE_COGS,
    ROLE_INVENTORY,
    ROLE_REVENUE,
    ROLE_VAT_PAYABLE,
    audit,
    decimal_in,
    lock,
    require,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)

CHECKOUT_FIELDS = (
    "client_uuid",
    "session_id",
    "ticket_id",
    "lines",
    "payments",
    "party",
    "party_id",
    "expected_total",
    "notes",
    "offline",
    "currency",
    "document_date",
)
SOURCE = "pos.ticket"


@dataclass(frozen=True)
class TenderIn:
    tender_type: str
    amount: Decimal
    tendered: Decimal
    reference: str


def writable_session(
    uow: UnitOfWork, company_id: uuid.UUID, session_id: Any, *, for_update: bool = True
) -> CashSession:
    """The cash session, OPEN, operated by the caller (supervisors may act on any session of the company)."""
    ctx = uow.ctx
    qs = CashSession.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).select_related("config", "terminal")
    sid = uuid_req(session_id, "session_id")
    session = lock(qs, sid) if for_update else qs.filter(id=sid).first()
    if session is None or (session.cashier_id != ctx.principal_id and not is_supervisor(ctx)):
        raise NotFound("session not found")
    if session.status != CashSession.Status.OPEN:
        raise SessionNotOpen(f"session {session.number} is {session.status}", status_now=session.status)
    return session


def party_of(uow: UnitOfWork, company_id: uuid.UUID, raw: Any) -> Party | None:
    pid = uuid_in(raw, "party", required=False)
    if pid is None:
        return None
    party = Party.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=pid).first()
    if party is None or party.status != Party.Status.ACTIVE:
        raise ValidationFailed("party is not an active customer of the company", field="party")
    return party


def parse_tenders(allowed: list[str], raw: Any, total: Decimal) -> tuple[list[TenderIn], Decimal]:
    """Mixed tenders → (tenders, change). Cash may exceed the rest (change); other tenders settle exactly."""
    if isinstance(raw, Mapping):
        raw = raw.get("items", raw.get("tenders"))
    if not isinstance(raw, list) or not raw:
        raise TenderMismatch("payments must list at least one tender", field="payments")
    rows: list[tuple[str, Decimal, str]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValidationFailed(f"payments[{i}] must be an object", field="payments")
        unknown_keys(row, ("type", "tender_type", "amount", "tendered", "reference"), f" in payments[{i}]")
        kind = str(row.get("type") or row.get("tender_type") or "")
        if kind not in allowed:
            raise TenderNotAllowed(f"payments[{i}]: {kind or '∅'} is not accepted at this POS", field="payments")
        amount = row.get("tendered") if kind == "CASH" and row.get("tendered") not in (None, "") else row.get("amount")
        if isinstance(amount, Mapping):
            amount = amount.get("amount")
        value = q_amount(decimal_in(amount, f"payments[{i}].amount", positive=True))
        ref = text_in(row.get("reference"), "reference", 80).strip()
        if kind == "CARD" and not ref:
            raise ValidationFailed(
                f"payments[{i}]: a card payment needs the external terminal reference", field="payments"
            )
        rows.append((kind, value, ref))
    non_cash = sum((v for k, v, _ in rows if k != "CASH"), Decimal(0))
    cash = sum((v for k, v, _ in rows if k == "CASH"), Decimal(0))
    if non_cash > total:
        raise TenderMismatch("non-cash tenders exceed the ticket total", field="payments")
    due_cash = total - non_cash
    if cash < due_cash or (cash == 0 and non_cash != total):
        raise TenderMismatch(f"tenders settle {non_cash + cash:f} of {total:f}", field="payments")
    change = cash - due_cash
    out: list[TenderIn] = []
    left_change = change
    for kind, value, ref in rows:
        if kind == "CASH":
            applied = value - min(value, left_change)
            left_change -= value - applied
            if applied > 0:
                out.append(TenderIn(kind, applied, value, ref))
        else:
            out.append(TenderIn(kind, value, value, ref))
    return out, change


def _issue(
    uow: UnitOfWork,
    ticket: Ticket,
    cfg_location: uuid.UUID | None,
    source: DocumentRef,
    lines: list[pricing.PricedLine],
) -> dict[int, Decimal]:
    """ONE stock issue for the ticket (presentation lines decrement packaged + canonical stock, D-013)."""
    company = ticket.company_id
    stock: list[StockLine] = []
    owner: dict[str, int] = {}
    for x in lines:
        if not is_stocked(company, x.item.id):
            continue
        ref = str(x.line_no)
        if item_is_kit(company, x.item.id):
            for n, part in enumerate(
                sorted(components(company, x.item.id, x.base_qty, ticket.document_date), key=lambda p: p.item_id), 1
            ):
                sub = f"{ref}.k{n}"
                stock.append(StockLine(sub, part.item_id, ticket.warehouse_id, part.quantity, location_id=cfg_location))
                owner[sub] = x.line_no
            continue
        stock.append(
            StockLine(
                ref,
                x.item.id,
                ticket.warehouse_id,
                x.base_qty,
                location_id=cfg_location,
                presentation_id=x.presentation.id if x.presentation else None,
            )
        )
        owner[ref] = x.line_no
    if not stock:
        return {}
    res = services.get(StockService).issue(uow, company, source, stock, ticket.document_date)
    values = _values_by_ref(res.move_ids)
    if values is None:  # stock double without move rows: allocate by quantity
        total_qty = sum((s.quantity for s in stock), Decimal(0))
        values = {s.line_ref: res.total_value * s.quantity / total_qty for s in stock} if total_qty else {}
    out: dict[int, Decimal] = {}
    for ref, line_no in owner.items():
        out[line_no] = out.get(line_no, Decimal(0)) + values.get(ref, Decimal(0))
    return {k: q_amount(v) for k, v in out.items()}


def _posting(
    ticket: Ticket, cart: pricing.PricedCart, tenders: list[TenderIn], cogs: dict[int, Decimal]
) -> PostingRequest:
    memo = ticket.number or ""
    party = PartyRef(ticket.party_id, "customer") if ticket.party_id else None
    lines: list[PostingLine] = []
    by_role: dict[str, Decimal] = {}
    for t in tenders:
        role = TENDER_ROLES[t.tender_type]
        by_role[role] = by_role.get(role, Decimal(0)) + t.amount
    for role, amount in sorted(by_role.items()):
        lines.append(PostingLine(role, "DEBIT", amount, party=party, memo=memo))
    for x in cart.lines:
        lines.append(
            PostingLine(ROLE_REVENUE, "CREDIT", x.amount, party=party, item_id=x.item.id, memo=f"{memo} L{x.line_no}")
        )
    for tax in cart.taxes.lines:
        if not tax.withholding and tax.amount:
            lines.append(PostingLine(ROLE_VAT_PAYABLE, "CREDIT", tax.amount, tax_code=tax.tax_code, memo=memo))
    for x in cart.lines:
        value = cogs.get(x.line_no, Decimal(0))
        if value > 0:
            lines.append(PostingLine(ROLE_COGS, "DEBIT", value, item_id=x.item.id, memo=f"{memo} L{x.line_no}"))
            lines.append(
                PostingLine(
                    ROLE_INVENTORY,
                    "CREDIT",
                    value,
                    item_id=x.item.id,
                    warehouse_id=ticket.warehouse_id,
                    memo=f"{memo} L{x.line_no}",
                )
            )
    return PostingRequest(
        company_id=ticket.company_id,
        posting_date=ticket.document_date,
        source=DocumentRef(SOURCE, ticket.id, memo),
        purpose="pos_ticket",
        lines=tuple(lines),
        journal_code="SALES",
        branch_id=ticket.branch_id,
    )


def _refuse_offline(uow: UnitOfWork, company_id: uuid.UUID, params: Mapping[str, Any]) -> None:
    """Disconnected fiscal selling is an [A] capability that this edition does not build (ADR-015, EG-13)."""
    if params.get("offline"):
        enabled = flags.is_enabled(flags.POS_OFFLINE_FISCAL_SELLING, uow.ctx.tenant_id, company_id)
        raise OfflineFiscalSellingRefused(
            "offline fiscal selling is not available: the sale is final only when the server commits",
            flag_enabled=enabled,
        )


def checkout(uow: UnitOfWork, company_id: uuid.UUID, params: Mapping[str, Any]) -> Ticket:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.ticket.post", {"company_id": company_id})
    unknown_keys(params, CHECKOUT_FIELDS)
    _refuse_offline(uow, company_id, params)
    client_uuid = uuid_req(params.get("client_uuid"), "client_uuid")
    done = Ticket.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, client_uuid=client_uuid).first()
    if done is not None and done.status == Ticket.Status.POSTED:
        return done  # same client UUID → the same ticket (T-P12-03)
    session = writable_session(uow, company_id, params.get("session_id"))
    cfg = session.config
    if not cfg.active or session.terminal.status != "ACTIVE":
        raise ValidationFailed("the POS configuration or terminal is not active", field="session_id")
    assert_postable_branch(ctx, company_id, cfg.branch_id)
    party = party_of(uow, company_id, params.get("party", params.get("party_id")) or cfg.default_party_id)
    day = clock.business_date()
    cart = pricing.price_cart(uow, cfg, params.get("lines"), day, party)
    expected = params.get("expected_total")
    if expected not in (None, ""):
        if isinstance(expected, Mapping):
            expected = expected.get("amount")
        changed = q_amount(decimal_in(expected, "expected_total", minimum=Decimal(0))) != q_amount(cart.total)
    else:
        changed = bool(cart.diff)
    if changed:
        raise PriceChanged(
            "the server recomputed prices/taxes; show the difference and confirm the new total",
            server=pricing.cart_json(cart),
        )
    tenders, change = parse_tenders(list(cfg.allowed_tenders), params.get("payments"), q_amount(cart.total))
    ticket = done
    if ticket is None and params.get("ticket_id"):
        ticket = Ticket.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, id=uuid_req(params["ticket_id"], "ticket_id")
        ).first()
        if ticket is None:
            raise ValidationFailed("ticket_id is not a parked ticket of the company", field="ticket_id")
    if ticket is not None:
        ticket = lock(Ticket.objects.filter(tenant_id=ctx.tenant_id), ticket.id)
        if ticket.status != Ticket.Status.DRAFT:
            raise InvalidStateTransition(f"a {ticket.status} ticket cannot be checked out", status_now=ticket.status)
        if ticket.cashier_id != ctx.principal_id and not is_supervisor(ctx):
            raise NotFound("ticket not found")
    number = fiscal_number(uow, company_id, cfg.numbering_range_id, day)
    if ticket is None:
        ticket = Ticket(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            client_uuid=client_uuid,
            created_by=ctx.principal_id,
        )
    ticket.session = session
    ticket.client_uuid = client_uuid if ticket.client_uuid is None else ticket.client_uuid
    ticket.document_date = day
    ticket.cashier_id = ctx.principal_id
    ticket.branch_id = cfg.branch_id
    ticket.warehouse_id = cfg.warehouse_id
    ticket.party_id = party.id if party else None
    ticket.notes = text_in(params.get("notes"), "notes")
    ticket.number = number
    ticket.updated_by = ctx.principal_id
    ticket.save()  # still unposted: number held in this transaction only
    source = DocumentRef(SOURCE, ticket.id, number)
    cogs = _issue(uow, ticket, cfg.location_id, source, cart.lines)
    entry = services.get(PostingService).post(uow, _posting(ticket, cart, tenders, cogs))
    for x in cart.lines:
        value = cogs.get(x.line_no, Decimal(0))
        TicketLine.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            ticket=ticket,
            line_no=x.line_no,
            item_id=x.item.id,
            presentation_id=x.presentation.id if x.presentation else None,
            barcode=x.barcode,
            description=x.description,
            quantity=x.quantity,
            factor=x.factor,
            base_qty=x.base_qty,
            unit_price=x.unit_price,
            discount_pct=x.discount_pct,
            amount=x.amount,
            tax_code=x.tax_code,
            tax_amount=x.tax_amount,
            cost_value=value,
            unit_cost=q_unit_cost(value / x.base_qty) if x.base_qty else Decimal(0),
            price_source=x.price_source,
            posted=True,
        )
    for n, t in enumerate(tenders, start=1):
        Tender.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            ticket=ticket,
            line_no=n,
            tender_type=t.tender_type,
            amount=t.amount,
            tendered=t.tendered,
            reference=t.reference,
            posted=True,
        )
    ticket.subtotal, ticket.tax_total, ticket.total = cart.subtotal, cart.tax_total, cart.total
    ticket.change_due = change
    ticket.cost_total = sum(cogs.values(), Decimal(0))
    ticket.journal_entry_id = entry.entry_id
    ticket.sequence_code = "POS"
    ticket.fiscal_doc_type = Ticket.FiscalDocType.DEE_POS
    ticket.fiscal_status = "PENDING"
    ticket.tax_snapshot = {
        "parameters": list(cart.taxes.parameter_snapshots),
        "warnings": list(cart.taxes.warnings),
        "lines": [
            {"line_ref": t.line_ref, "tax_code": t.tax_code, "rate": f"{t.rate:f}", "amount": f"{t.amount:f}"}
            for t in cart.taxes.lines
        ],
    }
    ticket.draft_payload = {}
    ticket.status, ticket.posted, ticket.posted_at = Ticket.Status.POSTED, True, clock.now()
    ticket.version += 1
    ticket.save()
    uow.emit(
        "pos.ticket.checked_out.v1",
        {
            "ticket_id": str(ticket.id),
            "number": number,
            "total": {"amount": f"{q_amount(cart.total):f}", "currency": "COP"},
            "journal_entry_id": str(entry.entry_id),
        },
        company_id=company_id,
    )
    audit(
        uow,
        "pos.ticket.checkout",
        SOURCE,
        ticket.id,
        company_id,
        {"number": number, "journal_entry": str(entry.entry_id)},
    )
    return ticket


def preview(uow: UnitOfWork, company_id: uuid.UUID, params: Mapping[str, Any]) -> dict[str, Any]:
    """Server price/tax recomputation of a cart (read-only; used on reconnect to show the diff)."""
    ctx = uow.ctx
    require(ctx, "pos.ticket.create", {"company_id": company_id})
    session = writable_session(uow, company_id, params.get("session_id"), for_update=False)
    party = party_of(uow, company_id, params.get("party", params.get("party_id")) or session.config.default_party_id)
    return pricing.cart_json(pricing.price_cart(uow, session.config, params.get("lines"), clock.business_date(), party))


# --------------------------------------------------------------------------- parked tickets (DRAFT, unposted)

DRAFT_FIELDS = (
    "session_id",
    "client_uuid",
    "lines",
    "party",
    "payments",
    "notes",
    "document_date",
    "currency",
    "posting_date",
)


def park(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Ticket:
    """``POST /tickets``: a parked cart on the server — no number, no stock, no GL (BORRADOR – sin valor fiscal)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.ticket.create", {"company_id": company_id})
    unknown_keys(data, DRAFT_FIELDS)
    session = writable_session(uow, company_id, data.get("session_id"))
    lines = data.get("lines") or []
    if not isinstance(lines, list):
        raise ValidationFailed("lines must be a list", field="lines")
    client_uuid = uuid_req(data.get("client_uuid"), "client_uuid")
    if Ticket.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, client_uuid=client_uuid).exists():
        raise ValidationFailed("client_uuid already used", field="client_uuid")
    ticket = Ticket.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        session=session,
        client_uuid=client_uuid,
        document_date=clock.business_date(),
        cashier_id=ctx.principal_id,
        branch_id=session.config.branch_id,
        warehouse_id=session.config.warehouse_id,
        party_id=uuid_in(data.get("party"), "party", required=False),
        notes=text_in(data.get("notes"), "notes"),
        draft_payload={"lines": lines},
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "pos.ticket.park", SOURCE, ticket.id, company_id, {})
    return ticket


def update_draft(uow: UnitOfWork, ticket: Ticket, data: Mapping[str, Any]) -> Ticket:
    ctx = uow.ctx
    require(ctx, "pos.ticket.update", ticket)
    unknown_keys(data, DRAFT_FIELDS)
    ticket = lock(Ticket.objects.filter(tenant_id=ctx.tenant_id), ticket.id)
    if ticket.status != Ticket.Status.DRAFT:
        raise InvalidStateTransition("a posted ticket is immutable (use a return)", status_now=ticket.status)
    if "lines" in data:
        if not isinstance(data["lines"], list):
            raise ValidationFailed("lines must be a list", field="lines")
        ticket.draft_payload = {"lines": data["lines"]}
    if "notes" in data:
        ticket.notes = text_in(data["notes"], "notes")
    if "party" in data:
        ticket.party_id = uuid_in(data["party"], "party", required=False)
    ticket.version += 1
    ticket.updated_by, ticket.updated_at = ctx.principal_id, clock.now()
    ticket.save()
    return ticket


def delete_draft(uow: UnitOfWork, ticket: Ticket) -> None:
    require(uow.ctx, "pos.ticket.delete", ticket)
    if ticket.status != Ticket.Status.DRAFT:
        raise InvalidStateTransition("only a parked (DRAFT) ticket can be deleted", status_now=ticket.status)
    ticket.delete()


BUYER_FIELDS = ("id_type", "id_number", "dv", "name", "email", "address", "municipality_code", "phone", "tax_regime")


def to_invoice(uow: UnitOfWork, ticket: Ticket, reason: str, params: Mapping[str, Any]) -> Ticket:
    """Buyer requests an FE for a posted ticket (R-FE-03 minimum buyer data); P15 issues it from the outbox.

    Whether the DEE already issued must be annulled with a note is an annex rule (B-06): this edition records the
    request and hands it to P15 (``pos.ticket.invoice_requested`` audit + request row); it never edits the ticket.
    """
    ctx = uow.ctx
    require(ctx, "pos.ticket.update", ticket)
    if ticket.status != Ticket.Status.POSTED:
        raise InvalidStateTransition(
            "only a posted ticket can be converted to an electronic invoice", status_now=ticket.status
        )
    buyer = params.get("buyer", params)
    if not isinstance(buyer, Mapping):
        raise ValidationFailed("parameters.buyer must be an object", field="buyer")
    unknown_keys(buyer, BUYER_FIELDS, " in buyer")
    for key in ("id_type", "id_number", "name", "email"):
        if not str(buyer.get(key) or "").strip():
            raise ValidationFailed(f"buyer.{key} is required (R-FE-03)", field=f"buyer.{key}")
    if InvoiceRequest.objects.filter(ticket=ticket).exists():
        raise InvalidStateTransition("an electronic invoice was already requested for this ticket")
    InvoiceRequest.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=ticket.company_id,
        ticket=ticket,
        buyer={k: str(v) for k, v in buyer.items()},
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "pos.ticket.to_invoice", SOURCE, ticket.id, ticket.company_id, {"number": ticket.number}, reason)
    return ticket
