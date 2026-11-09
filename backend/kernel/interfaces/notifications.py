"""NotificationService (owner P07). ``Notifications`` is the FEATURES-ADDENDUM name.

Contract: ``notify`` only enqueues inside the caller's UoW (outbox); delivery
happens in a worker; e-mail goes to local capture in DEV (no real e-mail).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from kernel.uow import UnitOfWork

Channel = Literal["in_app", "email"]


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    recipient_id: uuid.UUID
    template_code: str
    data: dict[str, Any] = field(default_factory=dict)
    channels: tuple[Channel, ...] = ("in_app",)
    company_id: uuid.UUID | None = None


@runtime_checkable
class NotificationService(Protocol):
    def notify(self, uow: UnitOfWork, request: NotificationRequest) -> uuid.UUID: ...


Notifications = NotificationService
