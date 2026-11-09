"""Django app configuration for the tax module (owner P06)."""

from django.apps import AppConfig


class TaxConfig(AppConfig):
    name = "tax"
    label = "tax"
    verbose_name = "Impuestos y parámetros regulatorios"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from tax import wiring

        wiring.register_services()
