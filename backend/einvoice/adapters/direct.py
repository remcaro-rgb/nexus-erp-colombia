"""``DirectDianAdapter`` ``[A]`` — own-software habilitación against the DIAN SOAP web services.

Gated: it needs EG-1 (habilitación of our software, TestSetId, software ID/PIN) and EG-2 (the issuer's
signing certificate) plus the method names of the current annex (B-06: the FE v1.8 annex names
``SendBillSync``, ``SendTestSetAsync``, ``GetStatus``, ``GetStatusZip``, ``GetNumberingRange``; v1.9 not
obtained). This edition registers the adapter so the configuration can name it, and every call fails
closed with ``provider_not_configured`` — no SOAP request is ever built against DIAN.
"""

from __future__ import annotations

from einvoice.adapters.base import AdapterConfig, NumberingRangeInfo, SubmitRequest, SubmitResult
from einvoice.errors import ProviderNotConfigured
from kernel import flags

FLAG = "einvoice_direct_dian"
#: Method names read from the FE v1.8 annex (OFFICIAL_FILE_PRIOR_VERSION; re-verify for v1.9).
METHODS = ("SendBillSync", "SendTestSetAsync", "GetStatus", "GetStatusZip", "GetNumberingRange")


class DirectDianAdapter:
    key = "direct"
    simulated = False
    certified = False

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config

    def _refuse(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"direct DIAN adapter [A] ({FLAG}) is gated by EG-1/EG-2 and disabled in the DEV-only edition"
        )

    def submit(self, request: SubmitRequest) -> SubmitResult:
        raise self._refuse()

    def status(self, request: SubmitRequest, tracking_id: str) -> SubmitResult:
        raise self._refuse()

    def numbering_ranges(self, company_nit: str, software_id: str) -> list[NumberingRangeInfo]:
        raise self._refuse()


def register_flag() -> None:
    flags.register(FLAG, kind="A")
