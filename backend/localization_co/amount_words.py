"""Spanish number-to-words for invoice "valor en letras" (es-CO).

``amount_in_words(Decimal("1190000.50"))`` →
``"UN MILLÓN CIENTO NOVENTA MIL PESOS CON 50/100 M/CTE"``.
Decimal only; floats are refused.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from kernel.errors import ValidationFailed

_UNITS = (
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve",
    "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete",
    "veintiocho", "veintinueve",
)  # fmt: skip
_TENS = ("", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa")
_HUNDREDS = (
    "", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos",
    "ochocientos", "novecientos",
)  # fmt: skip

MAX_VALUE = 10**18 - 1
CURRENCY_NAMES = {"COP": ("PESO", "PESOS", "M/CTE"), "USD": ("DÓLAR", "DÓLARES", ""), "EUR": ("EURO", "EUROS", "")}


def _below_hundred(n: int) -> str:
    if n < 30:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else f"{_TENS[tens]} y {_UNITS[unit]}"


def _below_thousand(n: int) -> str:
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    parts = [_HUNDREDS[hundreds]] if hundreds else []
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _apocope(words: str) -> str:
    """``uno`` → ``un`` before a noun (``veintiuno`` → ``veintiún``)."""
    if words.endswith("veintiuno"):
        return words[: -len("veintiuno")] + "veintiún"
    if words.endswith("uno"):
        return words[:-3] + "un"
    return words


def integer_to_words(n: int) -> str:
    if n < 0 or n > MAX_VALUE:
        raise ValidationFailed("valor fuera del rango admitido para letras", field="amount")
    if n == 0:
        return "cero"
    parts: list[str] = []
    for scale_value, singular, plural in ((10**12, "billón", "billones"), (10**6, "millón", "millones")):
        count, n = divmod(n, scale_value)
        if count:
            parts.append(f"un {singular}" if count == 1 else f"{_apocope(integer_to_words(count))} {plural}")
    thousands, n = divmod(n, 1000)
    if thousands:
        parts.append("mil" if thousands == 1 else f"{_apocope(_below_thousand(thousands))} mil")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal, currency: str = "COP", *, upper: bool = True) -> str:
    if not isinstance(amount, Decimal):
        raise ValidationFailed("el valor debe ser Decimal", field="amount")
    if amount < 0:
        raise ValidationFailed("valor negativo", field="amount")
    singular, plural, suffix = CURRENCY_NAMES.get(currency.upper(), (currency.upper(), currency.upper(), ""))
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = int(rounded)
    cents = int((rounded - whole) * 100)
    words = integer_to_words(whole)
    if whole == 1:
        text = f"un {singular.lower()}"
    else:
        text = _apocope(words)
        # "un millón de pesos", "dos millones de pesos" (scale word with nothing after it)
        if whole >= 10**6 and whole % 10**6 == 0:
            text += " de"
        text += f" {plural.lower()}"
    text += f" con {cents:02d}/100" if cents else ""
    if suffix:
        text += f" {suffix}"
    return text.upper() if upper else text
