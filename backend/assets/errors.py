"""Stable error codes of the fixed-assets module (contracts/errors.yaml)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, PermissionDenied, ValidationFailed


class AssetAlreadyDisposed(Conflict):
    code, title = "asset_already_disposed", "Activo ya dado de baja"


class DepreciationRunExists(Conflict):
    code, title = "depreciation_run_exists", "Ya existe depreciación del período"


class DocumentImmutable(Conflict):
    code, title = "document_immutable", "Documento contabilizado inmutable (use reverso)"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Acción no permitida en el estado actual"


class ReasonRequired(DomainError):
    code, status, title = "reason_required", 422, "Se requiere motivo"


class PeriodClosed(DomainError):
    code, status, title = "period_closed", 422, "Período cerrado"


class SodViolation(PermissionDenied):
    code, title = "sod_violation", "Violación de segregación de funciones"


def invalid(detail: str, pointer: str, reason: str) -> ValidationFailed:
    return ValidationFailed(detail, pointer=pointer, reason=reason)
