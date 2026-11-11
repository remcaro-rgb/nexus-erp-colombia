"""Standard-14 font metrics (Helvetica, Helvetica-Bold; Courier is monospaced 600) in 1/1000 em.

Only the metrics needed for alignment: ASCII 32–126 from the Adobe AFM files; Latin-1 letters with diacritics use
the width of their base letter (á → a, Ñ → N) and any other glyph uses the width of ``n``. The PDF references the
fonts by name with ``/WinAnsiEncoding`` (no embedding), so text is limited to Windows-1252.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal

_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 "
    "278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 "
    "611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 "
    "556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 "
    "333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 "
    "611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889 "
    "611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)


def _table(raw: str) -> dict[str, int]:
    return {chr(32 + n): int(w) for n, w in enumerate(raw.split())}


WIDTHS: dict[str, dict[str, int]] = {
    "Helvetica": _table(_HELVETICA),
    "Helvetica-Bold": _table(_HELVETICA_BOLD),
}
MONOSPACED = {"Courier": 600, "Courier-Bold": 600}
FONTS = ("Helvetica", "Helvetica-Bold", "Courier", "Courier-Bold")


def _glyph_width(table: dict[str, int], ch: str) -> int:
    width = table.get(ch)
    if width is not None:
        return width
    base = unicodedata.normalize("NFD", ch)[:1]
    return table.get(base, table["n"])


Number = int | float | Decimal


def text_width(text: str, font: str, size: Number) -> Decimal:
    """Width in points of ``text`` set in ``font`` at ``size`` (Decimal arithmetic, deterministic)."""
    points = Decimal(str(size))
    if font in MONOSPACED:
        return Decimal(len(text) * MONOSPACED[font]) * points / 1000
    table = WIDTHS[font]
    return Decimal(sum(_glyph_width(table, ch) for ch in text)) * points / 1000


def fit(text: str, font: str, size: Number, width: Number) -> str:
    """``text`` shortened with an ellipsis so that it fits in ``width`` points."""
    limit = Decimal(str(width))
    if text_width(text, font, size) <= limit:
        return text
    out = text
    while out and text_width(out + "…", font, size) > limit:
        out = out[:-1]
    return out + "…" if out else ""


def wrap(text: str, font: str, size: Number, width: Number) -> list[str]:
    """Greedy word wrap of ``text`` into lines of at most ``width`` points (long words are cut)."""
    limit = Decimal(str(width))
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, font, size) <= limit:
                current = candidate
                continue
            if current:
                lines.append(current)
            while text_width(word, font, size) > limit and len(word) > 1:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], font, size) > limit:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines
