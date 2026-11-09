"""NotificationService (owner P07): in-app rows + e-mail through the outbox.

``notify`` only writes inside the caller's unit of work; e-mail is delivered
by the outbox consumer to the local capture (DEV: no real e-mail). Listing and
badge counts are company-scoped and policy-filtered (a notification whose
permission the user lost is not shown nor counted).
"""

from __future__ import annotations

import uuid
from typing import Any

from kernel import clock, ids
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.notifications import NotificationRequest
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from workflow.notifications.templates import TEMPLATES, render
from workflow.policy import policy

EMAIL_EVENT = "workflow.notification.email_requested.v1"


class DbNotificationService:
    def notify(self, uow: UnitOfWork, request: NotificationRequest) -> uuid.UUID:
        from workflow.models import Notification, NotificationPreference

        template = TEMPLATES.get(request.template_code)
        if template is None:
            raise ValidationFailed(f"unknown notification template {request.template_code}")
        company_id = request.company_id
        if company_id is None and len(uow.ctx.company_ids) == 1:
            company_id = next(iter(uow.ctx.company_ids))
        if company_id is None:
            raise ValidationFailed("notifications are company-scoped")
        pref = (
            NotificationPreference.objects.filter(
                tenant_id=uow.ctx.tenant_id, principal_id=request.recipient_id, template_code__in=[template.code, "*"]
            )
            .order_by("-template_code")
            .first()
        )
        channels = list(pref.channels) if pref is not None and pref.channels else list(request.channels)
        if "in_app" not in channels:
            channels.insert(0, "in_app")  # the bell always shows it
        title, body = render(template, dict(request.data), hide=set())
        first_id: uuid.UUID | None = None
        for channel in channels:
            if channel not in ("in_app", "email"):
                continue
            hide = set(template.sensitive) if channel == "email" else set()
            _, channel_body = render(template, dict(request.data), hide=hide)
            n = Notification.objects.create(
                id=ids.new_id(), tenant_id=uow.ctx.tenant_id, company_id=company_id, recipient_id=request.recipient_id,
                template_code=template.code, channel=channel, title=title, body=channel_body,
                data={k: str(v) for k, v in request.data.items() if k not in hide}, link=template.link,
                permission=template.permission,
                delivered_at=clock.now() if channel == "in_app" else None,
            )  # fmt: skip
            first_id = first_id or n.id
            if channel == "email" and (pref is None or pref.digest == "none"):
                uow.emit(EMAIL_EVENT, {"notification_id": str(n.id)}, company_id=company_id)
        if first_id is None:
            raise ValidationFailed("no deliverable channel")
        return first_id

    # ------------------------------------------------------------------ reading
    def _visible(self, ctx: TenantContext, company_id: uuid.UUID) -> Any:
        from workflow.models import Notification

        ctx.require_company(company_id)
        qs = Notification.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, recipient_id=ctx.principal_id, channel="in_app"
        )
        perms = set(qs.exclude(permission="").values_list("permission", flat=True).distinct())
        allowed = {p for p in perms if policy().authorize(ctx, p).allowed}
        return qs.filter(permission__in=[*allowed, ""])

    def list(self, ctx: TenantContext, company_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50) -> list[Any]:
        qs = self._visible(ctx, company_id)
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        return list(qs.order_by("-created_at")[: max(1, min(limit, 500))])

    def badge_count(self, ctx: TenantContext, company_id: uuid.UUID) -> int:
        return int(self._visible(ctx, company_id).filter(read_at__isnull=True).count())

    def mark_read(self, uow: UnitOfWork, notification_id: uuid.UUID) -> Any:
        from workflow.models import Notification

        n = Notification.objects.filter(
            tenant_id=uow.ctx.tenant_id, recipient_id=uow.ctx.principal_id, id=notification_id
        ).first()
        if n is None:
            raise NotFound("notification not found")
        if n.read_at is None:
            n.read_at = clock.now()
            n.save(update_fields=["read_at"])
        return n


class LocalMailCapture:
    """DEV e-mail adapter: messages are kept in memory (and Mailpit when the stack runs)."""

    outbox: list[dict[str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append({"to": to, "subject": subject, "body": body})


def deliver_email(notification_id: uuid.UUID, recipient_address: str, adapter: LocalMailCapture | None = None) -> None:
    """Outbox consumer step (outside any transaction)."""
    from workflow.models import Notification

    n = Notification.objects.filter(id=notification_id, channel="email").first()
    if n is None or n.delivered_at is not None:
        return
    (adapter or LocalMailCapture()).send(recipient_address, n.title, n.body)
    Notification.objects.filter(id=n.id, delivered_at__isnull=True).update(delivered_at=clock.now())
