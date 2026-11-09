"""StockService / Inventory (owner P09, C1-2, shared/07 §3, §7).

Contract:
* Called inside the caller's unit of work; row locks on quants/valuation
  acquired in sorted ``(item_id, warehouse_id, lot_id)`` order.
* Post: stock ledger, on-hand/reserved and valuation layers updated in the
  same transaction; GL posted through ``PostingService`` when perpetual.
* Negative stock refused unless the warehouse allows it (D-032):
  ``insufficient_stock``.
* Idempotency: ``(source.type, source.id, line_ref)`` applies once.
* ``Inventory`` extends ``StockService`` with availability, reservations,
  transfers, putaway and quality hold (FEATURES-ADDENDUM stubs).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class StockLine:
    line_ref: str
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal  # base UoM (D-013)
    location_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    presentation_id: uuid.UUID | None = None
    unit_cost: Decimal | None = None  # receipts only, NUMERIC(24,8)


@dataclass(frozen=True, slots=True)
class StockMoveResult:
    move_ids: tuple[uuid.UUID, ...]
    total_value: Decimal
    journal_entry_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Availability:
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


@runtime_checkable
class StockService(Protocol):
    def receive(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        """Receipt into leaf INTERNAL bins (putaway default per D-014)."""
        ...

    def issue(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        """Issue at current valuation; ``insufficient_stock`` when not available."""
        ...

    def return_moves(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, original: DocumentRef
    ) -> StockMoveResult:
        """Reverse the moves of ``original`` (returns), linked to it."""
        ...


@runtime_checkable
class Inventory(StockService, Protocol):
    def availability(
        self, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> list[Availability]:
        """Policy-scoped availability per warehouse (read-only)."""
        ...

    def reserve(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine]
    ) -> list[uuid.UUID]:
        """Reserve under row lock; never exceeds availability."""
        ...

    def release(self, uow: UnitOfWork, reservation_ids: list[uuid.UUID]) -> None:
        """Release reservations (idempotent)."""
        ...

    def create_transfer(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        source: DocumentRef,
        from_warehouse: uuid.UUID,
        to_warehouse: uuid.UUID,
        lines: list[StockLine],
    ) -> uuid.UUID:
        """DRAFT transfer with reservations (D-012), same company only."""
        ...

    def quality_hold(self, uow: UnitOfWork, company_id: uuid.UUID, move_ids: list[uuid.UUID], reason: str) -> None:
        """Move received stock to a non-available quality-hold state."""
        ...
