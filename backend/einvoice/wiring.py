"""App-ready wiring: adapters, kernel services, outbox consumer, log redaction, flags."""

from __future__ import annotations

from kernel import services
from kernel.interfaces.fiscal import FiscalDocuments, FiscalGateway


def register() -> None:
    from einvoice import secrets, worker
    from einvoice.adapters import direct, registry
    from einvoice.gateway import EinvoiceFiscalDocuments, EinvoiceFiscalGateway

    registry.register_builtins()
    direct.register_flag()
    secrets.install_log_filter()
    worker.register()
    if services.optional(FiscalGateway) is None:
        gateway: FiscalGateway = EinvoiceFiscalGateway()
        services.register(FiscalGateway, gateway)
    if services.optional(FiscalDocuments) is None:
        documents: FiscalDocuments = EinvoiceFiscalDocuments()
        services.register(FiscalDocuments, documents)
