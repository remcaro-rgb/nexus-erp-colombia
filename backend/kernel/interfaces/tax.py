"""TaxService (owner P06). Parameters always via ``kernel.parameters`` (C1-9).

Contract: pure calculation (no writes) callable inside or outside a UoW;
unknown parameters fail closed with ``tax_parameter_unknown`` /
``parameter_unknown``; every result carries the parameter snapshots used.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TaxLineInput:
    line_ref: str
    base_amount: Decimal
    tax_codes: tuple[str, ...]
    concept: str | None = None


@dataclass(frozen=True, slots=True)
class TaxLineResult:
    line_ref: str
    tax_code: str
    base_amount: Decimal
    rate: Decimal
    amount: Decimal
    withholding: bool = False


@dataclass(frozen=True, slots=True)
class TaxComputation:
    lines: tuple[TaxLineResult, ...]
    parameter_snapshots: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class TaxService(Protocol):
    def compute(
        self,
        company_id: uuid.UUID,
        document_type: str,
        document_date: dt.date,
        party_id: uuid.UUID | None,
        lines: list[TaxLineInput],
    ) -> TaxComputation:
        """Per-line VAT/consumption tax (shared/07 §8)."""
        ...

    def withholdings(
        self,
        company_id: uuid.UUID,
        document_type: str,
        document_date: dt.date,
        party_id: uuid.UUID,
        lines: list[TaxLineInput],
    ) -> TaxComputation:
        """Withholdings (retefuente, reteIVA, reteICA) from the party tax profile (D-029)."""
        ...
