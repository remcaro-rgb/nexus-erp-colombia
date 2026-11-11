"""Stable sales error codes (contracts/errors.yaml, P10)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied, ValidationFailed


class CreditLimitExceeded(DomainError):
    code, status, title = "credit_limit_exceeded", 422, "Credit limit exceeded"


class PartyDuplicate(DomainError):
    code, status, title = "party_duplicate", 422, "Party duplicate (same NIT)"


class PriceNotFound(DomainError):
    code, status, title = "price_not_found", 422, "Price not found"


class PricingFormulaMissingInput(DomainError):
    code, status, title = "pricing_formula_missing_input", 422, "Pricing formula input missing"


class PricingOverrideRequiresApproval(DomainError):
    code, status, title = "pricing_override_requires_approval", 422, "Price override requires approval (D-024)"


class PricingPostedDocumentImmutable(DomainError):
    code, status, title = "pricing_posted_document_immutable", 409, "Posted document cannot be repriced"


class NumberingRangeMissing(DomainError):
    code, status, title = "numbering_range_missing", 422, "No authorized numbering range"


class StockInsufficient(DomainError):
    code, status, title = "stock_insufficient", 422, "Insufficient stock"


class TreasuryOverapplication(DomainError):
    code, status, title = "treasury_overapplication", 422, "Application exceeds open item balance"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Action not allowed in the current state"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Segregation of duties violation"


__all__ = [
    "CreditLimitExceeded",
    "InvalidStateTransition",
    "NumberingRangeMissing",
    "PartyDuplicate",
    "PriceNotFound",
    "PricingFormulaMissingInput",
    "PricingOverrideRequiresApproval",
    "PricingPostedDocumentImmutable",
    "SodViolation",
    "StockInsufficient",
    "TreasuryOverapplication",
    "ValidationFailed",
]
