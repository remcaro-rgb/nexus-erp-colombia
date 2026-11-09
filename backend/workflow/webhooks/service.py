"""Outbound webhooks (owner P07).

* Signature header ``X-Nexus-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>``.
* Retries with exponential backoff (30 s · 2^n, capped at 6 h) up to
  :data:`MAX_ATTEMPTS`, then DEAD + ``workflow.webhook.delivery_failed.v1``;
  dead deliveries can be replayed.
* Payloads are filtered by the subscriber's service-account permissions:
  no ``<module>.event.read`` → not delivered; fields outside its field mask
  are removed.
* DEV-only: targets must be local/test hosts (no real endpoints, no SSRF to
  the internet). HTTP happens outside any unit of work.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

from kernel import clock, ids
from kernel.errors import NotFound, ValidationFailed
from kernel.events import to_jsonable
from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from workflow.audit.service import record_change
from workflow.policy import policy

MAX_ATTEMPTS = 8
BASE_DELAY = dt.timedelta(seconds=30)
MAX_DELAY = dt.timedelta(hours=6)
SIGNATURE_HEADER = "X-Nexus-Signature"
TOLERANCE_SECONDS = 300
DEV_HOST_SUFFIXES = (".localhost", ".test", ".invalid", ".local", ".example")
DEV_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "mailpit", "webhook-sink"}
ALWAYS_KEPT = {"id", "event_id", "event_type", "company_id", "status", "state"}


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, now: int, tolerance: int = TOLERANCE_SECONDS) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(now - ts) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, ts), f"t={ts},v1={parts.get('v1', '')}")


def backoff(attempts: int) -> dt.timedelta:
    return min(BASE_DELAY * (2 ** max(attempts - 1, 0)), MAX_DELAY)


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValidationFailed("webhook URL must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValidationFailed("credentials in webhook URLs are not allowed")
    if host not in DEV_HOSTS and not host.endswith(DEV_HOST_SUFFIXES):
        raise ValidationFailed(
            "DEV-only build: webhook targets must be local/test hosts", code="production_endpoint_refused"
        )
    return url


def matches(event_type: str, patterns: list[str]) -> bool:
    return any(p == event_type or p == "*" or (p.endswith(".*") and event_type.startswith(p[:-1])) for p in patterns)


class Transport(Protocol):
    def send(self, url: str, headers: Mapping[str, str], body: bytes) -> int: ...


class HttpxTransport:
    def send(self, url: str, headers: Mapping[str, str], body: bytes) -> int:
        import httpx

        validate_url(url)
        resp = httpx.post(url, content=body, headers=dict(headers), timeout=5.0, follow_redirects=False)
        return int(resp.status_code)


def service_account_context(sub: Any) -> TenantContext:
    return TenantContext(
        tenant_id=sub.tenant_id, company_ids=frozenset({sub.company_id}), principal_id=sub.service_account_id,
        principal_type=PrincipalType.SERVICE, channel=Channel.API,
    )  # fmt: skip


def filter_payload(sub: Any, envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    """The envelope as the service account may see it, or None when it may not see the event."""
    event_type = str(envelope["event_type"])
    module = event_type.split(".", 1)[0]
    ctx = service_account_context(sub)
    if not policy().authorize(ctx, f"{module}.event.read", {"event_type": event_type}).allowed:
        return None
    resource = event_type.rsplit(".", 1)[0]  # e.g. ledger.journal_entry.posted
    mask = policy().field_mask(ctx, resource)
    payload = envelope.get("payload") or {}
    visible = {k: v for k, v in payload.items() if k in mask.readable or k in ALWAYS_KEPT}
    out = {k: v for k, v in envelope.items() if k != "payload"}
    out["payload"] = visible
    return out


class WebhookService:
    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or HttpxTransport()

    def subscribe(self, ctx: TenantContext, company_id: uuid.UUID, url: str, event_types: list[str],
                  service_account_id: uuid.UUID, description: str = "",
                  idempotency_key: str | None = None) -> tuple[Any, str]:  # fmt: skip
        """Returns (subscription, secret). The secret is shown once and never serialized again."""
        result = run_in_unit_of_work(
            lambda uow: self.subscribe_in(uow, company_id, url, event_types, service_account_id, description), ctx,
            idempotency_key=idempotency_key,
        )  # fmt: skip
        return result  # type: ignore[return-value]

    def subscribe_in(self, uow: UnitOfWork, company_id: uuid.UUID, url: str, event_types: list[str],
                     service_account_id: uuid.UUID, description: str = "") -> tuple[Any, str]:  # fmt: skip
        from workflow.models import WebhookSubscription

        ctx = uow.ctx
        ctx.require_company(company_id)
        policy().require(ctx, "workflow.webhook.create", {"company_id": company_id})
        validate_url(url)
        if not event_types or not all(isinstance(e, str) and e for e in event_types):
            raise ValidationFailed("at least one event type is required")
        secret = secrets.token_hex(32)
        sub = WebhookSubscription.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, url=url, event_types=list(event_types), secret=secret,
            service_account_id=service_account_id, description=description[:255],
            created_by=ctx.principal_id, updated_by=ctx.principal_id,
        )  # fmt: skip
        record_change(
            "workflow.webhook.create",
            "workflow.webhook",
            sub.id,
            company_id=company_id,
            after={"url": url, "event_types": event_types},
            uow=uow,
        )
        return sub, secret

    def fan_out(self, uow: UnitOfWork, envelope: Mapping[str, Any]) -> int:
        """Outbox consumer: one delivery per matching subscription (idempotent per event)."""
        from workflow.models import WebhookDelivery, WebhookSubscription

        envelope = to_jsonable(dict(envelope))
        subs = WebhookSubscription.objects.filter(
            tenant_id=envelope["tenant_id"], company_id=envelope.get("company_id"), active=True
        )
        created = 0
        for sub in subs:
            if not matches(str(envelope["event_type"]), list(sub.event_types)):
                continue
            payload = filter_payload(sub, envelope)
            if payload is None:
                continue
            _, was_created = WebhookDelivery.objects.get_or_create(
                subscription=sub, event_id=envelope["event_id"],
                defaults={"tenant_id": sub.tenant_id, "event_type": envelope["event_type"], "payload": payload,
                          "next_attempt_at": clock.now()},
            )  # fmt: skip
            created += int(was_created)
        return created

    def attempt(self, delivery_id: uuid.UUID, *, now: dt.datetime | None = None) -> Any:
        """Send one delivery (outside any transaction) and record the outcome."""
        from workflow.models import WebhookDelivery

        now = now or clock.now()
        d = WebhookDelivery.objects.select_related("subscription").filter(id=delivery_id).first()
        if d is None:
            raise NotFound("delivery not found")
        if d.state in (WebhookDelivery.DELIVERED, WebhookDelivery.DEAD):
            return d
        sub = d.subscription
        body = json.dumps(d.payload, sort_keys=True, separators=(",", ":")).encode()
        ts = int(now.timestamp())
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(sub.secret, body, ts),
            "X-Nexus-Event": d.event_type,
            "X-Nexus-Delivery": str(d.id),
        }
        status: int | None = None
        error = ""
        try:
            status = self.transport.send(sub.url, headers, body)
        except Exception as exc:  # noqa: BLE001 - any transport failure is a retryable delivery error
            error = type(exc).__name__
        d.attempts += 1
        d.last_status = status
        d.last_error = error[:500]
        if status is not None and 200 <= status < 300:
            d.state, d.delivered_at, d.next_attempt_at = WebhookDelivery.DELIVERED, now, None
        elif d.attempts >= MAX_ATTEMPTS:
            d.state, d.next_attempt_at = WebhookDelivery.DEAD, None
        else:
            d.state, d.next_attempt_at = WebhookDelivery.RETRYING, now + backoff(d.attempts)
        d.save(update_fields=["attempts", "last_status", "last_error", "state", "delivered_at", "next_attempt_at"])
        if d.state == WebhookDelivery.DEAD:
            ctx = service_account_context(sub)

            def dead(uow: UnitOfWork) -> None:
                uow.emit(
                    "workflow.webhook.delivery_failed.v1",
                    {"webhook_id": str(sub.id), "event_id": str(d.event_id), "attempts": d.attempts},
                    company_id=sub.company_id,
                )
                from workflow.notifications.helpers import notify

                notify(
                    uow,
                    sub.created_by or sub.service_account_id,
                    sub.company_id,
                    "webhook_fallido",
                    {"webhook": sub.description or str(sub.id), "evento": d.event_type},
                )

            run_in_unit_of_work(dead, ctx)
        return d

    def deliver_due(self, now: dt.datetime | None = None, limit: int = 100) -> int:
        from workflow.models import WebhookDelivery

        now = now or clock.now()
        due = WebhookDelivery.objects.filter(
            state__in=[WebhookDelivery.PENDING, WebhookDelivery.RETRYING], next_attempt_at__lte=now
        ).order_by("next_attempt_at")[:limit]
        return sum(1 for d in list(due) if self.attempt(d.id, now=now).state == WebhookDelivery.DELIVERED)

    def replay(self, ctx: TenantContext, delivery_id: uuid.UUID) -> Any:
        from workflow.models import WebhookDelivery

        d = (
            WebhookDelivery.objects.select_related("subscription")
            .filter(tenant_id=ctx.tenant_id, subscription__company_id__in=list(ctx.company_ids), id=delivery_id)
            .first()
        )
        if d is None:
            raise NotFound("delivery not found")
        policy().require(ctx, "workflow.webhook.update", d.subscription)

        def work(uow: UnitOfWork) -> Any:
            d.state, d.attempts, d.next_attempt_at = WebhookDelivery.PENDING, 0, clock.now()
            d.save(update_fields=["state", "attempts", "next_attempt_at"])
            record_change(
                "workflow.webhook.replay",
                "workflow.webhook",
                d.subscription_id,
                company_id=d.subscription.company_id,
                after={"delivery": d.id},
                uow=uow,
            )
            return d

        return run_in_unit_of_work(work, ctx)

    def test(self, ctx: TenantContext, subscription_id: uuid.UUID) -> Any:
        from workflow.models import WebhookDelivery, WebhookSubscription

        sub = WebhookSubscription.objects.filter(
            tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=subscription_id
        ).first()
        if sub is None:
            raise NotFound("webhook not found")
        policy().require(ctx, "workflow.webhook.update", sub)
        event_id = ids.new_id()
        d = WebhookDelivery.objects.create(
            tenant_id=sub.tenant_id, subscription=sub, event_id=event_id, event_type="workflow.webhook.ping.v1",
            payload={"event_id": str(event_id), "event_type": "workflow.webhook.ping.v1", "payload": {}},
            next_attempt_at=clock.now(),
        )  # fmt: skip
        return self.attempt(d.id)
