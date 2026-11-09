"""Shortcut used by the workflow services to notify inside the current unit of work."""

from __future__ import annotations

import uuid
from typing import Any

from kernel import services
from kernel.interfaces.notifications import NotificationRequest, NotificationService
from kernel.uow import UnitOfWork


def notify(uow: UnitOfWork, recipient: uuid.UUID, company_id: uuid.UUID, template: str, data: dict[str, Any]) -> uuid.UUID:
    svc = services.optional(NotificationService)
    if svc is None:
        from workflow.notifications.service import DbNotificationService

        svc = DbNotificationService()
    return svc.notify(uow, NotificationRequest(recipient_id=recipient, template_code=template, data=data, company_id=company_id))
