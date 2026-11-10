"""``ProviderAdapter`` port (ADR-007, F-PT-OPEN): what every transmission adapter must implement.

An adapter is a pluggable object created by a factory registered in :mod:`einvoice.adapters.registry`
from an :class:`AdapterConfig`. Adapters are only called by the einvoice worker **outside** any unit of
work (they call :func:`kernel.io_guard.check` first), never re-number documents, never change the
CUFE/CUDE they receive, and never put credentials in results, messages or exceptions. The contract suite
(:mod:`einvoice.adapters.contract`) checks all of this for any adapter.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from einvoice.secrets import Secret

ResultStatus = Literal["ACCEPTED", "ACCEPTED_WITH_NOTICES", "REJECTED", "RETRYABLE"]
FINAL_STATUSES: frozenset[str] = frozenset({"ACCEPTED", "ACCEPTED_WITH_NOTICES", "REJECTED"})


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    adapter: str
    provider_code: str = "SIM"
    base_url: str = ""
    environment: str = "SANDBOX"
    credentials: Secret | None = None
    mapping_version: str = "generic-json-v1"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    document_id: uuid.UUID
    company_id: uuid.UUID
    family: str
    number: str
    unique_code: str
    xml: bytes
    environment: str = "2"
    fixture_outcome: str = "accept"
    attempt: int = 1
    test_set_id: str = ""
    kind: Literal["document", "event"] = "document"

    @property
    def idempotency_key(self) -> str:
        return f"einvoice-{self.document_id}"


@dataclass(frozen=True, slots=True)
class SubmitResult:
    status: ResultStatus
    tracking_id: str
    unique_code: str | None
    simulated: bool
    messages: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    response_xml: bytes = b""
    latency_ms: int = 0

    @property
    def final(self) -> bool:
        return self.status in FINAL_STATUSES


@dataclass(frozen=True, slots=True)
class NumberingRangeInfo:
    prefix: str
    from_number: int
    to_number: int
    resolution: str
    valid_from: dt.date
    valid_to: dt.date
    technical_key: Secret | None = None


@runtime_checkable
class ProviderAdapter(Protocol):
    key: str
    simulated: bool
    certified: bool

    def submit(self, request: SubmitRequest) -> SubmitResult:
        """Transmit a signed document or RADIAN event (outside any UoW)."""
        ...

    def status(self, request: SubmitRequest, tracking_id: str) -> SubmitResult:
        """Current status of an earlier submission (GetStatus)."""
        ...

    def numbering_ranges(self, company_nit: str, software_id: str) -> list[NumberingRangeInfo]:
        """Authorized numbering ranges (GetNumberingRange); ``AdapterNotSupported`` if unavailable."""
        ...
