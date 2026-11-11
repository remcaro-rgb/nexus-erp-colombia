"""Pure barcode encoders and validators (D-004): Code 128 (subset auto), EAN-13, GTIN mod-10.

No database, no network, no external service. Shared by the barcode service, the label renderer and the
unit tests (golden vectors, T-INV-BARCODE-01).

* ``code128_values(text)`` → symbol values incl. start, code-set switches, check symbol and stop.
* ``code128_modules(text)`` → bar/space module string (``"1"`` = bar, ``"0"`` = space), without quiet zones.
* ``gtin_check_digit(body)`` → GS1 mod-10 check digit of a GTIN body (7, 11, 12 or 13 digits).
* ``validate_gtin(value)`` → normalized GTIN or :class:`BarcodeFormatError`.
* ``ean13_modules(value)`` → 95-module EAN-13 pattern (guards included).
"""

from __future__ import annotations

from dataclasses import dataclass

# Code 128 element widths (bar, space, bar, space, bar, space) for symbol values 0..105; 106 = STOP (7 elements).
CODE128_PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)  # fmt: skip

START_A, START_B, START_C, STOP = 103, 104, 105, 106
CODE_A, CODE_B, CODE_C = 101, 100, 99  # switch symbols (value meaning in the *current* set)

# EAN-13 left-hand odd parity (L) patterns; G and R are derived (R = complement of L, G = reverse of R).
EAN_L: tuple[str, ...] = (
    "0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011",
)  # fmt: skip
EAN_R: tuple[str, ...] = tuple("".join("1" if b == "0" else "0" for b in p) for p in EAN_L)
EAN_G: tuple[str, ...] = tuple(p[::-1] for p in EAN_R)
EAN_PARITY: tuple[str, ...] = (
    "LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL",
)  # fmt: skip

GTIN_LENGTHS = (8, 12, 13, 14)
RESTRICTED_PREFIXES = tuple(str(n) for n in range(20, 30))  # GS1 restricted circulation (D-004 synthetic demo)


class BarcodeFormatError(ValueError):
    """The value cannot be encoded / is not a valid GTIN (``reason`` is a stable machine code)."""

    def __init__(self, message: str, reason: str = "format") -> None:
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------------------- Code 128


def _digit_run(text: str, start: int) -> int:
    n = start
    while n < len(text) and text[n].isdigit():
        n += 1
    return n - start


def _value_in_set(ch: str, code_set: str) -> int:
    o = ord(ch)
    if code_set == "B":
        if not 32 <= o <= 127:
            raise BarcodeFormatError(f"character {ch!r} is not in Code 128 set B")
        return o - 32
    if code_set == "A":
        if not 0 <= o <= 95:
            raise BarcodeFormatError(f"character {ch!r} is not in Code 128 set A")
        return o + 64 if o < 32 else o - 32
    raise BarcodeFormatError("set C encodes digit pairs only")


def _text_set(text: str, start: int) -> str:
    """A or B for the text that follows ``start``: A only when a control character comes before a lowercase one."""
    for ch in text[start:]:
        o = ord(ch)
        if o < 32:
            return "A"
        if 96 <= o <= 127:
            return "B"
    return "B"


def code128_values(text: str) -> list[int]:
    """Symbol values (start … data … check, stop) with automatic subset selection.

    Rules (common minimal-length heuristics): start in C when the data is ≥ 4 digits at the start (or exactly
    2 digits); in A/B switch to C for a run of ≥ 4 digits at the end or ≥ 6 digits in the middle (an odd run
    encodes its first digit in the current set); leave C when fewer than two digits follow.
    """
    if not text:
        raise BarcodeFormatError("empty value", reason="empty")
    if len(text) > 80:
        raise BarcodeFormatError("Code 128 values are limited to 80 characters", reason="too_long")
    for ch in text:
        if ord(ch) > 127:
            raise BarcodeFormatError(f"character {ch!r} is not ASCII", reason="charset")
    values: list[int] = []
    i = 0
    lead = _digit_run(text, 0)
    if (lead == len(text) == 2) or lead >= 4:
        code_set = "C"
        values.append(START_C)
    else:
        code_set = _text_set(text, 0)
        values.append(START_A if code_set == "A" else START_B)
    while i < len(text):
        if code_set == "C":
            if _digit_run(text, i) >= 2:
                values.append(int(text[i : i + 2]))
                i += 2
                continue
            code_set = _text_set(text, i)
            values.append(CODE_A if code_set == "A" else CODE_B)
            continue
        run = _digit_run(text, i)
        at_end = i + run == len(text)
        if run >= 6 or (at_end and run >= 4):
            if run % 2 == 1:
                values.append(_value_in_set(text[i], code_set))
                i += 1
            values.append(CODE_C)
            code_set = "C"
            continue
        ch = text[i]
        o = ord(ch)
        if code_set == "B" and o < 32:
            code_set = "A"
            values.append(CODE_A)
        elif code_set == "A" and 96 <= o <= 127:
            code_set = "B"
            values.append(CODE_B)
        values.append(_value_in_set(ch, code_set))
        i += 1
    values.append(code128_checksum(values))
    values.append(STOP)
    return values


