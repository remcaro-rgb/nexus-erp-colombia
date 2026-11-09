"""Django model fields with the constitutional NUMERIC precisions (C2-2)."""

from __future__ import annotations

from typing import Any

from django.db import models

from kernel import ids


class _FixedDecimalField(models.DecimalField):
    DIGITS = 20
    PLACES = 4

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_digits"] = self.DIGITS
        kwargs["decimal_places"] = self.PLACES
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> Any:
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("max_digits", None)
        kwargs.pop("decimal_places", None)
        return name, path, args, kwargs


class AmountField(_FixedDecimalField):
    """NUMERIC(20,4)."""

    DIGITS, PLACES = 20, 4


class QtyField(_FixedDecimalField):
    """NUMERIC(20,6)."""

    DIGITS, PLACES = 20, 6


class UnitCostField(_FixedDecimalField):
    """NUMERIC(24,8)."""

    DIGITS, PLACES = 24, 8


class FxRateField(_FixedDecimalField):
    """NUMERIC(24,10)."""

    DIGITS, PLACES = 24, 10


class PctField(_FixedDecimalField):
    """NUMERIC(9,6)."""

    DIGITS, PLACES = 9, 6


class CurrencyField(models.CharField):
    """ISO 4217 code (3 upper-case letters)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_length"] = 3
        kwargs.setdefault("default", "COP")
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> Any:
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("max_length", None)
        return name, path, args, kwargs


def uuid7_pk() -> models.UUIDField:
    """Primary key field with a UUIDv7 default."""
    return models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
