"""T-INV-BARCODE-01 — Code 128 encoder golden vectors, EAN-13 check digit (mod-10) golden vectors, GTIN validation.

The vectors were computed by hand from the symbology definitions (Code 128: value table of ISO/IEC 15417, check =
(start + Σ value × position) mod 103; GS1 mod-10 with weights 3/1 from the right) and from published examples."""

from __future__ import annotations

from typing import Any

import pytest

from inventory.barcodes import core

CODE128_VECTORS = [
    # text, symbol values (start, data, check, stop)
    ("PJJ123C", [104, 48, 42, 42, 17, 18, 19, 35, 55, 106]),  # set B; a 3-digit run in the middle stays in B
    ("1234", [105, 12, 34, 82, 106]),  # starts in C
    ("12345", [105, 12, 34, 100, 21, 54, 106]),  # odd digit tail: C then CODE B for the last digit
    ("A1234567", [104, 33, 17, 99, 23, 45, 67, 54, 106]),  # odd run of 7 at the end: '1' in B, then C
    ("ALFA-00000001", [104, 33, 44, 38, 33, 13, 99, 0, 0, 0, 1, 7, 106]),  # D-004 internal code format
    ("ab\tc", [104, 65, 66, 101, 73, 100, 67, 47, 106]),  # control char → CODE A, back to B for lowercase
]

EAN13_CHECK_VECTORS = [
    ("400638133393", 1),  # 4006381333931
    ("590123412345", 7),  # 5901234123457
    ("978030640615", 7),  # ISBN-13 9780306406157
    ("290000000001", 8),  # restricted-circulation synthetic code (D-004)
]

GTIN_VALID = ["4006381333931", "036000291452", "012345678905", "00012345678905", "96385074", "5901234123457"]
GTIN_INVALID = ["4006381333932", "036000291453", "1234567", "40063813339", "ABCDEFGHIJKLM", "123456789012345"]


@pytest.mark.parametrize(("text", "values"), CODE128_VECTORS)
def test_T_INV_BARCODE_01_code128_golden_vectors(text: Any, values: Any) -> None:
    assert core.code128_values(text) == values
    modules = core.code128_modules(text)
    # 11 modules per symbol, 13 for STOP; bars and spaces alternate, starting and ending with a bar
    assert len(modules) == 11 * (len(values) - 1) + 13
    assert modules.startswith("11") and modules.endswith("11")
    assert core.code128_decode_values(modules) == values


def test_T_INV_BARCODE_01_code128_table_and_checksum_properties() -> None:
    assert len(core.CODE128_PATTERNS) == 107 and len(set(core.CODE128_PATTERNS)) == 107
    assert all(sum(map(int, p)) == 11 for p in core.CODE128_PATTERNS[:106])
    assert sum(map(int, core.CODE128_PATTERNS[core.STOP])) == 13
    # start B 104 + 'W'=55 … 'a'=65 → check of "Wikipedia" by the definition
    vals = core.code128_values("Wikipedia")
    data = vals[1:-2]
    assert data == [55, 73, 75, 73, 80, 69, 68, 73, 65]
    assert vals[-2] == (104 + sum(v * i for i, v in enumerate(data, start=1))) % 103
    for bad in ("", "ñandú", "x" * 81):
        with pytest.raises(core.BarcodeFormatError):
            core.code128_values(bad)


@pytest.mark.parametrize(("body", "digit"), EAN13_CHECK_VECTORS)
def test_T_INV_BARCODE_01_ean13_check_digit_golden_vectors(body: Any, digit: Any) -> None:
    assert core.gtin_check_digit(body) == digit
    full = body + str(digit)
    assert core.validate_gtin(full) == full
    modules = core.ean13_modules(full)
    assert len(modules) == 95 and modules.startswith("101") and modules.endswith("101") and modules[45:50] == "01010"


def test_T_INV_BARCODE_01_ean13_patterns_and_gtin_validation() -> None:
    # derived tables: R = complement of L, G = reverse of R (EAN-13 standard tables)
    assert core.EAN_R[0] == "1110010" and core.EAN_G[0] == "0100111" and core.EAN_G[9] == "0010111"
    # first digit 4 → parity LGLLGG of the left half (4006381333931)
    left = core.ean13_modules("4006381333931")[3:45]
    assert left[:7] == core.EAN_L[0] and left[7:14] == core.EAN_G[0] and left[14:21] == core.EAN_L[6]
    for v in GTIN_VALID:
        assert core.validate_gtin(v) == v
        assert core.gtin14(v) == v.zfill(14)
    for v in GTIN_INVALID:
        with pytest.raises(core.BarcodeFormatError):
            core.validate_gtin(v)
    with pytest.raises(core.BarcodeFormatError) as exc:
        core.validate_gtin("4006381333932")
    assert exc.value.reason == "check_digit"
    # EAN-13 from a user-entered GS1 prefix: prefix + padded reference + check digit
    assert core.ean13_from_prefix("29", 1) == "2900000000018"
    assert core.is_restricted_circulation("2900000000018") and not core.is_restricted_circulation("4006381333931")
    with pytest.raises(core.BarcodeFormatError):
        core.ean13_from_prefix("29", 10**10)
    with pytest.raises(core.BarcodeFormatError):
        core.ean13_from_prefix("7", 1)
