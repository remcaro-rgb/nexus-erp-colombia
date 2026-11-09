"""Domain exception hierarchy mapped to RFC 9457 ``application/problem+json``.

Every error carries a stable ``code``. The kernel codes are listed in
:data:`KERNEL_ERROR_CODES` and requested for ``contracts/errors.yaml`` via
``contracts/change-requests/P01-1.md``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.http import Http404
from rest_framework import exceptions as drf_exc
from rest_framework.response import Response

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://errors.nexus-erp.invalid/"


class DomainError(Exception):
    """Base for every expected business error."""

    code: ClassVar[str] = "domain_error"
    status: ClassVar[int] = 400
    title: ClassVar[str] = "Domain error"

    def __init__(self, detail: str = "", *, code: str | None = None, **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        if code is not None:
            # Module-specific code on a kernel class (e.g. Conflict(code="period_closed")).
            self.code = code  # type: ignore[misc]
        self.extra = extra

    def to_problem(self, trace_id: str | None = None, instance: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": PROBLEM_TYPE_BASE + self.code,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            body["instance"] = instance
        if trace_id:
            body["trace_id"] = trace_id
        if self.extra:
            body["errors"] = self.extra
        return body


class ValidationFailed(DomainError):
    code, status, title = "validation_failed", 422, "Validation failed"


class NotFound(DomainError):
    code, status, title = "not_found", 404, "Not found"


class PermissionDenied(DomainError):
    code, status, title = "permission_denied", 403, "Permission denied"


class NotAuthenticated(DomainError):
    code, status, title = "not_authenticated", 401, "Authentication required"


class Conflict(DomainError):
    code, status, title = "conflict", 409, "Conflict"


class ConcurrencyConflict(Conflict):
    code, title = "concurrency_conflict", "The resource was modified concurrently"


class PreconditionFailed(DomainError):
    code, status, title = "precondition_failed", 412, "Precondition failed (If-Match)"


class PreconditionRequired(DomainError):
    code, status, title = "precondition_required", 428, "If-Match header required"


class IdempotencyKeyReused(DomainError):
    code, status, title = (
        "idempotency_key_reused",
        422,
        "Idempotency-Key reused with a different request",
    )


class IdempotencyKeyInvalid(DomainError):
    code, status, title = "idempotency_key_invalid", 400, "Invalid Idempotency-Key"


class TenancyNotBound(DomainError):
    code, status, title = "tenancy_not_bound", 500, "No tenant context bound"


class UnitOfWorkRequired(DomainError):
    code, status, title = "unit_of_work_required", 500, "Operation requires an active unit of work"


class ExternalIOForbidden(DomainError):
    code, status, title = (
        "external_io_in_transaction",
        500,
        "External I/O is forbidden inside a unit of work (use the outbox)",
    )


class ImmutableRecord(DomainError):
    code, status, title = "immutable_record", 409, "Posted records are immutable; use a reversal"


class SequenceNotFound(DomainError):
    code, status, title = "sequence_not_found", 422, "No numbering sequence configured"


class SequenceNotValid(DomainError):
    code, status, title = "sequence_not_valid", 422, "Numbering sequence not valid on this date"


class SequenceExhausted(DomainError):
    code, status, title = "sequence_exhausted", 409, "Numbering range exhausted"


class ParameterUnknown(DomainError):
    code, status, title = "parameter_unknown", 422, "Regulatory parameter unknown"


class ParameterNotVerified(DomainError):
    code, status, title = (
        "parameter_not_verified",
        422,
        "Regulatory parameter lacks a legal-review stamp",
    )


class FeatureDisabled(DomainError):
    code, status, title = "feature_disabled", 403, "Feature disabled"


class RetryExhausted(DomainError):
    code, status, title = "retry_exhausted", 503, "Transaction retries exhausted"


class ServiceUnavailable(DomainError):
    code, status, title = "service_unavailable", 503, "Service not available"


def _all_subclasses(cls: type[DomainError]) -> list[type[DomainError]]:
    out: list[type[DomainError]] = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out.extend(_all_subclasses(sub))
    return out


def kernel_error_codes() -> dict[str, int]:
    """``code -> HTTP status`` for every kernel error class."""
    classes: list[type[DomainError]] = [DomainError, *_all_subclasses(DomainError)]
    return {c.code: c.status for c in classes if c.__module__ == __name__}


KERNEL_ERROR_CODES = kernel_error_codes()


_DRF_MAP: dict[type[drf_exc.APIException], tuple[str, str]] = {
    drf_exc.NotAuthenticated: ("not_authenticated", "Authentication required"),
    drf_exc.AuthenticationFailed: ("not_authenticated", "Authentication required"),
    drf_exc.PermissionDenied: ("permission_denied", "Permission denied"),
    drf_exc.NotFound: ("not_found", "Not found"),
    drf_exc.ValidationError: ("validation_failed", "Validation failed"),
    drf_exc.ParseError: ("validation_failed", "Malformed request"),
    drf_exc.MethodNotAllowed: ("method_not_allowed", "Method not allowed"),
    drf_exc.Throttled: ("throttled", "Too many requests"),
}


def drf_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF ``EXCEPTION_HANDLER``: every error leaves as problem+json."""
    request = context.get("request")
    instance = request.path if request is not None else None
    trace_id = getattr(request, "trace_id", None) if request is not None else None
    if isinstance(exc, DomainError):
        return Response(
            exc.to_problem(trace_id, instance),
            status=exc.status,
            content_type=PROBLEM_CONTENT_TYPE,
        )
    if isinstance(exc, Http404):
        exc = drf_exc.NotFound()
    if isinstance(exc, drf_exc.APIException):
        code, title = ("api_error", "Request failed")
        for klass, mapped in _DRF_MAP.items():
            if isinstance(exc, klass):
                code, title = mapped
                break
        body: dict[str, Any] = {
            "type": PROBLEM_TYPE_BASE + code,
            "title": title,
            "status": exc.status_code,
            "code": code,
        }
        if instance:
            body["instance"] = instance
        if trace_id:
            body["trace_id"] = trace_id
        if isinstance(exc, drf_exc.ValidationError):
            body["errors"] = exc.detail
        else:
            body["detail"] = str(exc.detail)
        return Response(body, status=exc.status_code, content_type=PROBLEM_CONTENT_TYPE)
    return None
