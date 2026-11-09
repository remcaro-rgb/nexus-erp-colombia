"""ReportQueryService (owner P17, ADR-009): semantic-layer queries only.

Contract: every query goes through ``PolicyService`` row/field scoping (the
same path for UI, export, AI and MCP); no raw SQL from callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kernel.tenancy import TenantContext


@dataclass(frozen=True, slots=True)
class ReportQuery:
    model: str
    measures: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = 1000


@dataclass(frozen=True, slots=True)
class ReportResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool = False


@runtime_checkable
class ReportQueryService(Protocol):
    def run(self, ctx: TenantContext, query: ReportQuery) -> ReportResult: ...

    def models(self, ctx: TenantContext) -> list[str]:
        """Semantic models visible to the principal."""
        ...
