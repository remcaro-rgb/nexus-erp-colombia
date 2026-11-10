"""Notifications: badge count = visible notifications; policy-filtered; e-mail via outbox without sensitive data."""

from __future__ import annotations

import pytest

from kernel.interfaces.notifications import NotificationRequest
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from workflow.models import Notification, NotificationPreference
from workflow.notifications.service import DbNotificationService

pytestmark = pytest.mark.django_db


def _send(ctx, recipient, template, data, company, channels=("in_app",)):  # type: ignore[no-untyped-def]
    req = NotificationRequest(recipient, template, data, channels, company)
    return run_in_unit_of_work(lambda uow: DbNotificationService().notify(uow, req), ctx)


def test_badge_count_equals_visible_notifications(user_factory, company, grants) -> None:  # type: ignore[no-untyped-def]
    sender = user_factory()
    user = user_factory("workflow.approval.read")
    svc = DbNotificationService()
    _send(sender, user.principal_id, "aprobacion_pendiente", {"documento": "OC 1"}, company)
    _send(sender, user.principal_id, "aprobacion_pendiente", {"documento": "OC 2"}, company)
    _send(sender, user.principal_id, "tarea_fallida", {"tarea": "x", "motivo": "y"}, company)  # needs scheduler perm
    visible = svc.list(user, company)
    assert len(visible) == 2 == svc.badge_count(user, company)
    assert visible[0].title == "Aprobación pendiente" and "OC" in visible[0].body
    run_in_unit_of_work(lambda uow: svc.mark_read(uow, visible[0].id), user)
    assert svc.badge_count(user, company) == 1
    grants.revoke(user.principal_id, "workflow.approval.read")
    assert svc.badge_count(user, company) == 0 and svc.list(user, company) == []


def test_email_goes_through_outbox_without_sensitive_data(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.models import OutboxMessage

    sender = user_factory()
    user = user_factory("workflow.dsr_request.read")
    with bound(user):  # workflow_notification_preference has forced RLS
        NotificationPreference.objects.create(
            tenant_id=user.tenant_id, principal_id=user.principal_id, template_code="*", channels=["in_app", "email"]
        )
    _send(
        sender,
        user.principal_id,
        "dsr_vencimiento",
        {"radicado": "DSR-1", "fecha": "2026-11-24", "titular": "Ana Sintética"},
        company,
    )
    email = Notification.objects.get(recipient_id=user.principal_id, channel="email")
    assert "Ana" not in email.body and "titular" not in email.data
    assert OutboxMessage.objects.filter(event_type="workflow.notification.email_requested.v1").exists()


def test_notifications_are_company_scoped(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    from kernel import ids
    from kernel.errors import ValidationFailed

    sender = user_factory()
    user = user_factory("workflow.approval.read")
    _send(sender, user.principal_id, "aprobacion_pendiente", {"documento": "OC 1"}, company)
    with pytest.raises(ValidationFailed):
        DbNotificationService().list(user, ids.new_id())
