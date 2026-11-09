"""ArOpenItems (owner P10; consumer P16 treasury receipts). FEATURES-ADDENDUM stub.

Contract: application of a receipt to open items is a sub-ledger operation in
the caller's UoW, under row locks on the open items; never over-applies.
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
class OpenItem:
    id: uuid.UUID
    party_id: uuid.UUID
    document: DocumentRef
    due_date: dt.date
    currency: str
    open_amount: Decimal


@dataclass(frozen=True, slots=True)
class Application:
    open_item_id: uuid.UUID
    amount: Decimal


@runtime_checkable
class ArOpenItems(Protocol):
    def open_items(self, company_id: uuid.UUID, party_id: uuid.UUID) -> list[OpenItem]: ...

    def apply(
        self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef, applications: list[Application]
    ) -> None:
        """``overapplication`` if an amount exceeds the open amount."""
        ...

    def unapply(self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef) -> None: ...
