"""Django app of the sales / AR module (P10a core; P10b adds ``sales.portal``).

Routes are mounted by ``erp.discovery`` at ``api/v1/sales/`` (no settings edit needed). ``ready`` registers the
field classifications (P04 field policy) and the services other modules consume: ``ArOpenItems`` (P16) and
``PartyService`` (P11/P13/P16, via ``sales.interfaces``).
"""

from django.apps import AppConfig


class SalesConfig(AppConfig):
    name = "sales"
    label = "sales"
    verbose_name = "Ventas y cartera"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from kernel import services
        from kernel.interfaces.ar import ArOpenItems
        from sales.api.serializers import register_fields
        from sales.party.service import PartyService
        from sales.services.ar import SalesArOpenItems

        register_fields()
        if services.optional(ArOpenItems) is None:
            impl: ArOpenItems = SalesArOpenItems()
            services.register(ArOpenItems, impl)
        if services.optional(PartyService) is None:
            services.register(PartyService, PartyService())
