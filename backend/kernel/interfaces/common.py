"""Shared value types for the kernel interfaces (mirror contracts/openapi/root.yaml)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel.money import to_decimal


@dataclass(frozen=True, slots=True)
class DocumentRef:
    type: str
    id: uuid.UUID
    number: str | None = None


@dataclass(frozen=True, slots=True)
class PartyRef:
    id: uuid.UUID
    kind: str = "party"  # party | employee | vendor | customer


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    uom: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))


@dataclass(frozen=True, slots=True)
class DimensionValues:
    """Accounting dimension slots of a line (kernel.dimensions registry)."""

    cost_center_id: uuid.UUID | None = None
    business_unit_id: uuid.UUID | None = None
    aux_abierto_id: uuid.UUID | None = None
    item_dimension_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Page:
    items: list[Any]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AuditStamp:
    at: dt.datetime
    by: uuid.UUID
    extra: dict[str, Any] = field(default_factory=dict)
