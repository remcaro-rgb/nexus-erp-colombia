"""Interfaces provided and consumed by the fixed-assets module (P14).

Provided — :class:`AssetService` (consumers P11 vendor invoices, P09 inventory). Registered in
``kernel.services`` under this Protocol; the typed fake lives in ``assets.testing.fakes.FakeAssetService``.
CCR P14-3 asks P01 to move the Protocol to ``kernel/interfaces/assets.py`` (re-exported here then).

Consumed — :class:`DisposalInvoicing` (owner P10, sales): creates the customer invoice of an asset sale as a
*non-stock* line linked to the disposal. The disposal entry posts the GL (AR, VAT, cost, accumulated
depreciation, gain/loss) so the invoice must not post a second entry (CCR P14-4). Until P10 registers an
implementation, a sale is recorded with the customer reference only and the invoice link stays empty.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel.interfaces.common import DimensionValues, DocumentRef, PartyRef
from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class CapitalizedAsset:
    asset_id: uuid.UUID
    code: str
    #: account role the caller must debit for the capitalized cost (category cost role)
    cost_account_role: str
    capitalized: bool = True


@dataclass(frozen=True, slots=True)
class InvoiceLineCapitalization:
    company_id: uuid.UUID
    category_id: uuid.UUID
    invoice: DocumentRef
    line_no: int
    description: str
    cost: Decimal
    acquisition_date: dt.date
    supplier: PartyRef | None = None
    in_service_date: dt.date | None = None
    code: str | None = None
    serial: str = ""
    location: str | None = None
    branch_id: uuid.UUID | None = None
    dimensions: DimensionValues = field(default_factory=DimensionValues)


@dataclass(frozen=True, slots=True)
class InventoryCapitalization:
    company_id: uuid.UUID
    category_id: uuid.UUID
    source: DocumentRef  # the stock issue / CIP settlement document
    description: str
    cost: Decimal
    date: dt.date
    item_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    #: credit role: ``INVENTORY`` (stock) or ``CIP`` (construction in progress)
    credit_role: str = "INVENTORY"
    in_service_date: dt.date | None = None
    code: str | None = None
    location: str | None = None
    branch_id: uuid.UUID | None = None
    dimensions: DimensionValues = field(default_factory=DimensionValues)


@runtime_checkable
class AssetService(Protocol):
    def capitalize_from_invoice_line(self, uow: UnitOfWork, data: InvoiceLineCapitalization) -> CapitalizedAsset:
        """Register the asset of a vendor-invoice line **inside the invoice's unit of work**.

        The caller (P11) posts ``Dr <cost_account_role> / Cr AP`` in its own invoice entry; this call writes the
        register rows (acquisition per book), so a rollback of the invoice removes the asset too. Below the
        category's capitalization threshold → ``validation_failed`` (reason ``below_capitalization_threshold``);
        the caller then expenses the line.
        """
        ...

    def capitalize_from_inventory(self, uow: UnitOfWork, data: InventoryCapitalization) -> CapitalizedAsset:
        """Create the asset and post ``Dr FIXED_ASSET_COST / Cr INVENTORY|CIP`` (all books) in the caller's UoW.

        The stock move (quantity/valuation) is P09's; P09 must not post a second GL entry for it.
        """
        ...

    def should_capitalize(
        self, uow: UnitOfWork, company_id: uuid.UUID, category_id: uuid.UUID, cost: Decimal, on_date: dt.date
    ) -> bool:
        """True when ``cost`` reaches the category's capitalization threshold."""
        ...


@dataclass(frozen=True, slots=True)
class DisposalInvoiceRequest:
    company_id: uuid.UUID
    customer: PartyRef
    date: dt.date
    description: str
    net: Decimal
    vat: Decimal
    vat_tax_code: str | None
    source: DocumentRef  # the disposal


@runtime_checkable
class DisposalInvoicing(Protocol):
    def create_disposal_invoice(self, uow: UnitOfWork, request: DisposalInvoiceRequest) -> DocumentRef:
        """Create (without posting GL) the sales invoice of an asset sale; returns its reference."""
        ...
