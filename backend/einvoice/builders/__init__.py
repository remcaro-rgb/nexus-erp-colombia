"""UBL 2.1 builders (pure functions ``FiscalDocumentModel`` → XML bytes), versioned by annex version.

``build(model, unique_code, ...)`` dispatches on the family; ``BUILDERS`` maps
``(annex_version, root)`` → builder so a new annex version adds builders with its ``valid_from``
instead of editing the old ones (golden files per version, ``einvoice/tests/golden/``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from einvoice.builders import ubl
from einvoice.model import FiscalDocumentModel

Builder = Callable[..., bytes]

#: (annex version, UBL root) → builder; the profile valid from ``valid_from`` (None = since the first version).
BUILDERS: dict[tuple[str, str], tuple[dt.date | None, Builder]] = {
    ("1.9", "Invoice"): (None, ubl.invoice),
    ("1.9", "CreditNote"): (None, ubl.credit_note),
    ("1.9", "DebitNote"): (None, ubl.debit_note),
    ("1.9", "NominaIndividual"): (None, ubl.nomina),
    ("1.9", "NominaIndividualDeAjuste"): (None, ubl.nomina),
}
CURRENT_VERSION = "1.9"


def build(
    model: FiscalDocumentModel,
    unique_code: str,
    *,
    environment: str,
    software_id: str,
    qr_url: str,
    annex_version: str = CURRENT_VERSION,
) -> bytes:
    entry = BUILDERS.get((annex_version, model.root))
    if entry is None:
        raise KeyError(f"no builder for annex {annex_version} / {model.root}")
    return entry[1](model, unique_code, environment=environment, software_id=software_id, qr_url=qr_url)


__all__ = ["BUILDERS", "CURRENT_VERSION", "build", "ubl"]
