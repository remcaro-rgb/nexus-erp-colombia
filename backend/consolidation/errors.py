"""Stable error codes of intercompany + consolidation (contracts/errors.yaml)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied, ValidationFailed


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Acción no permitida en el estado actual"


class DocumentImmutable(Conflict):
    code, title = "document_immutable", "Documento contabilizado inmutable (use reverso)"


class PeriodClosed(DomainError):
    code, status, title = "period_closed", 422, "Período cerrado"


class ReasonRequired(DomainError):
    code, status, title = "reason_required", 422, "Se requiere motivo"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Violación de segregación de funciones"


class PolicyDenied(PermissionDenied):
    code, title = "policy_denied", "Denied by access policy"


class FxRateMissing(DomainError):
    code, status, title = "fx_rate_missing", 422, "Falta tasa de cambio"


class IntercompanyMismatch(DomainError):
    code, status, title = "intercompany_mismatch", 422, "Diferencia intercompañía"


class FilterInvalid(ValidationFailed):
    code, status, title = "filter_invalid", 400, "Invalid filter"


def invalid(detail: str, pointer: str, reason: str) -> ValidationFailed:
    return ValidationFailed(detail, pointer=pointer, reason=reason)
