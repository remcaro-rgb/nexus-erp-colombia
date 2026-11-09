"""PaymentStatus (owner P16; consumers: portals). FEATURES-ADDENDUM stub."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef

VoucherState = Literal["DRAFT", "APPROVED", "POSTED", "REVERSED"]


@dataclass(frozen=True, slots=True)
class PaymentStatusInfo:
    voucher: DocumentRef  # RC / CE (D-017)
    state: VoucherState
    amount: Decimal
    currency: str
    posted_on: dt.date | None


@runtime_checkable
class PaymentStatus(Protocol):
    def for_document(self, company_id: uuid.UUID, document: DocumentRef) -> list[PaymentStatusInfo]:
        """Vouchers applied to an invoice/bill (policy-scoped, party-scoped for portals)."""
        ...

    def voucher(self, company_id: uuid.UUID, voucher_id: uuid.UUID) -> PaymentStatusInfo: ...
