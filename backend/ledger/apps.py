"""Django app configuration for the ledger (P05)."""

from django.apps import AppConfig
from django.conf import settings


class LedgerConfig(AppConfig):
    name = "ledger"
    label = "ledger"
    verbose_name = "Contabilidad (libro mayor)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Registers PostingService, PeriodService and Dimensions (kernel.services).
        # The pytest bootstrap (ledger/conftest.py) disables it so the kernel's own
        # registry tests keep an empty registry; ledger tests register per test.
        if getattr(settings, "ERP_LEDGER_AUTOREGISTER", True):
            from ledger import registry

            registry.register_services()
