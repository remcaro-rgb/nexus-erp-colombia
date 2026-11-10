"""Reference document flows for the cross-module scenarios (P22b test doubles of P10/P11/P12/P13/P14/P16).

Each flow is **one unit of work** that composes the real platform pieces exactly as C1-1/C1-2 require:

1. a gap-free number from the real ``kernel.sequences`` (row lock, D-017 / invariant 9);
2. the document row (scratch table ``qa_ref_document``);
3. stock moves + valuation through ``kernel.interfaces.stock.StockService`` (``refstock`` until P09);
4. the balanced journal entries through the real P05 ``PostingService`` (one per purpose, mirror books);
5. the outbox event (``uow.emit``) and the audit record (real P07 ``DbAuditService``).

Any exception anywhere aborts the whole document. ``hooks`` lets the failure-injection variants raise at
a named stage (``after_number``, ``after_stock``, ``after_journal``) - S4/S5 "fail after the stock move
insert and before the journal insert". The flows never decide accounting policy beyond shared/07: the
journal lines are passed in by the scenario, computed from the golden oracle and the real tax engine.

``implementation`` names what ran, so the evidence never presents a double as the module.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

IMPLEMENTATION = "reference-flows:P22b (document modules P10/P11/P12/P13/P14/P16 not merged)"

DDL = """
CREATE TABLE IF NOT EXISTS qa_ref_document (
    id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL, doc_type text NOT NULL,
    number text NOT NULL, status text NOT NULL, document_date date NOT NULL, total numeric(20,4) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb, links jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, company_id, doc_type, number)
);
"""
SEQUENCE_BY_TYPE = {
    "sales.invoice": "FV",
    "sales.receipt": "RC",
    "sales.credit_note": "NC",
    "purchase.receipt": "REC",
    "purchase.invoice": "FC",
    "purchase.support_document": "DS",
    "treasury.payment": "CE",
    "treasury.fx_revaluation": "CE",
    "pos.ticket": "POS",
    "pos.return": "NC",
    "pos.session_close": "SES",
    "payroll.run": "NOM",
    "assets.acquisition": "AF",
    "assets.depreciation": "AF",
    "assets.disposal": "AF",
    "intercompany.sale": "IC",
    "onboarding.opening": "APE",
}
JOURNAL_BY_TYPE = {
    "sales.invoice": "SALES",
    "sales.receipt": "BANK",
    "sales.credit_note": "SALES",
    "purchase.receipt": "INVENTORY",
    "purchase.invoice": "PURCHASES",
    "purchase.support_document": "PURCHASES",
    "treasury.payment": "BANK",
    "treasury.fx_revaluation": "ADJUSTMENTS",
    "pos.ticket": "CASH",
    "pos.return": "CASH",
    "pos.session_close": "CASH",
    "payroll.run": "PAYROLL",
    "assets.acquisition": "PURCHASES",
    "assets.depreciation": "ASSETS",
    "assets.disposal": "ASSETS",
    "intercompany.sale": "SALES",
    "onboarding.opening": "OPENING",
}


def install(using: str = "default") -> None:
    from django.db import connections

    with connections[using].cursor() as cur:
        cur.execute(DDL)


def uninstall(using: str = "default") -> None:
    from django.db import connections

    with connections[using].cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS qa_ref_document")


@dataclass(frozen=True)
class Entry:
    """One journal entry of a document: ``purpose`` + lines ``(role, 'DEBIT'|'CREDIT', amount, extra)``."""

    purpose: str
    lines: Sequence[tuple[str, str, Decimal] | tuple[str, str, Decimal, dict[str, Any]]]
    journal: str | None = None
    posting_date: dt.date | None = None
    company: uuid.UUID | None = None


@dataclass(frozen=True)
class StockOp:
    kind: str  # receive | issue | return
    lines: Sequence[Any] = ()  # kernel.interfaces.stock.StockLine
    original: Any = None  # DocumentRef for returns
    company: uuid.UUID | None = None


@dataclass
class Posted:
    ref: Any  # DocumentRef(type, id, number)
    company: uuid.UUID
    stock: list[Any] = field(default_factory=list)
    entries: list[Any] = field(default_factory=list)
    status: str = "posted"


def posting_line(spec: Sequence[Any]) -> Any:
    from kernel.interfaces.common import PartyRef
    from kernel.interfaces.posting import PostingLine

    role, side, amount = spec[0], spec[1], spec[2]
    extra = dict(spec[3]) if len(spec) > 3 else {}
    party = extra.pop("party", None)
    if party is not None and not isinstance(party, PartyRef):
        party = PartyRef(party, extra.pop("party_kind", "party"))
    return PostingLine(role, side, Decimal(amount), party=party, **extra)


def post_document(
    uow: Any,
    *,
    doc_type: str,
    company: uuid.UUID,
    day: dt.date,
    total: Decimal,
    entries: Sequence[Entry] = (),
    stock: Sequence[StockOp] = (),
    payload: dict[str, Any] | None = None,
    links: dict[str, Any] | None = None,
    hooks: dict[str, Callable[[Any], None]] | None = None,
    stock_service: Any = None,
    status: str = "posted",
    doc_id: uuid.UUID | None = None,
) -> Posted:
    """The single in-line posting path every reference flow uses (see module docstring)."""
    from django.db import connections

    from kernel import ids, sequences, services
    from kernel.interfaces.audit import AuditEntry, AuditService
    from kernel.interfaces.common import DocumentRef
    from kernel.interfaces.posting import PostingRequest, PostingService

    hooks = hooks or {}
    uow.ctx.require_company(company)
    number = sequences.allocate(company, SEQUENCE_BY_TYPE[doc_type], prefix=_prefix(doc_type), on_date=day)
    ref = DocumentRef(doc_type, doc_id or ids.new_id(), number.formatted)
    _hook(hooks, "after_number", uow)
    with connections[uow.using].cursor() as cur:
        cur.execute(
            "INSERT INTO qa_ref_document (id, tenant_id, company_id, doc_type, number, status, document_date, total, "
            "payload, links) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [ref.id, uow.ctx.tenant_id, company, doc_type, ref.number, status, day, total,
             json.dumps(payload or {}, default=str), json.dumps(links or {}, default=str)],
        )  # fmt: skip
    posted = Posted(ref=ref, company=company, status=status)
    for op in stock:
        target = op.company or company
        if op.kind == "receive":
            posted.stock.append(stock_service.receive(uow, target, ref, list(op.lines), day))
        elif op.kind == "issue":
            posted.stock.append(stock_service.issue(uow, target, ref, list(op.lines), day))
        elif op.kind == "return":
            posted.stock.append(stock_service.return_moves(uow, target, ref, op.original))
        else:
            raise ValueError(op.kind)
    _hook(hooks, "after_stock", uow)
    posting = services.get(PostingService)
    for entry in entries:
        target = entry.company or company
        request = PostingRequest(
            company_id=target,
            posting_date=entry.posting_date or day,
            source=ref,
            purpose=entry.purpose,
            lines=tuple(posting_line(spec) for spec in entry.lines),
            journal_code=entry.journal or JOURNAL_BY_TYPE[doc_type],
        )
        posted.entries.append(posting.post(uow, request))
    _hook(hooks, "after_journal", uow)
    uow.emit(
        f"{doc_type}.posted.v1",
        {"id": str(ref.id), "number": ref.number, "company_id": str(company), "total": str(total)},
        company_id=company,
    )
    services.get(AuditService).record(
        uow,
        AuditEntry(f"{doc_type}.post", doc_type, ref.id, company, changes={"number": ref.number, "total": str(total)}),
    )
    uow.set_response({"id": str(ref.id), "number": ref.number, "status": status}, 201)
    return posted


def _prefix(doc_type: str) -> str:
    from .andina import SEQUENCES

    return dict(SEQUENCES)[SEQUENCE_BY_TYPE[doc_type]]


def _hook(hooks: dict[str, Callable[[Any], None]], stage: str, uow: Any) -> None:
    fn = hooks.get(stage)
    if fn is not None:
        fn(uow)


def document(ctx: Any, ref: Any) -> dict[str, Any] | None:
    from django.db import connection

    from kernel.testing.tenancy import bound

    with bound(ctx), connection.cursor() as cur:
        cur.execute(
            "SELECT number, status, total, payload, links FROM qa_ref_document WHERE tenant_id = %s AND id = %s",
            [ctx.tenant_id, ref.id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    out = dict(zip(("number", "status", "total", "payload", "links"), row, strict=True))
    for key in ("payload", "links"):  # raw cursors return jsonb as text under Django
        if isinstance(out[key], str):
            out[key] = json.loads(out[key])
    return out


def numbers(ctx: Any, company: uuid.UUID, doc_type: str) -> list[str]:
    from django.db import connection

    from kernel.testing.tenancy import bound

    with bound(ctx), connection.cursor() as cur:
        cur.execute(
            "SELECT number FROM qa_ref_document WHERE tenant_id = %s AND company_id = %s AND doc_type = %s",
            [ctx.tenant_id, company, doc_type],
        )
        return sorted((r[0] for r in cur.fetchall()), key=lambda n: int(n.split("-")[-1]))
