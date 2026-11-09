"""NIT check digit (dígito de verificación, DV) and identification document types.

The DV algorithm (weights 3, 7, 13, … 71 applied right-to-left, modulo 11) is
the one stated in the P06 prompt. Document-type codes for the DIAN annex are
master data owned with P15 (``localization_co.code_lists``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kernel.errors import ValidationFailed

WEIGHTS: tuple[int, ...] = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
MAX_DIGITS = len(WEIGHTS)

_SEPARATORS = re.compile(r"[\s.\-,]")


class NitInvalid(ValidationFailed):
    code, status, title = "nit_invalid", 422, "NIT o dígito de verificación inválido"


def normalize(raw: str) -> str:
    """Strip dots, spaces, commas and hyphens; keep digits only (raises on anything else)."""
    if not isinstance(raw, str):
        raise NitInvalid("el NIT debe ser texto", field="nit")
    digits = _SEPARATORS.sub("", raw)
    if not digits or not digits.isdigit():
        raise NitInvalid("el NIT solo puede contener dígitos", field="nit")
    return digits


def check_digit(number: str) -> int:
    """Return the DV for ``number`` (digits only, without the DV)."""
    digits = normalize(number)
    if len(digits) > MAX_DIGITS:
        raise NitInvalid(f"el NIT admite como máximo {MAX_DIGITS} dígitos", field="nit")
    total = sum(int(d) * w for d, w in zip(reversed(digits), WEIGHTS, strict=False))
    remainder = total % 11
    return remainder if remainder in (0, 1) else 11 - remainder


@dataclass(frozen=True, slots=True)
class NitValidation:
    number: str
    check_digit: int
    provided_check_digit: int | None
    valid: bool

    @property
    def formatted(self) -> str:
        """``900.123.456-7`` style (es-CO thousands separator)."""
        groups = f"{int(self.number):,}".replace(",", ".")
        return f"{groups}-{self.check_digit}"

    def to_json(self) -> dict[str, object]:
        return {
            "number": self.number,
            "check_digit": self.check_digit,
            "provided_check_digit": self.provided_check_digit,
            "valid": self.valid,
            "formatted": self.formatted,
        }


def validate(number: str, provided_dv: str | int | None = None) -> NitValidation:
    """Validate ``number`` (optionally ``"900123456-7"``) against ``provided_dv``.

    When no DV is supplied (neither separately nor after a hyphen) the result
    is ``valid`` with the computed DV, so callers can fill the field.
    """
    raw = number.strip() if isinstance(number, str) else number
    if provided_dv is None and isinstance(raw, str) and "-" in raw:
        raw, _, tail = raw.rpartition("-")
        provided_dv = tail
    digits = normalize(raw)
    dv = check_digit(digits)
    given: int | None = None
    if provided_dv is not None and str(provided_dv).strip() != "":
        text = str(provided_dv).strip()
        if not text.isdigit() or len(text) != 1:
            raise NitInvalid("el dígito de verificación es un solo dígito", field="dv")
        given = int(text)
    return NitValidation(digits, dv, given, given is None or given == dv)


def require_valid(number: str, provided_dv: str | int | None = None) -> NitValidation:
    result = validate(number, provided_dv)
    if not result.valid:
        raise NitInvalid(
            f"dígito de verificación {result.provided_check_digit} no corresponde (esperado {result.check_digit})",
            field="dv",
        )
    return result


@dataclass(frozen=True, slots=True)
class DocumentType:
    code: str
    name: str
    person_types: tuple[str, ...]
    has_check_digit: bool = False


# Internal identification document types (P06 prompt list). The DIAN annex code
# for each lives in the versioned code list ``dian.document_type`` (P15/P06).
DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    DocumentType("CC", "Cédula de ciudadanía", ("NATURAL",)),
    DocumentType("CE", "Cédula de extranjería", ("NATURAL",)),
    DocumentType("NIT", "Número de identificación tributaria", ("NATURAL", "JURIDICA"), True),
    DocumentType("PP", "Pasaporte", ("NATURAL",)),
    DocumentType("TI", "Tarjeta de identidad", ("NATURAL",)),
    DocumentType("PEP", "Permiso especial de permanencia", ("NATURAL",)),
    DocumentType("PPT", "Permiso por protección temporal", ("NATURAL",)),
    DocumentType("NIT_EXT", "NIT de otro país", ("NATURAL", "JURIDICA")),
)

DOCUMENT_TYPES_BY_CODE = {d.code: d for d in DOCUMENT_TYPES}
