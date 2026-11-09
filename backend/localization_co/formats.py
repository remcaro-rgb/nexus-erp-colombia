"""es-CO display formats: ``1.234.567,89``, ``$ 1.234.567,89``, ``09/11/2026``."""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from kernel.errors import ValidationFailed


def format_number(value: Decimal, decimals: int = 2) -> str:
    if not isinstance(value, Decimal):
        raise ValidationFailed("el valor debe ser Decimal", field="value")
    q = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    rounded = value.quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    whole, _, frac = f"{abs(rounded):f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{sign}{grouped},{frac}" if decimals else f"{sign}{grouped}"


def format_money(value: Decimal, currency: str = "COP", decimals: int = 2) -> str:
    symbol = "$" if currency.upper() == "COP" else currency.upper()
    text = format_number(value, decimals)
    if text.startswith("-"):
        return f"-{symbol} {text[1:]}"
    return f"{symbol} {text}"


def format_date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def parse_number(text: str) -> Decimal:
    """Parse ``1.234.567,89`` (es-CO) into a Decimal."""
    cleaned = text.strip().replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except ArithmeticError as exc:
        raise ValidationFailed(f"número inválido: {text!r}", field="value") from exc
