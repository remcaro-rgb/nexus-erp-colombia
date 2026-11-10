"""Django app configuration for inventory (P09a core)."""

from django.apps import AppConfig
from django.conf import settings


class InventoryConfig(AppConfig):
    name = "inventory"
    label = "inventory"
    verbose_name = "Inventario"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from inventory import registry

        registry.register_fields()
        registry.register_branch_reference()
        # StockService / Inventory registration in dev/compose; off under erp.settings.test so the
        # kernel registry tests keep an empty registry (inventory tests register per test).
        default = getattr(settings, "ERP_ENVIRONMENT", "dev") != "test"
        if getattr(settings, "ERP_INVENTORY_AUTOREGISTER", default):
            registry.register_services()
