"""Stable purchasing error codes (contracts/errors.yaml, F-PUR-CYCLE)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied, ValidationFailed


class RfqDeadlinePassed(DomainError):
    code, status, title = "rfq_deadline_passed", 422, "RFQ deadline passed"


class QuoteCurrencyRateMissing(DomainError):
    code, status, title = "quote_currency_rate_missing", 422, "Quote currency rate missing"


class AwardReasonRequired(DomainError):
    code, status, title = "award_reason_required", 422, "Award reason required"


class AwardNotApproved(DomainError):
    code, status, title = "award_not_approved", 422, "Award not approved"


class ReceiptOverTolerance(DomainError):
    code, status, title = "receipt_over_tolerance", 422, "Receipt over tolerance"


class ThreeWayMismatch(DomainError):
    code, status, title = "three_way_mismatch", 422, "Three-way match mismatch"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Action not allowed in the current state"


class ReasonRequired(ValidationFailed):
    code, title = "reason_required", "Reason required"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Segregation of duties violation"


class DocumentImmutable(Conflict):
    code, title = "document_immutable", "Posted document is immutable (use reversal)"


#: Warning code (HTTP 200/201, never blocks): a quote received after the deadline is stored LATE (D-010).
QUOTE_LATE_WARNING = "quote_late"
