"""Stable error codes of the einvoice module (``contracts/errors.yaml`` › einvoice)."""

from __future__ import annotations

from kernel.errors import DomainError


class FiscalRejected(DomainError):
    code, status, title = "fiscal_rejected", 422, "Fiscal document rejected (simulator in dev)"


class NumberingRangeMissing(DomainError):
    code, status, title = "numbering_range_missing", 422, "No authorized numbering range"


class ProviderNotConfigured(DomainError):
    code, status, title = "provider_not_configured", 422, "Technology provider not configured"


class ProviderContractTestFailed(DomainError):
    code, status, title = "provider_contract_test_failed", 422, "Provider contract test failed"


class CertificateMissing(DomainError):
    code, status, title = "certificate_missing", 422, "Signing certificate missing (EXTERNAL)"


class InvalidTransition(DomainError):
    code, status, title = "validation_failed", 409, "Invalid fiscal-document state transition"


class ProductionRefused(DomainError):
    """Production transmission or a simulator in production (C1-11, EG-1..EG-3)."""

    code, status, title = "provider_not_configured", 422, "Production transmission refused (external gate missing)"


class TransientTransmissionError(Exception):
    """Retryable transport failure raised by adapters (timeouts, 5xx, unavailable service)."""


class AdapterNotSupported(DomainError):
    code, status, title = "provider_not_configured", 422, "Operation not supported by the adapter"


class InvariantViolated(RuntimeError):
    """An internal invariant does not hold (programming error); explicit, unlike ``assert`` under ``python -O``."""


def present[T](value: T | None, what: str) -> T:
    if value is None:
        raise InvariantViolated(f"{what} is missing")
    return value


def of_type[T](value: object, cls: type[T]) -> T:
    if not isinstance(value, cls):
        raise InvariantViolated(f"expected {cls.__name__}, got {type(value).__name__}")
    return value
