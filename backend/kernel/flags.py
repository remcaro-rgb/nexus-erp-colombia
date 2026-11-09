"""Feature flags per tenant/company with defaults from code (shared/02 §4.5).

Resolution: company override > tenant override > registered default.
Every ``[A]`` (advanced) feature is registered here and defaults to off.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from django.db.models import Q

from kernel.models import FeatureFlagOverride

Kind = Literal["A", "P"]


@dataclass(frozen=True, slots=True)
class FlagDefinition:
    code: str
    default: bool
    kind: Kind
    description: str


_registry: dict[str, FlagDefinition] = {}


def register(code: str, *, default: bool = False, kind: Kind = "A", description: str = "") -> FlagDefinition:
    if kind == "A" and default:
        raise ValueError("[A] features must default to off")
    definition = FlagDefinition(code, default, kind, description)
    existing = _registry.get(code)
    if existing is not None and existing != definition:
        raise ValueError(f"flag {code!r} already registered differently")
    _registry[code] = definition
    return definition


def definition(code: str) -> FlagDefinition:
    try:
        return _registry[code]
    except KeyError:
        raise KeyError(f"unknown feature flag {code!r}") from None


def registered() -> dict[str, FlagDefinition]:
    return dict(_registry)


def is_enabled(code: str, tenant_id: uuid.UUID, company_id: uuid.UUID | None = None) -> bool:
    default = definition(code).default
    scope = Q(company_id__isnull=True)
    if company_id is not None:
        scope |= Q(company_id=company_id)
    rows = dict(
        FeatureFlagOverride.objects.filter(scope, tenant_id=tenant_id, code=code).values_list("company_id", "enabled")
    )
    if company_id is not None and company_id in rows:
        return rows[company_id]
    if None in rows:
        return rows[None]
    return default


POS_OFFLINE_FISCAL_SELLING = register(
    "pos_offline_fiscal_selling",
    kind="A",
    description="Offline POS fiscal selling (ADR-015); requires EG-13; never used by [P] tests",
).code
