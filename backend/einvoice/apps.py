"""Django app configuration for the einvoice module (owner P15)."""

from typing import ClassVar

from django.apps import AppConfig


class EinvoiceConfig(AppConfig):
    name = "einvoice"
    label = "einvoice"
    verbose_name = "Facturación electrónica DIAN (simulador en DEV)"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: routes at ``api/v1/einvoice/`` plus ``GET /api/v1/kpis/facturacion``.
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/kpis/facturacion": ("einvoice.urls:kpi_urlpatterns", "kpis-facturacion")
    }

    def ready(self) -> None:
        from einvoice import wiring

        wiring.register()
