"""Stable error codes of F-INV-REQ / F-PORTAL-SUP (``contracts/errors.yaml``)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied, ValidationFailed


class RequisitionNotApproved(DomainError):
    code, status, title = "requisition_not_approved", 422, "Requisition not approved"


class SourcingOverallocation(DomainError):
    code, status, title = "sourcing_overallocation", 422, "Sourcing over-allocation"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Action not allowed in the current state"


class ReasonRequired(ValidationFailed):
    code, title = "reason_required", "Reason required"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Segregation of duties violation"


class PortalRfqClosed(DomainError):
    code, status, title = "portal_rfq_closed", 422, "RFQ closed"


class PortalProfileChangePending(Conflict):
    code, title = "portal_profile_change_pending", "Profile change already pending"


#: warning code (HTTP 200) when D-011 forbids an automatic draft and a buyer task is created instead
AUTO_PO_LIMIT_EXCEEDED = "auto_po_limit_exceeded"
#: D-011: no approved award / price agreement of at most 90 days → buyer task
VENDOR_PRICE_MISSING = "vendor_price_missing"
