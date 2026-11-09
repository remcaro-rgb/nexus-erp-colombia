"""``dsr_registry``: modules declare which models hold personal data of which subjects.

Each module registers a :class:`PersonalDataSource` at app ready. The DSR
workflow uses the registry for discovery (export package), rectification and
suppression; a source with a legal retention rule explains the denial.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kernel.tenancy import TenantContext

Finder = Callable[[TenantContext, str], list[dict[str, Any]]]
Suppressor = Callable[[TenantContext, str], int]


@dataclass(frozen=True)
class PersonalDataSource:
    code: str  # e.g. "hr.employee"
    subject_types: frozenset[str]  # employee | customer | supplier | contact
    fields: tuple[str, ...]
    find: Finder
    suppress: Suppressor | None = None
    retention: str = ""  # legal basis that forbids suppression (e.g. "Ley 962/2005 art. 28: 10 años")
    rectifiable: tuple[str, ...] = field(default_factory=tuple)


DSR_SOURCES: dict[str, PersonalDataSource] = {}


def register_source(source: PersonalDataSource, *, replace: bool = False) -> None:
    if source.code in DSR_SOURCES and not replace:
        raise ValueError(f"DSR source {source.code} already registered")
    DSR_SOURCES[source.code] = source


def sources_for(subject_type: str) -> list[PersonalDataSource]:
    return [s for s in sorted(DSR_SOURCES.values(), key=lambda s: s.code) if subject_type in s.subject_types]
