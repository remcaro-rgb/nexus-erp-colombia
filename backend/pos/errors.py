"""Stable POS error codes (contracts/errors.yaml + CCR P12-1)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError


class SessionNotOpen(Conflict):
    code, title = "pos_session_not_open", "The cash session is not open"


class SessionAlreadyOpen(Conflict):
    code, title = "pos_session_already_open", "The terminal already has an open cash session"


class PriceChanged(Conflict):
    """Server recomputation differs from the client draft: the cashier must confirm (ADR-015 reconnect)."""

    code, title = "pos_price_changed", "Prices or taxes changed since the draft was built; confirm the new total"


class TenderMismatch(DomainError):
    code, status, title = "pos_tender_mismatch", 422, "Tenders do not settle the ticket total"


class TenderNotAllowed(DomainError):
    code, status, title = "pos_tender_not_allowed", 422, "Tender type not allowed at this POS"


class ReturnExceedsSold(DomainError):
    code, status, title = "pos_return_exceeds_sold", 422, "Return quantity exceeds the quantity sold"


class OfflineFiscalSellingRefused(DomainError):
    code, status, title = "pos_offline_fiscal_selling_refused", 409, "Offline fiscal selling is disabled (ADR-015)"
