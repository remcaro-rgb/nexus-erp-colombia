"""Django app of the treasury module (P16a: funds, RC/CE vouchers, payment batches, bank statements).

Routes are mounted by ``erp.discovery`` at ``api/v1/treasury/`` (no settings edit needed). ``ready`` registers the
field classifications (P04 field policy) and the services other modules consume: ``PaymentStatus`` (portals) and
the AP open-item adapter used by CE vouchers (``treasury.interfaces.ApOpenItems``, CCR P16a-1).
"""

from typing import ClassVar

from django.apps import AppConfig


class TreasuryConfig(AppConfig):
    name = "treasury"
    label = "treasury"
    verbose_name = "Tesorería"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: routes at ``api/v1/treasury/`` plus ``GET /api/v1/kpis/tesoreria[/panels/flujo_caja]``.
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/kpis/": ("treasury.urls:kpi_urlpatterns", "kpis-tesoreria")
    }

    def ready(self) -> None:
        from kernel import services
        from kernel.interfaces.treasury import PaymentStatus
        from treasury.api.serializers import register_fields
        from treasury.interfaces import ApOpenItems, TreasuryPaymentStatus
        from treasury.vouchers.ap import PurchasingApOpenItems

        register_fields()
        if services.optional(PaymentStatus) is None:
            status: PaymentStatus = TreasuryPaymentStatus()
            services.register(PaymentStatus, status)
        if services.optional(ApOpenItems) is None:
            ap: ApOpenItems = PurchasingApOpenItems()
            services.register(ApOpenItems, ap)
