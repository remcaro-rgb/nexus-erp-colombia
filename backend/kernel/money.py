"""Money and decimal precision primitives (C2-2).

``Decimal`` only. Floats are rejected everywhere. Precisions (scale) per the
constitution: AMOUNT=4, QTY=6, UNIT_COST=8, FX=10, PCT=6.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

AMOUNT: Final = 4
QTY: Final = 6
UNIT_COST: Final = 8
FX: Final = 10
PCT: Final = 6

#: ISO 4217 minor units for the currencies the product enables by default.
#: Unknown currencies default to 2 (ISO norm); add entries through a CCR.
MINOR_UNITS: Final[dict[str, int]] = {
    "COP": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "MXN": 2,
    "PEN": 2,
    "CLP": 0,
    "JPY": 0,
    "BRL": 2,
    "CAD": 2,
}

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class MoneyError(ValueError):
    """Raised for invalid money operations (mixed currencies, floats, bad input)."""


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert ``value`` to ``Decimal``; floats and non-finite values are refused."""
    if isinstance(value, bool):
        raise MoneyError("booleans are not numbers")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"invalid decimal literal: {value!r}") from exc
    else:
        raise MoneyError(f"{type(value).__name__} is not allowed; use Decimal, int or str")
    if not result.is_finite():
        raise MoneyError("non-finite decimals are not allowed")
    return result


def _exp(places: int) -> Decimal:
    if places < 0:
        raise MoneyError("places must be >= 0")
    return Decimal(1).scaleb(-places)


def round_half_even(value: Decimal | int | str, places: int) -> Decimal:
    """Banker's rounding to ``places`` decimals."""
    return to_decimal(value).quantize(_exp(places), rounding=ROUND_HALF_EVEN)


def round_half_up(value: Decimal | int | str, places: int) -> Decimal:
    """Commercial rounding (document totals, D-001)."""
    return to_decimal(value).quantize(_exp(places), rounding=ROUND_HALF_UP)


def q_amount(value: Decimal | int | str) -> Decimal:
    return round_half_even(value, AMOUNT)


def q_qty(value: Decimal | int | str) -> Decimal:
    return round_half_even(value, QTY)


def q_unit_cost(value: Decimal | int | str) -> Decimal:
    return round_half_even(value, UNIT_COST)


def q_fx(value: Decimal | int | str) -> Decimal:
    return round_half_even(value, FX)


def q_pct(value: Decimal | int | str) -> Decimal:
    return round_half_even(value, PCT)


def minor_units(currency: str) -> int:
    return MINOR_UNITS.get(validate_currency(currency), 2)


def validate_currency(currency: str) -> str:
    if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
        raise MoneyError(f"invalid ISO 4217 currency code: {currency!r}")
    return currency


def allocate(total: Decimal | int | str, weights: Sequence[Decimal | int | str], places: int = AMOUNT) -> list[Decimal]:
    """Split ``total`` proportionally to ``weights`` with the largest-remainder method.

    * the parts always sum exactly to ``total`` quantized to ``places``;
    * deterministic: leftover units go to the largest remainders, ties by lower index;
    * weights must be >= 0 with a positive sum; a negative total is allocated by sign.
    """
    total_d = round_half_even(total, places)
    w = [to_decimal(x) for x in weights]
    if not w:
        raise MoneyError("weights must not be empty")
    if any(x < 0 for x in w):
        raise MoneyError("weights must be >= 0")
    wsum = sum(w, Decimal(0))
    if wsum == 0:
        raise MoneyError("weights must not all be zero")
    sign = -1 if total_d < 0 else 1
    unit = _exp(places)
    units_total = int((abs(total_d) / unit).to_integral_value())
    raw = [Decimal(units_total) * x / wsum for x in w]
    floors = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
    leftover = units_total - sum(floors)
    order = sorted(range(len(w)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:leftover]:
        floors[i] += 1
    return [(Decimal(sign * n) * unit).quantize(unit) for n in floors]


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable amount + ISO 4217 currency. Arithmetic only within one currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        validate_currency(self.currency)

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: str = "COP") -> Money:
        return cls(to_decimal(amount), currency)

    @classmethod
    def zero(cls, currency: str = "COP") -> Money:
        return cls(Decimal(0), currency)

    def _same(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise MoneyError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise MoneyError(f"currency mismatch: {self.currency} vs {other.currency}")
        return other

    def __add__(self, other: Money) -> Money:
        return Money(self.amount + self._same(other).amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount - self._same(other).amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: Decimal | int | str) -> Money:
        if isinstance(factor, Money):
            raise MoneyError("cannot multiply Money by Money")
        return Money(self.amount * to_decimal(factor), self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._same(other).amount

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._same(other).amount

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._same(other).amount

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._same(other).amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def quantize(self, places: int = AMOUNT) -> Money:
        return Money(round_half_even(self.amount, places), self.currency)

    def round_to_minor(self) -> Money:
        """Round half-up to the currency minor unit (document totals, D-001)."""
        return Money(round_half_up(self.amount, minor_units(self.currency)), self.currency)

    def allocate(self, weights: Sequence[Decimal | int | str], places: int = AMOUNT) -> list[Money]:
        return [Money(p, self.currency) for p in allocate(self.amount, weights, places)]

    def to_json(self) -> dict[str, str]:
        """Contract shape ``Money {amount: string(decimal), currency}``."""
        return {"amount": str(self.amount), "currency": self.currency}

    @classmethod
    def from_json(cls, data: dict[str, str]) -> Money:
        return cls(to_decimal(data["amount"]), data["currency"])


def money_sum(items: Sequence[Money], currency: str) -> Money:
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
