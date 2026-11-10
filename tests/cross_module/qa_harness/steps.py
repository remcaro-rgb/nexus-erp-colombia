"""Reusable scenario steps (shared/07 posting matrix) on top of ``refdocs``.

Amounts come from the real P06 tax engine (``taxes``) and the stock service's own valuation; the
golden oracle (``golden``) is only used by the tests to *check* them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from . import refdocs, taxes
from .refdocs import Entry, Posted, StockOp

D = Decimal
Hooks = dict[str, Callable[[Any], None]]


def stock_line(
    ref: str, item_id: uuid.UUID, warehouse_id: uuid.UUID, qty: Decimal, unit_cost: Decimal | None = None
) -> Any:
    from kernel.interfaces.stock import StockLine

    return StockLine(ref, item_id, warehouse_id, qty, unit_cost=unit_cost)


def define_item(
    world: Any, company: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID, method: str = "WAC"
) -> None:
    stock = world.services["stock"]
    if hasattr(stock, "define_item"):  # the reference double; P09 items come from its own master data
        world.run(lambda u: stock.define_item(u, company, item_id, warehouse_id, method))


def post_entries(uow: Any, posted: Posted, day: dt.date, entries: list[Entry]) -> None:
    from kernel import services
    from kernel.interfaces.posting import PostingRequest, PostingService

    for entry in entries:
        target = entry.company or posted.company
        request = PostingRequest(
            company_id=target,
            posting_date=entry.posting_date or day,
            source=posted.ref,
            purpose=entry.purpose,
            lines=tuple(refdocs.posting_line(spec) for spec in entry.lines),
            journal_code=entry.journal or refdocs.JOURNAL_BY_TYPE[posted.ref.type],
        )
        posted.entries.append(services.get(PostingService).post(uow, request))


def goods_receipt(
    world: Any,
    company: uuid.UUID,
    item_id: uuid.UUID,
    wh: uuid.UUID,
    qty: Decimal,
    unit_cost: Decimal,
    day: dt.date,
    line_ref: str = "1",
) -> Posted:
    """Goods receipt (stockable): valuation layer at PO price; Dr INVENTORY / Cr GRNI (shared/07 §2)."""
    stock = world.services["stock"]
    value = qty * unit_cost

    def fn(u: Any) -> Posted:
        return refdocs.post_document(
            u, doc_type="purchase.receipt", company=company, day=day, total=value,
            stock=[StockOp("receive", [stock_line(line_ref, item_id, wh, qty, unit_cost)])],
            entries=[Entry("receipt", [("INVENTORY", "DEBIT", value), ("GRNI", "CREDIT", value)])],
            payload={"item_id": item_id, "qty": qty, "unit_cost": unit_cost}, stock_service=stock,
        )  # fmt: skip

    return world.run(fn)


def sales_invoice(
    world: Any,
    company: uuid.UUID,
    customer: uuid.UUID,
    item_id: uuid.UUID,
    wh: uuid.UUID,
    qty: Decimal,
    price: Decimal,
    day: dt.date,
    *,
    buyer_agent: bool = True,
    hooks: Hooks | None = None,
    idempotency_key: str | None = None,
    doc_type: str = "sales.invoice",
    revenue_role: str = "REVENUE",
    ar_role: str = "AR",
    in_uow: Any = None,
) -> Any:
    """Invoice posted with "deliver on invoice": stock issue + AR/revenue/VAT entry + COGS entry in ONE unit
    of work (shared/07 §1). Hooks: ``after_number``, ``after_stock`` (stock moves written, no journal yet),
    ``after_journal``."""
    stock = world.services["stock"]
    hooks = hooks or {}
    net = qty * price
    tax = taxes.compute(company, day, [("1", net, ("FIX-IVA19",))], buyer_agent=buyer_agent)
    vat = taxes.by_type(tax).get("IVA", D(0))
    total = net + vat

    def fn(u: Any) -> Posted:
        posted = refdocs.post_document(
            u, doc_type=doc_type, company=company, day=day, total=total,
            stock=[StockOp("issue", [stock_line("1", item_id, wh, qty)])],
            payload={"customer": customer, "item_id": item_id, "qty": qty, "price": price, "net": net, "vat": vat,
                     "tax_warnings": list(tax.warnings)},
            stock_service=stock, hooks={k: v for k, v in hooks.items() if k == "after_number"},
        )  # fmt: skip
        if "after_stock" in hooks:
            hooks["after_stock"](u)
        cogs = sum((r.total_value for r in posted.stock), D(0))
        post_entries(u, posted, day, [
            Entry("issue", [(ar_role, "DEBIT", total, {"party": customer, "party_kind": "customer"}),
                            (revenue_role, "CREDIT", net), ("VAT_PAYABLE", "CREDIT", vat, {"tax_code": "FIX-IVA19"})]),
            Entry("cogs", [("COGS", "DEBIT", cogs), ("INVENTORY", "CREDIT", cogs)]),
        ])  # fmt: skip
        if "after_journal" in hooks:
            hooks["after_journal"](u)
        return posted

    if in_uow is not None:
        return fn(in_uow)
    request = {"customer": str(customer), "item": str(item_id), "qty": str(qty), "price": str(price)}
    return world.run(fn, idempotency_key=idempotency_key, request=request)


def customer_receipt(
    world: Any,
    company: uuid.UUID,
    customer: uuid.UUID,
    invoice: Posted,
    gross: Decimal,
    net_amount: Decimal,
    day: dt.date,
) -> tuple[Posted, Any]:
    """Customer receipt with the withholdings practised by the customer (shared/07 §1)."""
    tax = taxes.compute(
        company, day, [("1", net_amount, ("FIX-IVA19", "FIX-RF-VENTAS", "FIX-RETEIVA", "FIX-RETEICA"))],
        buyer_agent=True,
    )  # fmt: skip
    wh = {t.account_role: t.amount for t in tax.lines if t.withholding}
    bank = gross - sum(wh.values(), D(0))
    lines: list[Any] = [("BANK", "DEBIT", bank)]
    lines += [(role, "DEBIT", amount) for role, amount in sorted(wh.items())]
    lines.append(("AR", "CREDIT", gross, {"party": customer, "party_kind": "customer"}))

    def fn(u: Any) -> Posted:
        return refdocs.post_document(
            u, doc_type="sales.receipt", company=company, day=day, total=bank, entries=[Entry("receipt", lines)],
            links={"invoice": str(invoice.ref.id)}, payload={"withholdings": {k: str(v) for k, v in wh.items()}},
        )  # fmt: skip

    return world.run(fn), tax
