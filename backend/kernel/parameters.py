"""Regulatory parameter interface (C1-9). P06 implements the provider.

``ParameterProvider.get(code, at_date, company) -> ParameterValue``. Values are
data, never code. :func:`enforce` applies the fail-closed policy:

* UNKNOWN or missing → :class:`ParameterUnknown`;
* production without a legal-review stamp → :class:`ParameterNotVerified`;
* DISPUTED → computes, but the caller must store a warning + snapshot
  (``ParameterValue.requires_warning``).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from kernel.errors import ParameterNotVerified, ParameterUnknown


class ParameterStatus(enum.StrEnum):
    VERIFIED_PRIMARY = "VERIFIED-PRIMARY"
    VERIFIED_SEARCH = "VERIFIED-SEARCH"
    VERIFIED_SECONDARY = "VERIFIED-SECONDARY"
    UNKNOWN = "UNKNOWN"
    DISPUTED = "DISPUTED"


@dataclass(frozen=True, slots=True)
class ParameterValue:
    code: str
    value: Any
    status: ParameterStatus
    source_id: str
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    legal_review_stamp: str | None = None
    synthetic: bool = False  # labelled fixture: NOT LEGALLY VERIFIED

    @property
    def requires_warning(self) -> bool:
        return self.status is ParameterStatus.DISPUTED or self.synthetic

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "value": str(self.value),
            "status": self.status.value,
            "source_id": self.source_id,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "legal_review_stamp": self.legal_review_stamp,
            "synthetic": self.synthetic,
        }


@runtime_checkable
class ParameterProvider(Protocol):
    def get(self, code: str, at_date: dt.date, company: uuid.UUID) -> ParameterValue:
        """Return the value effective on ``at_date`` for ``company``.

        Raises :class:`ParameterUnknown` when the value is missing or UNKNOWN;
        :class:`ParameterNotVerified` in production without a legal-review stamp.
        """
        ...


def enforce(value: ParameterValue | None, code: str, *, production: bool) -> ParameterValue:
    if value is None or value.status is ParameterStatus.UNKNOWN:
        raise ParameterUnknown(f"parameter {code} is UNKNOWN for the requested date", code=code)
    if production and (value.synthetic or not value.legal_review_stamp):
        raise ParameterNotVerified(f"parameter {code} has no legal-review stamp", code=code)
    return value
