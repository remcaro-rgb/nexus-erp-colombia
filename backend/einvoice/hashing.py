"""CUFE / CUDE / CUNE / CUDS — SHA-384 over the annex concatenation (server side only, [P]).

The formulas are data (``FORMULAS``), mirrored byte for byte in ``specs/fiscal-hash/formulas.json``
(a test keeps both equal). Formula provenance:

* ``CUFE``, ``CUDE``, ``CUDE_AR`` — DIAN Anexo Técnico FE v1.8 §11.1.2/§11.1.4/§11.1.5, whose worked
  examples are the test vectors (``specs/fiscal-hash/vectors/annex-fe-1.8.json``); re-verify against v1.9 (B-06).
* ``CUDS``, ``CUNE`` — the DS v1.1 and Nómina v1.0 annex files were not obtained in this build: the field
  order is a labelled synthetic fixture (``SYNTHETIC_NOT_LEGALLY_VERIFIED``) and production use is refused.

The composition string contains the technical key / software PIN: it is never logged nor returned.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Final

from einvoice import annex
from kernel.errors import ValidationFailed

AMOUNT: Final = "amount"
TEXT: Final = "text"
FIXED: Final = "fixed"


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    kind: str = TEXT  # text | amount | fixed
    value: str = ""  # constant for kind=fixed
    secret: bool = False


@dataclass(frozen=True, slots=True)
class Formula:
    kind: str
    scheme_name: str
    status: str
    source: str
    fields: tuple[Field, ...]


_TAXES = (
    Field("CodImp1", FIXED, "01"),
    Field("ValImp1", AMOUNT),
    Field("CodImp2", FIXED, "04"),
    Field("ValImp2", AMOUNT),
    Field("CodImp3", FIXED, "03"),
    Field("ValImp3", AMOUNT),
)
_HEAD = (Field("NumFac"), Field("FecFac"), Field("HorFac"), Field("ValFac", AMOUNT))

FORMULAS: Final[dict[str, Formula]] = {
    "CUFE": Formula(
        "CUFE",
        "CUFE-SHA384",
        annex.OFFICIAL_PRIOR,
        annex.ANNEX_FE_18 + ".2",
        (
            *_HEAD,
            *_TAXES,
            Field("ValTot", AMOUNT),
            Field("NitOFE"),
            Field("NumAdq"),
            Field("ClTec", secret=True),
            Field("TipoAmbiente"),
        ),
    ),
    "CUDE": Formula(
        "CUDE",
        "CUDE-SHA384",
        annex.OFFICIAL_PRIOR,
        annex.ANNEX_FE_18 + ".4",
        (
            *_HEAD,
            *_TAXES,
            Field("ValTot", AMOUNT),
            Field("NitOFE"),
            Field("NumAdq"),
            Field("SoftwarePIN", secret=True),
            Field("TipoAmbiente"),
        ),
    ),
    "CUDE_AR": Formula(
        "CUDE_AR",
        "CUDE-SHA384",
        annex.OFFICIAL_PRIOR,
        annex.ANNEX_FE_18 + ".5",
        (
            Field("NumDE"),
            Field("FecEmi"),
            Field("HorEmi"),
            Field("NitFE"),
            Field("DocAdq"),
            Field("ResponseCode"),
            Field("ID"),
            Field("DocumentTypeCode"),
            Field("SoftwarePIN", secret=True),
        ),
    ),
    "CUDS": Formula(
        "CUDS",
        "CUDS-SHA384",
        annex.SYNTHETIC,
        "DS annex v1.1 not obtained (B-06)",
        (
            Field("NumDS"),
            Field("FecDS"),
            Field("HorDS"),
            Field("ValDS", AMOUNT),
            Field("CodImp", FIXED, "01"),
            Field("ValImp", AMOUNT),
            Field("ValTol", AMOUNT),
            Field("NumSNO"),
            Field("NITABS"),
            Field("SoftwarePIN", secret=True),
            Field("TipoAmbiente"),
        ),
    ),
    "CUNE": Formula(
        "CUNE",
        "CUNE-SHA384",
        annex.SYNTHETIC,
        "Nómina annex v1.0 not obtained (B-06)",
        (
            Field("NumNE"),
            Field("FecNE"),
            Field("HorNE"),
            Field("ValDev", AMOUNT),
            Field("ValDed", AMOUNT),
            Field("ValTolNE", AMOUNT),
            Field("NitNE"),
            Field("DocEmp"),
            Field("TipoXML"),
            Field("SoftwarePIN", secret=True),
            Field("TipoAmbiente"),
        ),
    ),
}


def format_amount(value: object) -> str:
    """Annex rule: dot decimal separator, two decimals *truncated*, no thousands separator, no sign symbol."""
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"invalid amount {value!r} for the fiscal hash") from None
    if not dec.is_finite():
        raise ValidationFailed("non-finite amount for the fiscal hash")
    return str(dec.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def composition(kind: str, values: Mapping[str, object]) -> str:
    """Concatenated input string (contains secrets — never log or return it)."""
    formula = FORMULAS.get(kind)
    if formula is None:
        raise ValidationFailed(f"unknown fiscal hash kind {kind!r}")
    parts: list[str] = []
    for f in formula.fields:
        if f.kind == FIXED:
            parts.append(f.value)
            continue
        if f.name not in values or values[f.name] is None or values[f.name] == "":
            raise ValidationFailed(f"fiscal hash {kind}: missing input {f.name}", field=f.name)
        raw = values[f.name]
        parts.append(format_amount(raw) if f.kind == AMOUNT else str(raw).strip())
    return "".join(parts)


def compute(kind: str, values: Mapping[str, object]) -> str:
    """Lower-case hex SHA-384 of the composition."""
    return hashlib.sha384(composition(kind, values).encode("utf-8")).hexdigest()


def scheme_name(kind: str) -> str:
    return FORMULAS[kind].scheme_name


def formulas_json() -> dict[str, object]:
    """Language-neutral description (equal to ``specs/fiscal-hash/formulas.json``)."""
    return {
        "algorithm": "SHA-384",
        "output": "lowercase hex",
        "amount_format": "dot decimal separator, 2 decimals truncated (ROUND_DOWN), no thousands separator",
        "formulas": {
            k: {
                "scheme_name": f.scheme_name,
                "status": f.status,
                "source": f.source,
                "fields": [
                    {
                        "name": x.name,
                        "kind": x.kind,
                        **({"value": x.value} if x.kind == FIXED else {}),
                        **({"secret": True} if x.secret else {}),
                    }
                    for x in f.fields
                ],
            }
            for k, f in FORMULAS.items()
        },
    }
