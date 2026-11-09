"""DocumentRenderer and Labels (D-005; owner P17, labels P09).

Contract: rendering is pure (no DB writes, no network); output PDF bytes are
deterministic for the same template version + data; synthetic/simulated
documents carry the mandated watermark label (e.g. 'SINTÉTICO — no válido
ante la DIAN'). Label print jobs are audited, never journaled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    content: bytes
    media_type: str
    template_code: str
    template_version: str
    watermark: str | None = None


@runtime_checkable
class DocumentRenderer(Protocol):
    def render(
        self,
        template_code: str,
        data: dict[str, Any],
        *,
        locale: str = "es-CO",
        media_type: Literal["application/pdf", "text/html"] = "application/pdf",
        watermark: str | None = None,
    ) -> RenderedDocument: ...


@dataclass(frozen=True, slots=True)
class LabelRequest:
    item_id: uuid.UUID
    copies: int
    source: DocumentRef | None = None
    symbology: Literal["CODE128", "EAN13"] = "CODE128"  # D-004
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Labels(Protocol):
    def request_labels(self, company_id: uuid.UUID, requests: list[LabelRequest]) -> uuid.UUID:
        """Queue a label print job (audited); returns the job id."""
        ...

    def render_job(self, company_id: uuid.UUID, job_id: uuid.UUID) -> RenderedDocument: ...
