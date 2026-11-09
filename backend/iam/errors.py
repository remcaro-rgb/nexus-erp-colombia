"""IAM error codes (``contracts/errors.yaml``, module iam/kernel)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, NotAuthenticated, PermissionDenied, ValidationFailed


class Unauthenticated(NotAuthenticated):
    code, status, title = "unauthenticated", 401, "Authentication required"


class PolicyDenied(PermissionDenied):
    code, status, title = "policy_denied", 403, "Denied by access policy"


class CompanyNotAllowed(PermissionDenied):
    code, status, title = "company_not_allowed", 403, "Company not allowed for the principal"


class FieldForbidden(PermissionDenied):
    code, status, title = "field_forbidden", 403, "Field not writable for this principal"


class SodViolation(PermissionDenied):
    code, status, title = "sod_violation", 403, "Segregation of duties violation"


class FilterInvalid(DomainError):
    code, status, title = "filter_invalid", 400, "Invalid filter, sort or fields"


class FilterNotAllowed(DomainError):
    """Filter/sort on a field the principal cannot read (prevents inference)."""

    code, status, title = "filter_not_allowed", 400, "Filter on a non-readable field"


class IdempotencyKeyRequired(DomainError):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


class BranchCodeDuplicate(ValidationFailed):
    code, title = "branch_code_duplicate", "Branch code duplicate in company"


class BranchCrossCompany(ValidationFailed):
    code, title = "branch_cross_company", "Branch belongs to another company"


class BranchInUse(Conflict):
    code, title = "branch_in_use", "Branch in use"


class BranchHeadOfficeRequired(ValidationFailed):
    code, title = "branch_head_office_required", "Exactly one head office required"


class ExternalAccountScopeViolation(PermissionDenied):
    code, title = "external_account_scope_violation", "Outside the external account scope"


class InvitationExpired(DomainError):
    code, status, title = "invitation_expired", 410, "Invitation expired"


class ExternalRoleForbidden(PermissionDenied):
    code, title = "external_role_forbidden", "Internal role cannot be granted to external user"


class PortalKindMismatch(PermissionDenied):
    code, title = "portal_kind_mismatch", "Portal kind mismatch"


class RateLimited(DomainError):
    code, status, title = "rate_limited", 429, "Too many requests"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Action not allowed in the current state"
