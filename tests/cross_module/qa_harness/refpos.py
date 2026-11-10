"""Reference POS terminal + server checkout for S4 (P22b test double of P12; shared/11 S4, ADR-015).

The terminal holds drafts locally while disconnected and can never post, number, print or transmit
without the server: checkout and tender are disabled with the message "Sin conexión", a draft is
watermarked "BORRADOR – sin valor fiscal" and has no receipt print. On reconnect the server **recomputes**
the ticket from its own price list: same price → one atomic commit (number, stock, GL, outbox, audit);
different price → a diff the cashier must confirm before anything is written. The flag
``pos_offline_fiscal_selling`` stays off (the real ``kernel.flags`` / ``kernel.sequences`` refuse terminal
blocks).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import refdocs, steps, taxes
from .refdocs import Entry, Posted, StockOp

OFFLINE_MESSAGE = "Sin conexión"
DRAFT_WATERMARK = "BORRADOR – sin valor fiscal"


class Offline(RuntimeError):
    """The terminal has no connection: nothing can be committed."""


@dataclass(frozen=True)
class PriceDiff:
    line: str
    draft_price: Decimal
    server_price: Decimal


@dataclass
class Draft:
    local_id: str
    lines: list[tuple[uuid.UUID, Decimal, Decimal]]  # item, qty, unit price incl. nothing (net)
    tender: str  # CASH | CARD
    idempotency_key: str
    watermark: str = DRAFT_WATERMARK
    posted: Posted | None = None

    @property
    def printable(self) -> bool:
        return self.posted is not None


@dataclass
class Terminal:
    world: Any
    company: uuid.UUID
    warehouse: uuid.UUID
    online: bool = True
    drafts: dict[str, Draft] = field(default_factory=dict)

    def ui(self) -> dict[str, Any]:
        return {
            "checkout_enabled": self.online,
            "tender_enabled": self.online,
            "message": "" if self.online else OFFLINE_MESSAGE,
        }

    def build(self, local_id: str, lines: list[tuple[uuid.UUID, Decimal, Decimal]], tender: str) -> Draft:
        draft = Draft(local_id, lines, tender, idempotency_key=f"pos-{local_id}-{self.company.hex[:8]}")
        self.drafts[local_id] = draft
        return draft

    def checkout(
        self, server: Server, draft: Draft, day: dt.date, *, confirm_diff: bool = False, hooks: Any = None
    ) -> Posted | list[PriceDiff]:
        if not self.online:
            raise Offline(OFFLINE_MESSAGE)
        result = server.checkout(self, draft, day, confirm_diff=confirm_diff, hooks=hooks)
        if isinstance(result, Posted):
            draft.posted = result
            draft.watermark = ""
        elif hasattr(result, "body"):  # idempotent replay of an already committed ticket
            draft.watermark = ""
        return result


@dataclass
class Server:
    prices: dict[uuid.UUID, Decimal]

    def checkout(
        self, terminal: Terminal, draft: Draft, day: dt.date, *, confirm_diff: bool, hooks: Any
    ) -> Posted | list[PriceDiff]:
        diffs = [PriceDiff(str(it), price, self.prices[it]) for it, _q, price in draft.lines if self.prices[it] != price]
        if diffs and not confirm_diff:
            return diffs  # shown to the cashier; nothing written
        w, a, wh = terminal.world, terminal.company, terminal.warehouse
        net = sum((q * self.prices[it] for it, q, _p in draft.lines), Decimal(0))
        tax = taxes.compute(a, day, [("1", net, ("FIX-IVA19",))], buyer_agent=False)
        vat = taxes.by_type(tax)["IVA"]
        total = net + vat
        tender_role = "CASH_POS" if draft.tender == "CASH" else "CARD_CLEARING"
        stock = w.services["stock"]
        hooks = hooks or {}

        def fn(u: Any) -> Posted:
            lines = [steps.stock_line(str(i), it, wh, q) for i, (it, q, _p) in enumerate(draft.lines)]
            posted = refdocs.post_document(
                u, doc_type="pos.ticket", company=a, day=day, total=total, stock=[StockOp("issue", lines)],
                stock_service=stock, payload={"local_id": draft.local_id, "tender": draft.tender},
            )  # fmt: skip
            if "after_stock" in hooks:
                hooks["after_stock"](u)
            cogs = sum((r.total_value for r in posted.stock), Decimal(0))
            steps.post_entries(u, posted, day, [
                Entry("sale", [(tender_role, "DEBIT", total), ("REVENUE", "CREDIT", net), ("VAT_PAYABLE", "CREDIT", vat)]),
                Entry("cogs", [("COGS", "DEBIT", cogs), ("INVENTORY", "CREDIT", cogs)]),
            ])  # fmt: skip
            return posted

        return w.run(fn, idempotency_key=draft.idempotency_key, request={"lines": [str(x) for x in draft.lines]})

    def refund(self, terminal: Terminal, ticket: Posted, amount_net: Decimal, day: dt.date) -> Posted:
        w, a = terminal.world, terminal.company
        vat = taxes.by_type(taxes.compute(a, day, [("1", amount_net, ("FIX-IVA19",))], buyer_agent=False))["IVA"]
        stock = w.services["stock"]

        def fn(u: Any) -> Posted:
            posted = refdocs.post_document(
                u, doc_type="pos.return", company=a, day=day, total=amount_net + vat,
                stock=[StockOp("return", original=ticket.ref)], stock_service=stock,
                links={"ticket": str(ticket.ref.id)},
            )  # fmt: skip
            back = abs(sum((r.total_value for r in posted.stock), Decimal(0)))  # value received back
            steps.post_entries(u, posted, day, [
                Entry("refund", [("REVENUE", "DEBIT", amount_net), ("VAT_PAYABLE", "DEBIT", vat),
                                 ("CASH_POS", "CREDIT", amount_net + vat)]),
                Entry("cogs", [("INVENTORY", "DEBIT", back), ("COGS", "CREDIT", back)]),
            ])  # fmt: skip
            return posted

        return w.run(fn)

    def open_session(self, terminal: Terminal, float_amount: Decimal, day: dt.date) -> Posted:
        w, a = terminal.world, terminal.company
        return w.run(lambda u: refdocs.post_document(
            u, doc_type="pos.session_close", company=a, day=day, total=float_amount, payload={"event": "open"},
            entries=[Entry("opening_float", [("CASH_POS", "DEBIT", float_amount), ("CASH", "CREDIT", float_amount)])],
        ))  # fmt: skip

    def close_session(self, terminal: Terminal, counted: Decimal, day: dt.date) -> tuple[Decimal, Posted]:
        w, a = terminal.world, terminal.company
        expected = w.balance("CASH_POS", a)
        diff = expected - counted
        entries = []
        if diff > 0:
            entries.append(Entry("cash_difference", [("CASH_DIFFERENCE", "DEBIT", diff), ("CASH_POS", "CREDIT", diff)]))
        elif diff < 0:
            entries.append(Entry("cash_difference", [("CASH_POS", "DEBIT", -diff), ("CASH_DIFFERENCE", "CREDIT", -diff)]))
        posted = w.run(lambda u: refdocs.post_document(
            u, doc_type="pos.session_close", company=a, day=day, total=counted,
            payload={"event": "close", "expected": str(expected), "counted": str(counted)}, entries=entries,
        ))  # fmt: skip
        return expected, posted
