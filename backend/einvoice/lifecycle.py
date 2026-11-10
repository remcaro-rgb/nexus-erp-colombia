"""Fiscal-document lifecycle (state machine, T-P15-05).

``pending_build → built → signed → submitted → accepted | accepted_with_notices | rejected | error_retryable``
``error_retryable → submitted`` (retries with backoff; the number is never re-issued)
``signed → contingency_issued → transmitted_late → accepted | … | error_retryable``

Every transition is written to ``einvoice_transition`` (append-only) with the evidence it produced, and
emits ``einvoice.document.status_changed.v1`` through the outbox.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from einvoice.errors import InvalidTransition
from einvoice.models import FiscalDocument, FiscalEvidence, FiscalTransition
from kernel import clock
from kernel.uow import UnitOfWork

S = FiscalDocument.State

TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    S.PENDING_BUILD: frozenset({S.BUILT}),
    S.BUILT: frozenset({S.SIGNED}),
    S.SIGNED: frozenset({S.SUBMITTED, S.CONTINGENCY_ISSUED}),
    S.SUBMITTED: frozenset({S.ACCEPTED, S.ACCEPTED_WITH_NOTICES, S.REJECTED, S.ERROR_RETRYABLE}),
    S.ERROR_RETRYABLE: frozenset({S.SUBMITTED, S.CONTINGENCY_ISSUED}),
    S.CONTINGENCY_ISSUED: frozenset({S.TRANSMITTED_LATE}),
    S.TRANSMITTED_LATE: frozenset({S.ACCEPTED, S.ACCEPTED_WITH_NOTICES, S.REJECTED, S.ERROR_RETRYABLE}),
    S.ACCEPTED: frozenset(),
    S.ACCEPTED_WITH_NOTICES: frozenset(),
    S.REJECTED: frozenset(),
}

#: Contract enum ``FiscalDocument.fiscal_status``.
FISCAL_STATUS: Final[dict[str, str]] = {
    S.PENDING_BUILD: "PENDING", S.BUILT: "PENDING", S.SIGNED: "PENDING",
    S.SUBMITTED: "SENT", S.ERROR_RETRYABLE: "SENT", S.TRANSMITTED_LATE: "SENT",
    S.ACCEPTED: "ACCEPTED", S.ACCEPTED_WITH_NOTICES: "ACCEPTED", S.REJECTED: "REJECTED",
    S.CONTINGENCY_ISSUED: "CONTINGENCY",
}  # fmt: skip

#: ``kernel.interfaces.fiscal.FiscalStatus``.
KERNEL_STATUS: Final[dict[str, str]] = {
    "PENDING": "QUEUED", "SENT": "SUBMITTED", "ACCEPTED": "ACCEPTED", "REJECTED": "REJECTED",
    "CONTINGENCY": "CONTINGENCY",
}  # fmt: skip

#: Backoff for retryable errors (attempt → delay); after the last step retries are manual only.
BACKOFF: Final[tuple[dt.timedelta, ...]] = (
    dt.timedelta(minutes=1), dt.timedelta(minutes=5), dt.timedelta(minutes=15), dt.timedelta(hours=1),
    dt.timedelta(hours=4),
)  # fmt: skip
#: Consecutive transport failures of the active provider that switch the company to contingency automatically.
AUTO_CONTINGENCY_AFTER: Final = 3


def can(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, frozenset())


def next_attempt(attempts: int) -> dt.datetime | None:
    if attempts < 1 or attempts > len(BACKOFF):
        return None
    return clock.now() + BACKOFF[attempts - 1]


def transition(
    uow: UnitOfWork,
    doc: FiscalDocument,
    to_state: str,
    *,
    reason: str = "",
    evidence: FiscalEvidence | None = None,
    fields: tuple[str, ...] = (),
) -> FiscalTransition:
    """Validate and apply ``doc.status → to_state`` (caller holds the row lock), log it, emit the event."""
    from_state = doc.status
    if not can(from_state, to_state):
        raise InvalidTransition(f"transition {from_state} → {to_state} is not allowed", code="validation_failed")
    doc.status = to_state
    doc.save_versioned(uow.ctx.principal_id, ["status", *fields])
    row = FiscalTransition.objects.create(
        tenant_id=doc.tenant_id, company_id=doc.company_id, document=doc, from_state=from_state, to_state=to_state,
        actor_id=uow.ctx.principal_id, attempt=doc.attempts, reason=reason[:1000], evidence=evidence,
    )  # fmt: skip
    uow.emit(
        "einvoice.document.status_changed.v1",
        {
            # contracts/events/einvoice/einvoice.document.status_changed.v1.json (additionalProperties: false)
            "document_id": str(doc.id),
            "source": {"type": doc.source_type, "id": str(doc.source_id), "number": doc.number},
            "from_status": from_state, "to_status": to_state, "cufe": doc.unique_code,
        },
        company_id=doc.company_id,
    )  # fmt: skip
    return row
