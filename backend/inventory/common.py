"""Small shared helpers: audit, events, id parsing, quantity formatting."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.uow import UnitOfWork


def audit(uow: UnitOfWork, action: str, resource_type: str, obj: Any, changes: dict[str, Any] | None = None) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, resource_type, obj.id, getattr(obj, "company_id", None), changes or {}))


def as_uuid(value: Any, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise ValidationFailed(f"{field} must be a UUID", pointer=f"/{field}", reason="format") from None


def opt_uuid(value: Any, field: str) -> uuid.UUID | None:
    return None if value in (None, "") else as_uuid(value, field)


def dec(value: Decimal | None) -> str | None:
    """Decimal → canonical API string (never a JSON number)."""
    if value is None:
        return None
    return format(value, "f")


def get_or_404(qs: Any, pk: Any) -> Any:
    obj = qs.filter(pk=pk).first()
    if obj is None:
        raise NotFound("not found")
    return obj
