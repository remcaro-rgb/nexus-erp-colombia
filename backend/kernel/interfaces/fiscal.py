"""FiscalGateway / FiscalDocuments (owner P15, ADR-007, C1-10).

Contract:
* ``FiscalGateway.submit`` is never called inside a UoW: the module emits an
  outbox event and the einvoice worker submits (at-least-once, idempotent by
  ``document.id``). In this edition every adapter is a fixture labelled
  ``FIXTURE_NOT_CERTIFIED`` (``simulated=True``).
* A validated document (CUFE/CUDE/CUNE) is corrected only by notes.
* ``FiscalDocuments`` is the read side used by purchasing/portals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef

FiscalStatus = Literal["DRAFT", "QUEUED", "SUBMITTED", "ACCEPTED", "REJECTED", "CONTINGENCY", "VOIDED_BY_NOTE"]


@dataclass(frozen=True, slots=True)
class FiscalStatusInfo:
    document: DocumentRef
    status: FiscalStatus
    unique_code: str | None  # CUFE/CUDE/CUNE
    simulated: bool
    message: str = ""


@runtime_checkable
class FiscalGateway(Protocol):
    mode: str  # DISABLED | FIXTURE_NOT_CERTIFIED | SANDBOX

    def submit(self, company_id: uuid.UUID, document: DocumentRef, payload: bytes) -> FiscalStatusInfo:
        """Transmit (worker only, outside any UoW)."""
        ...

    def poll(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo: ...


@runtime_checkable
class FiscalDocuments(Protocol):
    def status(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo:
        """Current fiscal status (exactly one, invariant 10)."""
        ...

    def history(self, company_id: uuid.UUID, document: DocumentRef) -> list[FiscalStatusInfo]: ...
