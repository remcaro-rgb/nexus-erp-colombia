"""Django app of the bulk import/export module (P18)."""

from typing import ClassVar

from django.apps import AppConfig


class DataioConfig(AppConfig):
    name = "dataio"
    label = "dataio"
    verbose_name = "Importar y exportar"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: routes at ``api/v1/dataio/`` plus ``GET /api/v1/kpis/datos`` (+ panels).
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/kpis/": ("dataio.urls:kpi_urlpatterns", "kpis-datos")
    }
