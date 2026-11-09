"""Transactional outbox relay and idempotent consumers (ADR-006, C2-7).

* :func:`write` — called by the unit of work inside the transaction.
* :func:`relay_batch` — ``SELECT ... FOR UPDATE SKIP LOCKED`` a batch of
  pending rows, publish each to its Celery queue, mark it PUBLISHED; failures
  back off and become DEAD (plus a :class:`DeadLetter`) after
  ``ERP_OUTBOX_MAX_ATTEMPTS``. Run continuously with ``manage.py outbox_relay``.
* :func:`consume` — consumer side: dedupe by ``(consumer, event_id)`` in
  ``kernel_inbox`` inside the consumer's transaction.

``kernel_outbox``/``kernel_inbox``/``kernel_dead_letter`` are platform tables
read by the relay role; they carry ``tenant_id`` for audit and replay scoping.
RLS (kernel migration 0002): a session bound to a tenant sees only that tenant's
outbox / dead-letter rows; the relay and dead-letter paths here run with
``tenancy.bind_platform("outbox")``; an unbound session sees none.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction

from kernel import clock, tenancy
from kernel.events import DomainEvent
from kernel.models import DeadLetter, InboxRecord, OutboxMessage

log = structlog.get_logger("kernel.outbox")

#: RLS platform scope of kernel_outbox / kernel_dead_letter (migration 0002, ADR-003).
PLATFORM_JOB = "outbox"

QUEUES = ("einvoice", "notifications", "reporting", "ai", "default")

_metrics: Counter[str] = Counter()
_metrics_lock = threading.Lock()


def _inc(name: str, n: int = 1) -> None:
    with _metrics_lock:
        _metrics[name] += n


def metrics() -> dict[str, int]:
    """Process counters plus current backlog sizes."""
    with _metrics_lock:
        snapshot = dict(_metrics)
    with transaction.atomic():
        tenancy.bind_platform(PLATFORM_JOB)
        snapshot["pending"] = OutboxMessage.objects.filter(status=OutboxMessage.Status.PENDING).count()
        snapshot["dead"] = OutboxMessage.objects.filter(status=OutboxMessage.Status.DEAD).count()
    return snapshot


def reset_metrics() -> None:
    with _metrics_lock:
        _metrics.clear()


def route_for(event_type: str) -> str:
    """Queue by module prefix (``ERP_OUTBOX_ROUTES``), else ``default``."""
    routes: dict[str, str] = getattr(settings, "ERP_OUTBOX_ROUTES", {})
    module = event_type.split(".", 1)[0]
    queue = routes.get(module, "default")
    return queue if queue in QUEUES else "default"


def write(events: Iterable[DomainEvent], using: str = "default") -> list[OutboxMessage]:
    rows = [
        OutboxMessage(
            id=e.event_id,
            tenant_id=e.tenant_id,
            company_id=e.company_id,
            event_type=e.event_type,
            queue=route_for(e.event_type),
            envelope=e.envelope(),
        )
        for e in events
    ]
    OutboxMessage.objects.using(using).bulk_create(rows)
    _inc("written", len(rows))
    return rows


class Publisher(Protocol):
    def publish(self, queue: str, envelope: dict[str, Any]) -> None: ...


class CeleryPublisher:
    """Publishes to the ``kernel.dispatch_event`` task on the routed queue."""

    def publish(self, queue: str, envelope: dict[str, Any]) -> None:
        from erp.celery import app

        app.send_task(
            "kernel.dispatch_event",
            args=[envelope],
            queue=queue,
            task_id=envelope["event_id"],
        )


def _backoff_for(attempts: int) -> dt.timedelta:
    return dt.timedelta(seconds=min(2**attempts, 3600))


@dataclass(frozen=True, slots=True)
class RelayResult:
    published: int
    failed: int
    dead: int


def relay_batch(publisher: Publisher | None = None, batch_size: int = 100, using: str = "default") -> RelayResult:
    pub = publisher or CeleryPublisher()
    max_attempts = int(getattr(settings, "ERP_OUTBOX_MAX_ATTEMPTS", 10))
    published = failed = dead = 0
    with transaction.atomic(using=using):
        tenancy.bind_platform(PLATFORM_JOB, using)
        batch = list(
            OutboxMessage.objects.using(using)
            .select_for_update(skip_locked=True)
            .filter(status=OutboxMessage.Status.PENDING, available_at__lte=clock.now())
            .order_by("created_at", "id")[:batch_size]
        )
        for msg in batch:
            try:
                pub.publish(msg.queue, msg.envelope)
            except Exception as exc:  # noqa: BLE001 - any broker error is retried
                msg.attempts += 1
                msg.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if msg.attempts >= max_attempts:
                    msg.status = OutboxMessage.Status.DEAD
                    DeadLetter.objects.using(using).create(
                        stage=DeadLetter.Stage.RELAY,
                        event_id=msg.id,
                        event_type=msg.event_type,
                        tenant_id=msg.tenant_id,
                        envelope=msg.envelope,
                        error=msg.last_error,
                        attempts=msg.attempts,
                    )
                    dead += 1
                else:
                    msg.available_at = clock.now() + _backoff_for(msg.attempts)
                    failed += 1
                msg.save(update_fields=["attempts", "last_error", "status", "available_at"])
                log.warning("outbox.publish_failed", event_id=str(msg.id), attempts=msg.attempts)
                continue
            msg.status = OutboxMessage.Status.PUBLISHED
            msg.published_at = clock.now()
            msg.attempts += 1
            msg.save(update_fields=["status", "published_at", "attempts"])
            published += 1
    _inc("published", published)
    _inc("failed", failed)
    _inc("dead", dead)
    return RelayResult(published=published, failed=failed, dead=dead)


# ----------------------------------------------------------------- consumers

Handler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class Consumer:
    name: str
    pattern: str
    handler: Handler


_consumers: dict[str, Consumer] = {}


def register_consumer(name: str, pattern: str, handler: Handler) -> None:
    """Register ``handler`` for event types matching the glob ``pattern``."""
    existing = _consumers.get(name)
    if existing is not None and existing.handler is not handler:
        raise ValueError(f"consumer {name!r} already registered")
    _consumers[name] = Consumer(name, pattern, handler)


def unregister_consumer(name: str) -> None:
    _consumers.pop(name, None)


def consumers_for(event_type: str) -> list[Consumer]:
    return [c for c in _consumers.values() if fnmatch.fnmatchcase(event_type, c.pattern)]


def consume(consumer: str, envelope: dict[str, Any], handler: Handler, using: str = "default") -> bool:
    """Process ``envelope`` once per consumer. Returns False for a duplicate."""
    with transaction.atomic(using=using):
        try:
            with transaction.atomic(using=using):
                InboxRecord.objects.using(using).create(consumer=consumer, event_id=envelope["event_id"])
        except IntegrityError:
            _inc("duplicates")
            return False
        handler(envelope)
    _inc("consumed")
    return True


def record_consumer_failure(consumer: str, envelope: dict[str, Any], error: BaseException, attempts: int) -> DeadLetter:
    _inc("consumer_dead")
    with transaction.atomic():
        tenancy.bind_platform(PLATFORM_JOB)
        return DeadLetter.objects.create(
            stage=DeadLetter.Stage.CONSUMER,
            consumer=consumer,
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            tenant_id=envelope["tenant_id"],
            envelope=envelope,
            error=f"{type(error).__name__}: {error}"[:2000],
            attempts=attempts,
        )


def dispatch(envelope: dict[str, Any], max_attempts: int = 1) -> dict[str, str]:
    """Deliver ``envelope`` to every matching consumer; failures go to dead letters."""
    outcome: dict[str, str] = {}
    for c in consumers_for(envelope["event_type"]):
        try:
            outcome[c.name] = "processed" if consume(c.name, envelope, c.handler) else "duplicate"
        except Exception as exc:  # noqa: BLE001 - isolate consumers from each other
            record_consumer_failure(c.name, envelope, exc, max_attempts)
            outcome[c.name] = "dead"
    return outcome


def replay_dead_letter(dead_letter_id: Any, publisher: Publisher | None = None) -> None:
    """Re-publish a dead-lettered event (P07 provides the audited replay UI)."""
    pub = publisher or CeleryPublisher()
    with transaction.atomic():
        tenancy.bind_platform(PLATFORM_JOB)
        dl = DeadLetter.objects.select_for_update().get(pk=dead_letter_id)
        pub.publish(route_for(dl.event_type), dl.envelope)
        dl.replayed_at = clock.now()
        dl.save(update_fields=["replayed_at"])
