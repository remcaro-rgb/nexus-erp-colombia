"""The real P06 ``TaxEngine`` wired to the shared/11 FIX rates (labelled synthetic parameters).

Every parameter is a ``FakeParameterProvider.synthetic`` value: ``synthetic=True``, source
``SYNTHETIC-FIXTURE``, so every computation carries the warning ``FIXTURE — NOT LEGALLY VERIFIED`` in
its snapshots (C1-9). SMMLV and the S9 base are DISPUTED (shared/11). Tax codes mirror
``backend/tax/fixtures/tax_codes_default.yaml`` (applicability rules are data) but point to the
``fix.*`` parameters, never to the official codes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from .golden import FIX

_AGENT_RULES = (
    {"when": {"seller.autorretenedor": True}, "apply": False, "reason": "el vendedor es autorretenedor"},
    {"when": {"buyer.withholding_agent": True}, "apply": True, "reason": "el comprador es agente de retención"},
    {"apply": False, "reason": "el comprador no es agente de retención"},
)


def parameters() -> Any:
    from kernel.parameters import ParameterStatus
    from kernel.testing.fakes import FakeParameterProvider

    def syn(code: str, value: Decimal, status: ParameterStatus = ParameterStatus.VERIFIED_PRIMARY) -> Any:
        return FakeParameterProvider.synthetic(code, value, status)

    disputed = ParameterStatus.DISPUTED
    values = {
        "fix.iva.general": syn("fix.iva.general", FIX["iva.general"]),
        "fix.retefuente.ventas": syn("fix.retefuente.ventas", FIX["retefuente.ventas"]),
        "fix.reteiva": syn("fix.reteiva", FIX["reteiva"]),
        "fix.reteica.bogota": syn("fix.reteica.bogota", FIX["reteica.bogota"]),
        "fix.retefuente.compras": syn("fix.retefuente.compras", FIX["retefuente.compras"]),
        "fix.retefuente.servicios": syn("fix.retefuente.servicios", FIX["retefuente.servicios"]),
        "fix.retefuente.servicios.base_uvt": syn(
            "fix.retefuente.servicios.base_uvt", FIX["retefuente.servicios.base_uvt"], disputed
        ),
        "uvt": syn("uvt", FIX["uvt.2026"]),
    }
    return FakeParameterProvider(values)


def engine() -> Any:
    from tax.engine.engine import TaxEngine
    from tax.engine.types import TaxCodeDef

    codes = {
        "FIX-IVA19": TaxCodeDef("FIX-IVA19", "IVA", "fix.iva.general", account_role="VAT_PAYABLE",
                                applicability=({"when": {"seller.regime": ["RESPONSABLE_IVA"]}, "apply": True},
                                               {"apply": False})),
        "FIX-RF-VENTAS": TaxCodeDef("FIX-RF-VENTAS", "RETEFUENTE", "fix.retefuente.ventas",
                                    account_role="WHT_RECEIVABLE_RENTA", applicability=_AGENT_RULES),
        "FIX-RETEIVA": TaxCodeDef("FIX-RETEIVA", "RETEIVA", "fix.reteiva", base_rule="TAX_AMOUNT",
                                  base_tax_type="IVA", account_role="WHT_RECEIVABLE_IVA",
                                  applicability=({"when": {"buyer.vat_withholding_agent": True}, "apply": True},
                                                 {"apply": False})),
        "FIX-RETEICA": TaxCodeDef("FIX-RETEICA", "RETEICA", "fix.reteica.bogota", account_role="WHT_RECEIVABLE_ICA",
                                  applicability=({"when": {"buyer.withholding_agent": True}, "apply": True},
                                                 {"apply": False})),
        "FIX-RF-COMPRAS": TaxCodeDef("FIX-RF-COMPRAS", "RETEFUENTE", "fix.retefuente.compras",
                                     account_role="WHT_PAYABLE", applicability=_AGENT_RULES),
        "FIX-RF-SERVICIOS": TaxCodeDef("FIX-RF-SERVICIOS", "RETEFUENTE", "fix.retefuente.servicios",
                                       min_base_uvt_parameter_code="fix.retefuente.servicios.base_uvt",
                                       account_role="WHT_PAYABLE", applicability=_AGENT_RULES),
    }  # fmt: skip
    return TaxEngine(parameters(), codes)


def compute(
    company: uuid.UUID,
    day: dt.date,
    lines: list[tuple[str, Decimal, tuple[str, ...]]],
    *,
    buyer_agent: bool,
    seller_regime: str = "RESPONSABLE_IVA",
    buyer_regime: str = "RESPONSABLE_IVA",
) -> Any:
    from tax.engine.types import DocumentInput, DocumentLine, PartyProfile

    buyer = PartyProfile(regime=buyer_regime, withholding_agent=buyer_agent, vat_withholding_agent=buyer_agent)
    seller = PartyProfile(regime=seller_regime)
    doc = DocumentInput(
        company_id=company,
        document_date=day,
        seller=seller,
        buyer=buyer,
        lines=tuple(DocumentLine(ref, base, codes) for ref, base, codes in lines),
        municipality="11001",
    )
    return engine().compute(doc)


def by_type(result: Any) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for t in result.lines:
        out[t.tax_type] = out.get(t.tax_type, Decimal(0)) + t.amount
    return out
