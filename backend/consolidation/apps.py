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
