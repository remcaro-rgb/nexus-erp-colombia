"""Amounts in words, Spanish (es-CO) — "valor en letras" of receipts, vouchers and invoices.

``amount_in_words(Decimal("1250000.50"))`` → ``"UN MILLÓN DOSCIENTOS CINCUENTA MIL PESOS CON 50/100 M/CTE"``.

Rules (RAE / Colombian commercial usage): apocope before a noun (``un``, ``veintiún``, ``ciento un``), ``cien``
alone and ``ciento`` before more digits, ``mil`` (never "un mil"), ``millón``/``millones``, ``mil millones``,
``billón``/``billones`` (10^12, long scale), ``de pesos`` after exact millions; cents as ``NN/100``. Decimal only.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

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

CURRENCIES = {
    "COP": ("peso", "pesos", "M/CTE"),
    "USD": ("dólar", "dólares", "USD"),
    "EUR": ("euro", "euros", "EUR"),
}
MAX_AMOUNT = Decimal(10) ** 18


def _apocope(words: str) -> str:
    """'uno' → 'un' at the end of a group that precedes a noun (mil, millón, pesos)."""
    if words.endswith("veintiuno"):
        return words[: -len("veintiuno")] + "veintiún"
    if words == "uno" or words.endswith(" uno"):
        return words[:-1]
    return words


def _below_thousand(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    parts: list[str] = []
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if rest:
        if rest < 30:
            parts.append(_UNITS[rest])
        else:
            tens, unit = divmod(rest, 10)
            parts.append(_TENS[tens] + (f" y {_UNITS[unit]}" if unit else ""))
    return " ".join(parts)


def _below_million(n: int) -> str:
    thousands, rest = divmod(n, 1000)
    parts: list[str] = []
    if thousands == 1:
        parts.append("mil")
    elif thousands:
        parts.append(f"{_apocope(_below_thousand(thousands))} mil")
    if rest:
        parts.append(_below_thousand(rest))
    return " ".join(parts)


def integer_in_words(n: int) -> str:
    """Cardinal number in Spanish (lower case), 0 ≤ n < 10^18."""
    if n < 0 or n >= MAX_AMOUNT:
        raise ValueError("amount out of range for words (0 ≤ n < 10^18)")
    if n == 0:
        return "cero"
    billions, rest = divmod(n, 10**12)
    millions, low = divmod(rest, 10**6)
    parts: list[str] = []
    if billions:
        parts.append("un billón" if billions == 1 else f"{_apocope(_below_million(billions))} billones")
    if millions:
        parts.append("un millón" if millions == 1 else f"{_apocope(_below_million(millions))} millones")
    if low:
        parts.append(_below_million(low))
    return " ".join(parts)


def amount_in_words(amount: Decimal, currency: str = "COP") -> str:
    """'Valor en letras' in upper case; the amount is rounded ROUND_HALF_UP to cents (D-001)."""
    if not isinstance(amount, Decimal):
        raise TypeError("amounts are Decimal")
    value = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = value < 0
    value = abs(value)
    whole = int(value)
    cents = int((value - whole) * 100)
    singular, plural, suffix = CURRENCIES.get(currency, (currency, currency, currency))
    words = integer_in_words(whole)
    if whole == 1:
        noun = singular
        words = "un"
    else:
        noun = plural
        words = _apocope(words)
        if whole and whole % 10**6 == 0:
            noun = f"de {plural}"
    text = f"{words} {noun}"
    if cents:
        text += f" con {cents:02d}/100"
    text += f" {suffix}"
    if negative:
        text = "menos " + text
    return text.upper()
