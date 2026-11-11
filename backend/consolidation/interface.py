"""Frozen in-process contract of the intercompany module (P16b) for sales (P10), purchasing (P11) and inventory (P09).

``IntercompanyService`` is registered in ``kernel.services`` (``consolidation.registry``). Every method runs
**inside the caller's unit of work** (S10: a failure on either side rolls back both companies) and never does I/O.

* ``mirror_sales_invoice`` — called by the sales posting of an IC invoice of company A to party = company B: creates
  B's mirror vendor invoice (Dr INVENTORY or EXPENSE_IC / Dr VAT_RECOVERABLE / Cr IC_PAYABLE, party = A) with the
  IC system principal acting on behalf of the user (recorded causation), linked by ``ic_link_id``.
* ``post_sale`` — the whole S10 composite for documents that do not come from the sales module (A: Dr IC_RECEIVABLE /
  Cr REVENUE_IC / Cr VAT_PAYABLE and Dr COGS / Cr INVENTORY; then the mirror).
* ``settle`` / ``net`` — IC payment (B pays A: B Dr IC_PAYABLE / Cr BANK, A Dr BANK / Cr IC_RECEIVABLE) and
  netting without cash, both sides in the same transaction.
* ``record_consumption`` — the buyer consumed/sold IC goods (reduces the unrealized profit in its inventory).

Taxes are never computed here: VAT amounts come from the caller (P06 tax engine through sales/purchasing).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class IcLine:
    description: str
    quantity: Decimal
    unit_price: Decimal  # transfer price, before taxes (COP)
    tax_amount: Decimal = Decimal(0)  # VAT of the line, from the tax engine (never computed here)
    unit_cost: Decimal = Decimal(0)  # seller's cost per unit (goods); 0 for services
    item_id: uuid.UUID | None = None  # goods when set (B debits INVENTORY), services otherwise (B debits EXPENSE_IC)

    @property
    def subtotal(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def cost(self) -> Decimal:
        return self.quantity * self.unit_cost


@dataclass(frozen=True, slots=True)
class IcSaleRequest:
    company_a_id: uuid.UUID  # seller
    company_b_id: uuid.UUID  # buyer
    source: DocumentRef  # A's IC sales invoice
    document_date: dt.date
    lines: tuple[IcLine, ...]
    currency: str = "COP"
    a_entry_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IcResult:
    link_id: uuid.UUID
    b_document_id: uuid.UUID | None
    b_document_number: str
    a_entry_ids: tuple[uuid.UUID, ...]
    b_entry_ids: tuple[uuid.UUID, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class IcSettlementRequest:
    company_a_id: uuid.UUID  # creditor (seller)
    company_b_id: uuid.UUID  # debtor (buyer)
    source: DocumentRef
    document_date: dt.date
    amount: Decimal
    currency: str = "COP"


@runtime_checkable
class IntercompanyService(Protocol):
    def mirror_sales_invoice(self, uow: UnitOfWork, request: IcSaleRequest) -> IcResult: ...

    def post_sale(self, uow: UnitOfWork, request: IcSaleRequest) -> IcResult: ...

    def settle(self, uow: UnitOfWork, request: IcSettlementRequest) -> IcResult: ...

    def net(self, uow: UnitOfWork, request: IcSettlementRequest) -> IcResult: ...

    def record_consumption(self, uow: UnitOfWork, link_id: uuid.UUID, amount: Decimal) -> None: ...
