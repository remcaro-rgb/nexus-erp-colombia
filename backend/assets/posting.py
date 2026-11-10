"""In-line posting for assets (shared/07 §5): all-books entries and **book-specific** entries.

The frozen ``PostingService`` (kernel.interfaces.posting, P05) writes one entry per *mirror* book. Depreciation,
revaluation, impairment, transfers and disposals need separate amounts per book (NIIF vs fiscal), so this
adapter posts into one ledger book only:

1. if the registered service offers ``post_to_book(uow, request, book_code)`` / ``simulate_to_book`` (requested
   from P05 in CCR P14-1), it is used;
2. else, for the P05 ledger implementation, the same planning code (`plan`: period, accounts, dimensions,
   balance) is run and the plan is restricted to that book before it is persisted — same validations, same
   immutable rows, same idempotency key ``(company, source, purpose)``; the purpose carries the book code;
3. any other implementation (consumer fakes) receives a plain ``post`` with the book-specific purpose.

Everything runs inside the caller's unit of work: a failure rolls the asset document back with the GL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import ValidationFailed
from kernel.interfaces.posting import PostingRequest, PostingResult, PostingService
from kernel.uow import UnitOfWork

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class SimulatedLine:
    account: str
    debit: Decimal
    credit: Decimal
    slots: dict[str, Any]


def _ledger_impl(svc: object) -> Any:
    try:
        from ledger.posting import LedgerPostingService
    except ImportError:  # pragma: no cover - the ledger app is part of every build
        return None
    return svc if isinstance(svc, LedgerPostingService) else None


def post_all_books(uow: UnitOfWork, request: PostingRequest) -> PostingResult:
    """Entries with the same amounts in every mirror book (acquisition / capitalization)."""
    return services.get(PostingService).post(uow, request)


def post_to_book(uow: UnitOfWork, request: PostingRequest, book_code: str) -> PostingResult:
    svc = services.get(PostingService)
    native = getattr(svc, "post_to_book", None)
    if callable(native):
        result: PostingResult = native(uow, request, book_code)
        return result
    ledger = _ledger_impl(svc)
    if ledger is None:
        return svc.post(uow, request)
    prior = ledger._existing(uow.ctx, request)
    if prior is not None:
        return PostingResult(prior.id, prior.number, prior.total_debit, prior.total_credit, True)
    plan = ledger.plan(uow, request)
    plan.books = [b for b in plan.books if b.code == book_code]
    if not plan.books:
        raise ValidationFailed(f"ledger book {book_code} is not configured", pointer="/book_id", reason="not_found")
    persisted: PostingResult = ledger._persist(uow, plan)
    return persisted


def simulate_to_book(uow: UnitOfWork, request: PostingRequest, book_code: str) -> list[SimulatedLine]:
    """Same validations as :func:`post_to_book`; writes nothing; returns the would-be lines."""
    svc = services.get(PostingService)
    native = getattr(svc, "simulate_to_book", None)
    if callable(native):
        lines: list[SimulatedLine] = native(uow, request, book_code)
        return lines
    ledger = _ledger_impl(svc)
    if ledger is None:
        svc.simulate(uow, request)
        return [
            SimulatedLine(
                ln.account_role,
                ln.amount if ln.side == "DEBIT" else ZERO,
                ln.amount if ln.side == "CREDIT" else ZERO,
                {
                    "cost_center_id": ln.dimensions.cost_center_id,
                    "business_unit_id": ln.dimensions.business_unit_id,
                    "aux_abierto_id": ln.dimensions.aux_abierto_id,
                    "item_dimension_id": ln.dimensions.item_dimension_id,
                },
            )
            for ln in request.lines
        ]
    plan = ledger.plan(uow, request)
    if not any(b.code == book_code for b in plan.books):
        raise ValidationFailed(f"ledger book {book_code} is not configured", pointer="/book_id", reason="not_found")
    return [
        SimulatedLine(ln.account.code, ln.debit, ln.credit, {k: v for k, v in ln.slots.items()}) for ln in plan.lines
    ]


def entry_ids(*results: PostingResult) -> list[str]:
    return [str(r.entry_id) for r in results if r.entry_id != uuid.UUID(int=0)]
