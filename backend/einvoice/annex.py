"""Annex profile: versions, code lists and their provenance (B-06, B-09; never invented silently).

Every value carries a ``status``:

* ``OFFICIAL_FILE_PRIOR_VERSION`` — read from an official DIAN file we downloaded and hashed
  (toolbox "Caja de herramientas" FE v1.8, see ``docs/compliance/annex-versions.md``), while the
  adopted annex is FE v1.9 (Res. 227/2025 art. 1.5.1.10.2). Usable in DEV; must be re-verified
  against the v1.9 files before production (B-06).
* ``SYNTHETIC_NOT_LEGALLY_VERIFIED`` — no official file was available in this build (DS v1.1,
  Nómina v1.0, DEE v1.0, RADIAN v1.1 annex files). Labelled fixture; production use is refused.

``require_verified`` is the production gate: anything not ``VERIFIED`` fails closed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from kernel.errors import ValidationFailed

OFFICIAL_PRIOR: Final = "OFFICIAL_FILE_PRIOR_VERSION"
SYNTHETIC: Final = "SYNTHETIC_NOT_LEGALLY_VERIFIED"
VERIFIED: Final = "VERIFIED"

TOOLBOX_FE_18 = "DIAN toolbox FE v1.8 (Caja_de_herramientas_Factura_Electronica_Validacion_Previa.zip)"
ANNEX_FE_18 = "DIAN Anexo Técnico FE v1.8 (Res. 000012/2021) §11.1"


@dataclass(frozen=True, slots=True)
class AnnexVersion:
    family: str
    version: str
    adopted_by: str
    valid_from: dt.date | None
    files_obtained: bool


#: Adopted versions (Res. 227/2025 compiled text, opened 2026-11-10; see annex-versions.md).
ANNEX_VERSIONS: Final[tuple[AnnexVersion, ...]] = (
    AnnexVersion("FE", "1.9", "Res. 227/2025 art. 1.5.1.10.2", None, False),
    AnnexVersion("DEE", "1.0", "Res. 227/2025 art. 1.5.1.10.2", dt.date(2024, 5, 1), False),
    AnnexVersion("DS", "1.1", "Res. 227/2025 (art. 17 Res. 167/2021)", None, False),
    AnnexVersion("NOMINA", "1.0", "Res. 227/2025 (Res. 013/2021)", None, False),
    AnnexVersion("RADIAN", "1.1", "Res. 227/2025 (Res. 085/2022)", None, False),
)


@dataclass(frozen=True, slots=True)
class Code:
    value: str
    label: str
    status: str
    source: str


def _c(value: str, label: str, status: str = OFFICIAL_PRIOR, source: str = TOOLBOX_FE_18) -> Code:
    return Code(value, label, status, source)


#: Document families handled by the gateway → (UBL root, hash kind, type code).
FAMILIES: Final[dict[str, tuple[str, str, Code]]] = {
    "FE": ("Invoice", "CUFE", _c("01", "Factura electrónica de Venta")),
    "FE_EXPORT": ("Invoice", "CUFE", _c("02", "Factura electrónica de venta con propósito de exportación")),
    "FE_CONTINGENCY_DIAN": ("Invoice", "CUFE", _c("04", "Factura electrónica de Venta por Contingencia DIAN")),
    "FE_CONTINGENCY_ISSUER": ("Invoice", "CUFE", _c("03", "Factura de talonario o papel con numeración de contingencia")),
    "NC": ("CreditNote", "CUDE", _c("91", "Nota Crédito")),
    "ND": ("DebitNote", "CUDE", _c("92", "Nota Débito")),
    "DEE_POS": ("Invoice", "CUDE", _c("20", "Documento equivalente electrónico tiquete POS", SYNTHETIC, "DEE annex v1.0 not obtained")),
    "DS": ("Invoice", "CUDS", _c("05", "Documento soporte", SYNTHETIC, "DS annex v1.1 not obtained")),
    "DS_ADJUSTMENT": ("CreditNote", "CUDS", _c("95", "Nota de ajuste al documento soporte", SYNTHETIC, "DS annex v1.1 not obtained")),
    "NOMINA": ("NominaIndividual", "CUNE", _c("102", "Nómina individual", SYNTHETIC, "Nómina annex v1.0 not obtained")),
    "NOMINA_AJUSTE": ("NominaIndividualDeAjuste", "CUNE", _c("103", "Nómina individual de ajuste", SYNTHETIC, "Nómina annex v1.0 not obtained")),
}  # fmt: skip

#: Contingency families (R-FE-05): the issuer-side outage uses the issuer contingency type, a DIAN/PT outage
#: keeps type 01 transmitted late in the provider flow (decision recorded in the handoff).
CONTINGENCY_FAMILY: Final = "FE_CONTINGENCY_ISSUER"

TAX_SCHEMES: Final[dict[str, Code]] = {
    "01": _c("01", "IVA"),
    "03": _c("03", "ICA"),
    "04": _c("04", "INC"),
}

ENVIRONMENTS: Final[dict[str, Code]] = {"1": _c("1", "Producción"), "2": _c("2", "Pruebas")}
#: The only environment this DEV-only edition may put in a document.
TEST_ENVIRONMENT: Final = "2"

#: RADIAN events: codes from the FE v1.8 toolbox example file names ("030 - Acuse_ReciboFEV.xml" …);
#: the RADIAN v1.1 annex itself was not obtained (B-09) → production emission refused.
RADIAN_EVENTS: Final[dict[str, Code]] = {
    "030": _c("030", "Acuse de recibo de la factura electrónica de venta"),
    "031": _c("031", "Reclamo de la factura electrónica de venta"),
    "032": _c("032", "Recibo del bien y/o prestación del servicio"),
    "033": _c("033", "Aceptación expresa"),
    "034": _c("034", "Aceptación tácita"),
}
#: Events only the gateway emits (tacit acceptance is computed, never requested by a user).
SYSTEM_ONLY_EVENTS: Final = frozenset({"034"})
#: Reclamo concept codes ("Concepto de Reclamo.gc", toolbox v1.8).
CLAIM_CONCEPTS: Final[dict[str, Code]] = {
    "01": _c("01", "Documento con inconsistencias"),
    "02": _c("02", "Mercancía no entregada totalmente"),
    "03": _c("03", "Mercancía no entregada parcialmente"),
    "04": _c("04", "Servicio no prestado"),
}
CREDIT_NOTE_CONCEPTS: Final[dict[str, Code]] = {
    "1": _c("1", "Devolución parcial de los bienes y/o no aceptación parcial del servicio"),
    "2": _c("2", "Anulación de factura electrónica"),
    "3": _c("3", "Rebaja o descuento parcial o total"),
    "4": _c("4", "Ajuste de precio"),
    "5": _c("5", "Otros"),
}
DEBIT_NOTE_CONCEPTS: Final[dict[str, Code]] = {
    "1": _c("1", "Intereses"),
    "2": _c("2", "Gastos por cobrar"),
    "3": _c("3", "Cambio del valor"),
    "4": _c("4", "Otro"),
}
ID_TYPES: Final[dict[str, Code]] = {
    "13": _c("13", "Cédula de ciudadanía"),
    "22": _c("22", "Cédula de extranjería"),
    "31": _c("31", "NIT"),
    "41": _c("41", "Pasaporte"),
    "42": _c("42", "Documento de identificación extranjero"),
    "50": _c("50", "NIT de otro país"),
}

#: QR verification URL of the DIAN catalogue (FE v1.8 annex; re-verify for v1.9 — B-06).
QR_BASE_URL: Final = Code(
    "https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey=", "QR (habilitación)", OFFICIAL_PRIOR, ANNEX_FE_18
)


def family(code: str) -> tuple[str, str, Code]:
    try:
        return FAMILIES[code]
    except KeyError:
        raise ValidationFailed(f"unknown fiscal document family {code!r}", field="family") from None


def radian_event(code: str) -> Code:
    try:
        return RADIAN_EVENTS[code]
    except KeyError:
        raise ValidationFailed(f"unknown RADIAN event code {code!r}", field="event_code") from None


def require_verified(code: Code, what: str) -> None:
    """Production gate: only VERIFIED values may be used in production (fail closed)."""
    if code.status != VERIFIED:
        raise ValidationFailed(
            f"{what} {code.value!r} is {code.status} (source: {code.source}); production use refused (B-06/B-09)",
            code="provider_not_configured",
        )


def profile() -> dict[str, object]:
    """JSON description of the profile (API/UI/handoff evidence)."""

    def codes(table: dict[str, Code]) -> list[dict[str, str]]:
        return [{"value": c.value, "label": c.label, "status": c.status, "source": c.source} for c in table.values()]

    return {
        "versions": [
            {"family": v.family, "version": v.version, "adopted_by": v.adopted_by,
             "valid_from": v.valid_from.isoformat() if v.valid_from else None, "files_obtained": v.files_obtained}
            for v in ANNEX_VERSIONS
        ],
        "families": {k: {"root": r, "hash": h, "type_code": t.value, "status": t.status} for k, (r, h, t) in FAMILIES.items()},
        "radian_events": codes(RADIAN_EVENTS),
        "tax_schemes": codes(TAX_SCHEMES),
        "environments": codes(ENVIRONMENTS),
    }  # fmt: skip
