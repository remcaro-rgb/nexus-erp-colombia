"""Typed parameter values.

Stored JSON per ``value_type``:

* ``decimal`` / ``uvt`` → ``{"value": "52374"}`` (``uvt``: an amount expressed in UVT)
* ``percent`` → ``{"value": "19"}`` meaning 19 %; the provider returns the fraction ``Decimal("0.19")``
* ``permil`` → ``{"value": "9.66"}`` meaning 9.66 ‰; the provider returns ``Decimal("0.00966")``
* ``table`` → ``{"rows": [...]}`` (strings for every number)
* ``json`` → ``{"data": {...}}``
* ``unknown`` → ``{}`` (status UNKNOWN; always fails closed)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from kernel.errors import ValidationFailed

NUMERIC_TYPES = ("decimal", "uvt", "percent", "permil")
VALUE_TYPES = (*NUMERIC_TYPES, "table", "json", "unknown")


def _dec(raw: Any, field: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise ValidationFailed(f"{field}: numbers are written as strings or integers (no floats)", field=field)
    try:
        number = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ValidationFailed(f"{field}: not a decimal number", field=field) from exc
    if not number.is_finite():
        raise ValidationFailed(f"{field}: not a finite number", field=field)
    return number


def normalize(value_type: str, value: Any) -> dict[str, Any]:
    """Validate and normalize a value for storage (JSON-safe, Decimal as string)."""
    if value_type not in VALUE_TYPES:
        raise ValidationFailed(f"unknown value_type {value_type!r}", field="value_type")
    if value_type in NUMERIC_TYPES:
        raw = value.get("value") if isinstance(value, dict) else value
        number = _dec(raw, "value")
        if value_type in ("percent", "permil") and not Decimal(0) <= number <= Decimal(1000):
            raise ValidationFailed("rate out of range", field="value")
        return {"value": str(number)}
    if value_type == "table":
        rows = value.get("rows") if isinstance(value, dict) else value
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise ValidationFailed("table values need a non-empty list of row objects", field="value")
        _reject_floats(rows, "value")
        return {"rows": rows}
    if value_type == "json":
        data = value.get("data") if isinstance(value, dict) and "data" in value else value
        if not isinstance(data, dict):
            raise ValidationFailed("json values must be objects", field="value")
        _reject_floats(data, "value")
        return {"data": data}
    return {}


def _reject_floats(obj: Any, field: str) -> None:
    if isinstance(obj, float):
        raise ValidationFailed(f"{field}: floats are not allowed; write numbers as strings", field=field)
    if isinstance(obj, dict):
        for v in obj.values():
            _reject_floats(v, field)
    elif isinstance(obj, list):
        for v in obj:
            _reject_floats(v, field)


def typed(value_type: str, stored: dict[str, Any]) -> Any:
    """Return the runtime value: Decimal (fractions for percent/permil), list or dict."""
    if value_type in ("decimal", "uvt"):
        return Decimal(stored["value"])
    if value_type == "percent":
        return Decimal(stored["value"]) / Decimal(100)
    if value_type == "permil":
        return Decimal(stored["value"]) / Decimal(1000)
    if value_type == "table":
        return list(stored.get("rows", []))
    if value_type == "json":
        return dict(stored.get("data", {}))
    return None
