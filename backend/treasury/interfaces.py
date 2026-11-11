"""Frozen treasury contract (P16a core) for P16b (intercompany / consolidation), P10/P11 aliases, portals, P19/P20.

``CONTRACT_VERSION`` follows semver: P16a only adds to this module; a breaking change needs a CCR.

* **Vouchers** (``treasury.vouchers.service``): ``create_voucher`` / ``update_voucher`` / ``submit`` / ``approve`` /
  ``reject`` / ``simulate`` / ``post`` / ``reverse`` — each runs inside the caller's unit of work and authorizes
  through ``PolicyService`` exactly like the public API (``treasury.receipt.*`` / ``treasury.disbursement.*``).
  P16b intercompany payments/netting create CE/RC vouchers with these functions (a ``source`` ref links them).
* **Payment batches** (``treasury.batches.service``): ``create_batch`` / ``submit_batch`` / ``approve_batch`` /
  ``generate_file`` / ``execute_batch`` (one CE per payee) / ``mark_sent``.
* **AP open items** (:class:`ApOpenItems`): the AP counterpart of ``kernel.interfaces.ArOpenItems``; implemented
  over P11's ``purchasing_ap_open_item`` by ``treasury.vouchers.ap.PurchasingApOpenItems`` until P11 adopts it
  (CCR P16a-1). Same rules: row locks in ascending id, never over-applies (``treasury_overapplication``).
* **Payment status** (:class:`TreasuryPaymentStatus`): ``kernel.interfaces.treasury.PaymentStatus`` for portals.
* **Events** (``contracts/events/treasury/``): ``treasury.receipt.posted.v1``, ``treasury.receipt.reversed.v1``,
  ``treasury.disbursement.posted.v1``, ``treasury.disbursement.reversed.v1``, ``treasury.statement_line.matched.v1``.
* **Permissions** (``contracts/permissions/catalog.yaml``): ``treasury.<resource>.<action>``; the AR application also
  needs P10's ``sales.receipt.post`` (checked by ``ArOpenItems.apply``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from kernel import services
from kernel.errors import NotFound
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.treasury import PaymentStatusInfo, VoucherState
from kernel.tenancy import require_current
from kernel.uow import UnitOfWork

CONTRACT_VERSION = "1.0.0"

RECEIPT_TYPE = "treasury.receipt"
DISBURSEMENT_TYPE = "treasury.disbursement"
BATCH_TYPE = "treasury.payment_batch"
#: Approval policy codes (P07 ``workflow_approval_policy``; created by the P21 seed / tenant admin).
POLICY_RC = "TES-RC"
POLICY_CE = "TES-CE"
POLICY_DUAL = "TES-DUAL"
#: Label of every simulated bank file (no live bank; formats pending EG-5).
BANK_FILE_LABEL = "FIXTURE_NOT_CERTIFIED"
#: Party-scoped portal grants accepted by :class:`TreasuryPaymentStatus` (P10b / P11b portals).
PORTAL_RECEIPT = "portal.customer_receipt.read"
PORTAL_PAYMENT = "portal.supplier_payment.read"


@dataclass(frozen=True, slots=True)
class ApOpenItem:
    id: uuid.UUID
    party_id: uuid.UUID  # P10 party of the vendor (supplier.party_id, else the supplier id)
    supplier_id: uuid.UUID
    document: DocumentRef
    due_date: dt.date
    currency: str
    open_amount: Decimal
    fx_rate: Decimal


@dataclass(frozen=True, slots=True)
class ApApplied:
    open_item_id: uuid.UUID
    amount: Decimal
    amount_cop: Decimal  # at the item's historical rate (GL AP = Σ balance_cop)


@runtime_checkable
class ApOpenItems(Protocol):
    def open_items(self, company_id: uuid.UUID, party_id: uuid.UUID) -> list[ApOpenItem]: ...

    def get(self, company_id: uuid.UUID, open_item_id: uuid.UUID) -> ApOpenItem | None: ...

    def apply(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        voucher: DocumentRef,
        applications: list[tuple[uuid.UUID, Decimal]],
    ) -> list[ApApplied]:
        """Reduce the open items under row locks (ascending id); ``treasury_overapplication`` past the balance."""
        ...

    def unapply(self, uow: UnitOfWork, company_id: uuid.UUID, voucher: DocumentRef) -> None:
        """Re-open every live application of ``voucher`` (reversal)."""
        ...


_STATE: dict[str, VoucherState] = {
    "DRAFT": "DRAFT",
    "SUBMITTED": "DRAFT",
    "REJECTED": "DRAFT",
    "CANCELLED": "DRAFT",
    "APPROVED": "APPROVED",
    "POSTED": "POSTED",
    "REVERSED": "REVERSED",
}


class TreasuryPaymentStatus:
    """``kernel.interfaces.treasury.PaymentStatus``: vouchers applied to an invoice/bill (policy-scoped)."""

    def _info(self, voucher: object) -> PaymentStatusInfo:
        from treasury.models import TreasuryVoucher

        assert isinstance(voucher, TreasuryVoucher)  # noqa: S101 - narrowing for mypy
        kind = RECEIPT_TYPE if voucher.voucher_type == TreasuryVoucher.Type.RC else DISBURSEMENT_TYPE
        return PaymentStatusInfo(
            voucher=DocumentRef(kind, voucher.id, voucher.number or None),
            state=_STATE[voucher.status],
            amount=voucher.total,
            currency=voucher.currency,
            posted_on=voucher.posting_date if voucher.posted else None,
        )

    def for_document(self, company_id: uuid.UUID, document: DocumentRef) -> list[PaymentStatusInfo]:
        from treasury.models import TreasuryVoucher
        from treasury.services.common import scoped

        ctx = require_current()
        ctx.require_company(company_id)
        receipt = document.type.startswith("sales.")
        read = "treasury.receipt.read" if receipt else "treasury.disbursement.read"
        portal = PORTAL_RECEIPT if receipt else PORTAL_PAYMENT
        policy = services.get(PolicyService)
        if policy.authorize(ctx, read).allowed:
            qs = scoped(ctx, read, TreasuryVoucher.objects.filter(company_id=company_id))
        else:
            # Portals (D-033): a dedicated, party-scoped grant; the portal caller passes only its own party's
            # documents, and only the voucher status projection (no party, lines or accounts) is returned.
            policy.require(ctx, portal)
            qs = TreasuryVoucher.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        rows = qs.filter(lines__open_item_document__id=str(document.id)).distinct().order_by("document_date", "id")
        return [self._info(v) for v in rows]

    def voucher(self, company_id: uuid.UUID, voucher_id: uuid.UUID) -> PaymentStatusInfo:
        from treasury.models import TreasuryVoucher
        from treasury.services.common import get_scoped

        ctx = require_current()
        ctx.require_company(company_id)
        row = TreasuryVoucher.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=voucher_id).first()
        if row is None:
            raise NotFound("not found")
        read = "treasury.receipt.read" if row.voucher_type == "RC" else "treasury.disbursement.read"
        return self._info(get_scoped(ctx, read, TreasuryVoucher.objects.filter(company_id=company_id), voucher_id))


__all__ = [
    "BANK_FILE_LABEL",
    "BATCH_TYPE",
    "CONTRACT_VERSION",
    "DISBURSEMENT_TYPE",
    "POLICY_CE",
    "POLICY_DUAL",
    "POLICY_RC",
    "RECEIPT_TYPE",
    "ApApplied",
    "ApOpenItem",
    "ApOpenItems",
    "TreasuryPaymentStatus",
]
