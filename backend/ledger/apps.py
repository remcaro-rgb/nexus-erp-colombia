"""Django app configuration for the ledger (P05)."""

from django.apps import AppConfig
from django.conf import settings


class LedgerConfig(AppConfig):
    name = "ledger"
    label = "ledger"
    verbose_name = "Contabilidad (libro mayor)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Registers PostingService, PeriodService and Dimensions (kernel.services) in
        # dev/compose. Off by default under erp.settings.test (ERP_ENVIRONMENT == "test")
        # so the kernel's own registry tests keep an empty registry; ledger tests
        # register per test through the ``ledger_services`` fixture.
        default = getattr(settings, "ERP_ENVIRONMENT", "dev") != "test"
        if getattr(settings, "ERP_LEDGER_AUTOREGISTER", default):
            from ledger import registry

            registry.register_services()
