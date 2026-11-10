"""Typed fakes for consumers of :class:`assets.interface.AssetService` (P11 vendor invoices, P09 inventory).

``FakeAssetService`` records the calls and returns deterministic references; it writes nothing and posts nothing.
Register it with ``kernel.services.override(AssetService, FakeAssetService())`` in a consumer's tests.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from assets.interface import (
    AssetService,
    CapitalizedAsset,
    DisposalInvoiceRequest,
    DisposalInvoicing,
    InventoryCapitalization,
    InvoiceLineCapitalization,
)
from kernel import ids
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork


@dataclass
class FakeAssetService:
    #: category_id → threshold; a cost below it is refused like the real service
    thresholds: dict[uuid.UUID, Decimal] = field(default_factory=dict)
    calls: list[object] = field(default_factory=list)

    def should_capitalize(
        self, uow: UnitOfWork, company_id: uuid.UUID, category_id: uuid.UUID, cost: Decimal, on_date: dt.date
    ) -> bool:
        limit = self.thresholds.get(category_id)
        return limit is None or cost >= limit

    def _ok(
        self, uow: UnitOfWork, company_id: uuid.UUID, category_id: uuid.UUID, cost: Decimal, day: dt.date
    ) -> CapitalizedAsset:
        if not self.should_capitalize(uow, company_id, category_id, cost, day):
            raise ValidationFailed(
                "below the capitalization threshold", pointer="/cost", reason="below_capitalization_threshold"
            )
        n = len(self.calls)
        return CapitalizedAsset(ids.new_id(), f"FAKE-AF-{n:06d}", "FIXED_ASSET_COST")

    def capitalize_from_invoice_line(self, uow: UnitOfWork, data: InvoiceLineCapitalization) -> CapitalizedAsset:
        result = self._ok(uow, data.company_id, data.category_id, data.cost, data.acquisition_date)
        self.calls.append(data)
        return result

    def capitalize_from_inventory(self, uow: UnitOfWork, data: InventoryCapitalization) -> CapitalizedAsset:
        result = self._ok(uow, data.company_id, data.category_id, data.cost, data.date)
        self.calls.append(data)
        return result


@dataclass
class FakeDisposalInvoicing:
    """Stands in for P10's sales invoicing of an asset sale (records the request, returns a reference)."""

    requests: list[DisposalInvoiceRequest] = field(default_factory=list)

    def create_disposal_invoice(self, uow: UnitOfWork, request: DisposalInvoiceRequest) -> DocumentRef:
        self.requests.append(request)
        return DocumentRef("sales.invoice", ids.new_id(), f"FAKE-FV-{len(self.requests):06d}")


def _conforms() -> None:  # static conformance check (mypy): fakes implement the Protocols
    _a: AssetService = FakeAssetService()
    _b: DisposalInvoicing = FakeDisposalInvoicing()
