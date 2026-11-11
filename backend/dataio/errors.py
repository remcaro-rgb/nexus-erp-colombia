"""Stable error codes of the dataio module (contracts/errors.yaml › dataio)."""

from __future__ import annotations

from typing import Any

from kernel.errors import Conflict, DomainError, NotFound, PermissionDenied, ValidationFailed


class ImportValidationFailed(DomainError):
    """422 with row-level errors; nothing was applied."""

    code, status, title = "import_validation_failed", 422, "Importación con errores por fila"

    def __init__(self, detail: str, rows: list[dict[str, Any]]) -> None:
        super().__init__(detail, rows=rows[:500], error_count=len(rows))
        self.rows = rows


class ImportDuplicateBatch(Conflict):
    code, title = "import_duplicate_batch", "Lote ya importado"


class ImportFileRejected(DomainError):
    code, status, title = "import_file_rejected", 422, "Archivo rechazado"


class TemplateUnknown(NotFound):
    code, title = "template_unknown", "Plantilla desconocida"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Acción no permitida en el estado actual"


class PolicyDenied(PermissionDenied):
    code, title = "policy_denied", "Denegado por la política de acceso"


class FilterInvalid(ValidationFailed):
    code, status, title = "filter_invalid", 400, "Filtro inválido"


class RowError(Exception):
    """One row failed (collected into ``import_validation_failed``); never escapes the engine."""

    def __init__(self, code: str, message: str, column: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.column = column
