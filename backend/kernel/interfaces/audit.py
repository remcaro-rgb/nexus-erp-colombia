"""AuditService (owner P07): hash-chained, append-only audit log.

Contract: ``record`` is called inside the UoW of the change it describes, so
the audit row commits or rolls back with it; never stores secrets or
sensitive PII values (only field names / masked values).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    company_id: uuid.UUID | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@runtime_checkable
class AuditService(Protocol):
    def record(self, uow: UnitOfWork, entry: AuditEntry) -> uuid.UUID:
        """Append an audit record; returns its id."""
        ...

    def verify_chain(self, tenant_id: uuid.UUID) -> bool:
        """Recompute the hash chain; False when tampering is detected."""
        ...
