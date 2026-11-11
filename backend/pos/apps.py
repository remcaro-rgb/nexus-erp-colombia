"""Django app of the POS module (P12, ADR-015 online finalization).

Routes are mounted by ``erp.discovery`` at ``api/v1/pos/`` plus ``GET /api/v1/kpis/pos[/panels/{id}]``
(``EXTRA_URL_MOUNTS``). ``ready`` registers the field classifications (P04 field policy), the ``pos.config``
branch reference (P04 archive check, F-ACC-BRANCH) and the production guard of the ``[A]`` flag
``pos_offline_fiscal_selling`` (T-P12-13: refused without the EG-13 evidence file).
"""

from typing import ClassVar

from django.apps import AppConfig


class PosAppConfig(AppConfig):
    name = "pos"
    label = "pos"
    verbose_name = "Punto de venta"
    default_auto_field = "django.db.models.BigAutoField"
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {"api/v1/kpis/": ("pos.urls:kpi_urlpatterns", "kpis-pos")}

    def ready(self) -> None:
        from pos import registry

        registry.register()
