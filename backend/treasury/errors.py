"""Stable treasury error codes (contracts/errors.yaml §treasury, FEATURES-ADDENDUM F-TRE-RC / F-TRE-CE)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied


class TreasuryPeriodClosed(DomainError):
    code, status, title = "treasury_period_closed", 422, "Treasury period closed"


class TreasuryOverapplication(DomainError):
    code, status, title = "treasury_overapplication", 422, "Application exceeds open item balance"


class TreasuryOpenItemMismatchParty(DomainError):
    code, status, title = "treasury_open_item_mismatch_party", 422, "Open item belongs to another party"


class TreasuryCurrencyMismatch(DomainError):
    code, status, title = "treasury_currency_mismatch", 422, "Currency mismatch"


class TreasuryApprovalRequired(DomainError):
    code, status, title = "treasury_approval_required", 422, "Treasury approval required"


class TreasuryInsufficientFunds(DomainError):
    """Blocking form of ``treasury_insufficient_funds_warning`` (the fund forbids overdraft)."""

    code, status, title = "treasury_insufficient_funds_warning", 422, "Insufficient funds"


class TaxParameterUnknown(DomainError):
    code, status, title = "tax_parameter_unknown", 422, "Tax parameter unknown"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Invalid state transition"


class DocumentImmutable(Conflict):
    code, title = "document_immutable", "Posted documents are immutable"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Segregation of duties violation"


class StatementLineAlreadyMatched(Conflict):
    code, title = "statement_line_already_matched", "Statement line already matched"


INSUFFICIENT_FUNDS_WARNING = "treasury_insufficient_funds_warning"
