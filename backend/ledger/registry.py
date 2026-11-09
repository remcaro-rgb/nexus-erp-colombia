"""Registers the ledger implementations of kernel interfaces (kernel.services)."""

from __future__ import annotations

from kernel import services
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingService


def implementations() -> dict[object, object]:
    from ledger.dimensions.interface import LedgerDimensions
    from ledger.periods import LedgerPeriodService
    from ledger.posting import LedgerPostingService

    return {
        PostingService: LedgerPostingService(),
        PeriodService: LedgerPeriodService(),
        Dimensions: LedgerDimensions(),
    }


def register_services() -> None:
    for interface, impl in implementations().items():
        if services.optional(interface) is None:  # type: ignore[call-overload]
            services.register(interface, impl)  # type: ignore[call-overload]
