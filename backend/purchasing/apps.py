"""Django app of the purchasing module (P11a core).

Routes are mounted by ``erp.discovery`` at ``api/v1/purchasing/`` (no settings edit needed).
"""

from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    name = "purchasing"
    label = "purchasing"
    verbose_name = "Compras y cuentas por pagar"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from purchasing.api.serializers import register_fields

        register_fields()
