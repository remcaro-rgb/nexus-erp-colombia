"""Django app of the fixed-assets module (P14)."""

from typing import ClassVar

from django.apps import AppConfig
from django.conf import settings


class AssetsConfig(AppConfig):
    name = "assets"
    label = "assets"
    verbose_name = "Activos fijos"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: routes at ``api/v1/assets/`` plus ``GET /api/v1/kpis/activos`` (+ panels).
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/kpis/": ("assets.urls:kpi_urlpatterns", "kpis-activos")
    }

    def ready(self) -> None:
        # Registers AssetService (assets.interface) in dev/compose; off under erp.settings.test so registry
        # tests stay deterministic; assets tests register it per test (fixture ``asset_services``).
        default = getattr(settings, "ERP_ENVIRONMENT", "dev") != "test"
        if getattr(settings, "ERP_ASSETS_AUTOREGISTER", default):
            from assets import registry

            registry.register_services()
