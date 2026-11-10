"""einvoice worker: outbox consumer + due-work sweeper (transmission happens here, never in a UoW).

* Consumer ``einvoice.transmit`` on ``einvoice.document.status_changed.v1``: a document that reached
  ``built`` is signed and transmitted (at-least-once delivery; ``kernel_inbox`` dedupes; the document row
  lock and the attempt counter make a duplicate delivery a no-op).
* ``process_due(ctx)``: retries whose backoff elapsed, manual retries, and the contingency backlog after
  the service is restored (late transmission). Run by ``manage.py einvoice_worker`` / the scheduler.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from einvoice import service
from einvoice.models import FiscalDocument, RadianEvent
from kernel import tenancy
from kernel.tenancy import Channel, PrincipalType, TenantContext

log = structlog.get_logger("einvoice.worker")
CONSUMER = "einvoice.transmit"
EVENT = "einvoice.document.status_changed.v1"


def worker_context(tenant_id: uuid.UUID, company_id: uuid.UUID, actor_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        company_ids=frozenset({company_id}),
        principal_id=actor_id,
        principal_type=PrincipalType.SERVICE,
        channel=Channel.JOB,
    )


def handle_status_changed(envelope: dict[str, Any]) -> None:
    payload = envelope.get("payload") or {}
    if payload.get("to_status") != FiscalDocument.State.BUILT:
        return
    ctx = worker_context(
        uuid.UUID(envelope["tenant_id"]),
        uuid.UUID(envelope["company_id"]),
        uuid.UUID(str(envelope.get("actor", {}).get("id") or envelope.get("actor_id"))),
    )
    doc = service.transmit(ctx, uuid.UUID(payload["document_id"]))
    log.info("einvoice.transmitted", document_id=str(doc.id), status=doc.status, attempt=doc.attempts)


def process_due(ctx: TenantContext, company_id: uuid.UUID | None = None) -> dict[str, int]:
    with tenancy.scope(ctx):
        ids = service.due_documents(ctx, company_id)
        events = list(
            RadianEvent.objects.filter(
                tenant_id=ctx.tenant_id,
                company_id__in=list(ctx.company_ids),
                direction=RadianEvent.Direction.EMITTED,
                status=RadianEvent.Status.PENDING,
            ).values_list("id", flat=True)[:500]
        )
    counts: dict[str, int] = {}
    for doc_id in ids:
        doc = service.transmit(ctx, doc_id)
        counts[doc.status] = counts.get(doc.status, 0) + 1
    for event_id in events:
        ev = service.transmit_event(ctx, event_id)
        counts[f"event:{ev.status}"] = counts.get(f"event:{ev.status}", 0) + 1
    return counts


def register() -> None:
    from kernel import outbox

    outbox.register_consumer(CONSUMER, EVENT, handle_status_changed)
