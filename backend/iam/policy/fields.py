"""Field classification registry and masking strategies.

Modules register their resources' fields with the contract classification
(``x-classification``): ``register("iam.branch", {"code": "internal", ...})``.
Unregistered fields of a registered resource are treated as ``secret``
(never readable): fail closed.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any

_REGISTRY: dict[str, dict[str, str]] = {}


def register(resource_type: str, fields: dict[str, str]) -> None:
    _REGISTRY[resource_type] = {**_REGISTRY.get(resource_type, {}), **fields}


def fields_of(resource_type: str) -> dict[str, str]:
    return dict(_REGISTRY.get(resource_type, {}))


def classification(resource_type: str, field: str) -> str:
    return _REGISTRY.get(resource_type, {}).get(field, "secret")


def _range(value: Any) -> str:
    """Salary-style range bucket, e.g. 3.500.000 → "3M–4M" (Decimal only)."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "****"
    million = Decimal(1_000_000)
    low = int(amount // million)
    return f"{low}M–{low + 1}M"


def apply_mask(strategy: str, value: Any) -> Any:
    if value is None or strategy in ("", "none"):
        return value
    text = str(value)
    if strategy == "last4":
        return "*" * max(len(text) - 4, 0) + text[-4:]
    if strategy == "hash":
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]
    if strategy == "range":
        return _range(value)
    return "****"  # full (and any unknown strategy: fail closed)