def code128_checksum(values: list[int]) -> int:
    """(start + Σ value_i × i) mod 103 over the start symbol and the data symbols."""
    return (values[0] + sum(v * n for n, v in enumerate(values[1:], start=1))) % 103


def widths_to_modules(widths: str) -> str:
    out: list[str] = []
    for n, w in enumerate(widths):
        out.append(("1" if n % 2 == 0 else "0") * int(w))
    return "".join(out)


def code128_modules(text: str) -> str:
    return "".join(widths_to_modules(CODE128_PATTERNS[v]) for v in code128_values(text))


def code128_decode_values(modules: str) -> list[int]:
    """Inverse of the module rendering (used by tests to prove the bars decode back to the symbols)."""
    by_pattern = {widths_to_modules(p): v for v, p in enumerate(CODE128_PATTERNS)}
    out: list[int] = []
    i = 0
    while i < len(modules):
        size = 13 if modules[i:].startswith(widths_to_modules(CODE128_PATTERNS[STOP])) else 11
        chunk = modules[i : i + size]
        if chunk not in by_pattern:
            raise BarcodeFormatError("undecodable module sequence", reason="decode")
        out.append(by_pattern[chunk])
        i += size
    return out


# --------------------------------------------------------------------------- GTIN / EAN-13


def gtin_check_digit(body: str) -> int:
    """GS1 mod-10: weights 3,1,3,… from the rightmost body digit."""
    if not body.isdigit() or not body:
        raise BarcodeFormatError("GTIN bodies are digits only", reason="format")
    total = sum(int(d) * (3 if n % 2 == 0 else 1) for n, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def validate_gtin(value: str) -> str:
    """Return ``value`` when it is a GTIN-8/12/13/14 with a correct check digit."""
    v = value.strip()
    if not v.isdigit() or len(v) not in GTIN_LENGTHS:
        raise BarcodeFormatError("a GTIN has 8, 12, 13 or 14 digits", reason="format")
    if gtin_check_digit(v[:-1]) != int(v[-1]):
        raise BarcodeFormatError(f"check digit of {v} is invalid", reason="check_digit")
    return v


def gtin14(value: str) -> str:
    """Zero-padded GTIN-14 form of a valid GTIN (comparison key: UPC-A 12 ≡ EAN-13 with a leading 0)."""
    return validate_gtin(value).zfill(14)


def ean13_from_prefix(prefix: str, reference: int) -> str:
    """EAN-13 = company prefix + item reference (zero-padded) + check digit."""
    if not prefix.isdigit() or not 2 <= len(prefix) <= 11:
        raise BarcodeFormatError("the GS1 company prefix has 2–11 digits", reason="prefix")
    room = 12 - len(prefix)
    if reference < 0 or reference >= 10**room:
        raise BarcodeFormatError(f"the prefix {prefix} has no item references left", reason="exhausted")
    body = prefix + str(reference).zfill(room)
    return body + str(gtin_check_digit(body))


def is_restricted_circulation(prefix_or_value: str) -> bool:
    """GS1 prefixes 20–29: restricted circulation (internal use, never a GS1 licence)."""
    return prefix_or_value[:2] in RESTRICTED_PREFIXES


def ean13_modules(value: str) -> str:
    v = validate_gtin(value)
    if len(v) != 13:
        raise BarcodeFormatError("EAN-13 needs 13 digits", reason="format")
    parity = EAN_PARITY[int(v[0])]
    left = "".join((EAN_L if parity[n] == "L" else EAN_G)[int(d)] for n, d in enumerate(v[1:7]))
    right = "".join(EAN_R[int(d)] for d in v[7:13])
    return "101" + left + "01010" + right + "101"


@dataclass(frozen=True, slots=True)
class Symbol:
    """Rendering input for labels/previews: the module string and its human-readable text."""

    symbology: str
    value: str
    modules: str


def symbol(symbology: str, value: str) -> Symbol:
    if symbology == "EAN13":
        return Symbol("EAN13", value, ean13_modules(value))
    if symbology in ("CODE128", "GTIN14_IMPORTED", "GS1_128", "QR"):
        # QR / GS1-128 labels fall back to a Code 128 symbol of the same value in dev (no 2D encoder in P09).
        return Symbol("CODE128", value, code128_modules(value))
    raise BarcodeFormatError(f"unknown symbology {symbology}", reason="symbology")
