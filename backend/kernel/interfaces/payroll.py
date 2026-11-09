"""PayrollPayables (owner P13; consumer P16 payments). FEATURES-ADDENDUM stub."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class PayrollPayable:
    id: uuid.UUID
    run: DocumentRef
    beneficiary_id: uuid.UUID  # employee or social-security entity
    concept: str
    open_amount: Decimal
    currency: str = "COP"


@runtime_checkable
class PayrollPayables(Protocol):
    def open_payables(self, company_id: uuid.UUID, run_id: uuid.UUID | None = None) -> list[PayrollPayable]: ...

    def settle(
        self, uow: UnitOfWork, company_id: uuid.UUID, payment: DocumentRef, payable_ids: list[uuid.UUID]
    ) -> None:
        """Mark payables paid by ``payment`` (sub-ledger op, caller's UoW)."""
        ...
