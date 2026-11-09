"""Effective-date helpers for ``valid_from`` / ``valid_to`` (DIMENSIONS-ADDENDUM).

Convention: both bounds inclusive; ``None`` means open-ended.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db.models import Q


@dataclass(frozen=True, slots=True)
class DateRange:
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None

    def __post_init__(self) -> None:
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be >= valid_from")

    def contains(self, day: dt.date) -> bool:
        return is_effective(self.valid_from, self.valid_to, day)

    def overlaps(self, other: DateRange) -> bool:
        start_ok = other.valid_to is None or self.valid_from is None or self.valid_from <= other.valid_to
        end_ok = self.valid_to is None or other.valid_from is None or other.valid_from <= self.valid_to
        return start_ok and end_ok

    def intersection(self, other: DateRange) -> DateRange | None:
        if not self.overlaps(other):
            return None
        starts = [d for d in (self.valid_from, other.valid_from) if d is not None]
        ends = [d for d in (self.valid_to, other.valid_to) if d is not None]
        return DateRange(max(starts) if starts else None, min(ends) if ends else None)


def is_effective(valid_from: dt.date | None, valid_to: dt.date | None, on: dt.date) -> bool:
    if valid_from is not None and on < valid_from:
        return False
    return valid_to is None or on <= valid_to


def effective_q(on: dt.date, from_field: str = "valid_from", to_field: str = "valid_to") -> Q:
    """ORM filter for rows effective on ``on`` (closure-table queries)."""
    return (Q(**{f"{from_field}__isnull": True}) | Q(**{f"{from_field}__lte": on})) & (
        Q(**{f"{to_field}__isnull": True}) | Q(**{f"{to_field}__gte": on})
    )


def overlapping_q(rng: DateRange, from_field: str = "valid_from", to_field: str = "valid_to") -> Q:
    """ORM filter for rows whose validity overlaps ``rng`` (no-overlap checks)."""
    q = Q()
    if rng.valid_to is not None:
        q &= Q(**{f"{from_field}__isnull": True}) | Q(**{f"{from_field}__lte": rng.valid_to})
    if rng.valid_from is not None:
        q &= Q(**{f"{to_field}__isnull": True}) | Q(**{f"{to_field}__gte": rng.valid_from})
    return q
