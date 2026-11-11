"""Barcode images for documents (labels, D-004/D-005) and an independent Code 128 / EAN-13 image decoder.

* :func:`bars_image` turns a module string (``"1"`` bar, ``"0"`` space, as produced by P09's pure encoders in
  ``inventory.barcodes.core``) into an 8-bit grayscale raster with quiet zones.
* :func:`decode_image` reads a raster back: it thresholds the middle row, measures bar/space runs, derives the
  module width from the start symbol, maps the run widths to symbol values with the standard Code 128 width
  table and decodes subsets A/B/C (shift and code switches), verifying the mod-103 check symbol; EAN-13 is
  decoded from its guard pattern with the L/G/R tables and its mod-10 check digit. It never consults the label data.
"""

from __future__ import annotations

from documents.pdf.writer import Image

QUIET = 10
START_A, START_B, START_C, STOP = 103, 104, 105, 106
CODE_A, CODE_B, CODE_C, SHIFT = 101, 100, 99, 98


class BarcodeDecodeError(ValueError):
    pass


def bars_image(modules: str, *, scale: int = 2, height: int = 40, quiet: int = QUIET) -> Image:
    if not modules or set(modules) - {"0", "1"}:
        raise ValueError("a module string contains only 0 and 1")
    padded = "0" * quiet + modules + "0" * quiet
    row = b"".join((b"\x00" if m == "1" else b"\xff") * scale for m in padded)
    return Image(len(row), height, row * height)


def _patterns() -> dict[str, int]:
    from inventory.barcodes.core import CODE128_PATTERNS

    return {p: v for v, p in enumerate(CODE128_PATTERNS)}


def _runs(row: bytes) -> list[int]:
    bits = [1 if px < 128 else 0 for px in row]
    if 1 not in bits:
        raise BarcodeDecodeError("no bars found")
    first = bits.index(1)
    last = len(bits) - 1 - bits[::-1].index(1)
    bits = bits[first : last + 1]
    runs: list[int] = []
    current, count = bits[0], 0
    for b in bits:
        if b == current:
            count += 1
        else:
            runs.append(count)
            current, count = b, 1
    runs.append(count)
    return runs


def _modules_of(runs: list[int], unit: float) -> list[int]:
    out = [max(1, round(r / unit)) for r in runs]
    return out


def _decode_code128(runs: list[int]) -> str:
    if len(runs) < 6 + 7:
        raise BarcodeDecodeError("too few bars for Code 128")
    unit = sum(runs[:6]) / 11  # every Code 128 symbol is 11 modules wide (stop: 13)
    widths = _modules_of(runs, unit)
    table = _patterns()
    values: list[int] = []
    i = 0
    while i < len(widths):
        if len(widths) - i == 7:
            chunk = "".join(str(w) for w in widths[i : i + 7])
            if table.get(chunk) != STOP:
                raise BarcodeDecodeError("missing stop symbol")
            values.append(STOP)
            break
        chunk = "".join(str(w) for w in widths[i : i + 6])
        if chunk not in table:
            raise BarcodeDecodeError(f"unknown symbol pattern {chunk}")
        values.append(table[chunk])
        i += 6
    if not values or values[-1] != STOP or values[0] not in (START_A, START_B, START_C):
        raise BarcodeDecodeError("not a Code 128 symbol")
    data, check = values[1:-2], values[-2]
    expected = (values[0] + sum(v * (n + 1) for n, v in enumerate(data))) % 103
    if check != expected:
        raise BarcodeDecodeError("Code 128 check symbol mismatch")
    code_set = {START_A: "A", START_B: "B", START_C: "C"}[values[0]]
    out: list[str] = []
    shift = False
    for v in data:
        active = ("B" if code_set == "A" else "A") if shift else code_set
        shift = False
        if active == "C":
            if v < 100:
                out.append(f"{v:02d}")
            elif v == CODE_A:
                code_set = "A"
            elif v == CODE_B:
                code_set = "B"
            else:
                raise BarcodeDecodeError(f"unsupported value {v} in subset C")
            continue
        if v == SHIFT:
            shift = True
        elif v == CODE_C:
            code_set = "C"
        elif v == CODE_A and active == "B":
            code_set = "A"
        elif v == CODE_B and active == "A":
            code_set = "B"
        elif v < 96:
            if active == "A":
                out.append(chr(v + 32) if v < 64 else chr(v - 64))
            else:
                out.append(chr(v + 32))
        else:
            raise BarcodeDecodeError(f"unsupported function value {v}")
    return "".join(out)


_EAN_L = ("3211", "2221", "2122", "1411", "1132", "1231", "1114", "1312", "1213", "3112")
_EAN_G = tuple(p[::-1] for p in _EAN_L)
_EAN_FIRST = {
    "LLLLLL": 0, "LLGLGG": 1, "LLGGLG": 2, "LLGGGL": 3, "LGLLGG": 4,
    "LGGLLG": 5, "LGGGLL": 6, "LGLGLG": 7, "LGLGGL": 8, "LGGLGL": 9,
}  # fmt: skip


def _decode_ean13(runs: list[int]) -> str:
    unit = sum(runs[:3]) / 3
    widths = _modules_of(runs, unit)
    if len(widths) != 59 or widths[:3] != [1, 1, 1]:
        raise BarcodeDecodeError("not an EAN-13 symbol")
    left = widths[3:27]
    right = widths[32:56]
    digits: list[str] = []
    parity = ""
    for n in range(6):
        chunk = "".join(str(w) for w in left[n * 4 : n * 4 + 4])
        if chunk in _EAN_L:
            digits.append(str(_EAN_L.index(chunk)))
            parity += "L"
        elif chunk in _EAN_G:
            digits.append(str(_EAN_G.index(chunk)))
            parity += "G"
        else:
            raise BarcodeDecodeError("bad EAN-13 left digit")
    for n in range(6):
        chunk = "".join(str(w) for w in right[n * 4 : n * 4 + 4])
        if chunk not in _EAN_L:
            raise BarcodeDecodeError("bad EAN-13 right digit")
        digits.append(str(_EAN_L.index(chunk)))
    if parity not in _EAN_FIRST:
        raise BarcodeDecodeError("bad EAN-13 parity")
    value = str(_EAN_FIRST[parity]) + "".join(digits)
    total = sum(int(d) * (3 if n % 2 == 0 else 1) for n, d in enumerate(reversed(value[:-1])))
    if (10 - total % 10) % 10 != int(value[-1]):
        raise BarcodeDecodeError("EAN-13 check digit mismatch")
    return value


def decode_runs(runs: list[int]) -> tuple[str, str]:
    """``(symbology, value)`` from bar/space run widths (pixels) of one scan line."""
    if len(runs) == 59:
        try:
            return "EAN13", _decode_ean13(runs)
        except BarcodeDecodeError:
            pass
    return "CODE128", _decode_code128(runs)


def decode_image(width: int, height: int, pixels: bytes) -> tuple[str, str]:
    """Decode the middle scan line of an 8-bit grayscale raster."""
    y = height // 2
    return decode_runs(_runs(pixels[y * width : (y + 1) * width]))
