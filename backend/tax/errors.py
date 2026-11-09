"""Stable error codes of the tax module (contracts/errors.yaml, module ``tax``)."""

from __future__ import annotations

from kernel.errors import Conflict, DomainError, ParameterUnknown, ValidationFailed


class TaxParameterUnknown(ParameterUnknown):
    """A tax parameter is missing or UNKNOWN for the date: fail closed (C1-9, D-029)."""

    code, status, title = "tax_parameter_unknown", 422, "Parámetro tributario desconocido"


class ParameterSourceRequired(ValidationFailed):
    code, status, title = "parameter_source_required", 422, "Parámetro sin fuente"


class TaxCodeInactive(ValidationFailed):
    code, status, title = "tax_code_inactive", 422, "Código de impuesto inactivo"


class ExogenaPackInvalid(ValidationFailed):
    code, status, title = "exogena_pack_invalid", 422, "Paquete de formatos inválido"


class SodViolation(DomainError):
    """Segregation of duties: the proposer can never approve (two-person rule)."""

    code, status, title = "sod_violation", 403, "Violación de segregación de funciones"


def invalid_transition(detail: str) -> Conflict:
    return Conflict(detail, code="invalid_state_transition")
