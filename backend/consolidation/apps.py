"""Django app of intercompany + consolidation (P16b)."""

from typing import ClassVar

from django.apps import AppConfig


class ConsolidationConfig(AppConfig):
    name = "consolidation"
    label = "consolidation"
    verbose_name = "Intercompañía y consolidación"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: routes at ``api/v1/consolidation/`` plus the frozen ``intercompany.yaml`` routes at
    #: ``api/v1/intercompany/`` (same module: P16b owns the intercompany scope of P16).
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/intercompany/": ("consolidation.urls:intercompany_urlpatterns", "intercompany")
    }

    def ready(self) -> None:
        # Registers IntercompanyService (consolidation.interface) in dev/compose; off under erp.settings.test so
        # registry tests stay deterministic; the module's tests register it per test.
        from django.conf import settings

        from consolidation import access  # noqa: F401  (registers the [A] ``consolidation`` feature flag)

        default = getattr(settings, "ERP_ENVIRONMENT", "dev") != "test"
        if getattr(settings, "ERP_CONSOLIDATION_AUTOREGISTER", default):
            from consolidation import registry

            registry.register_services()
