"""Django app of the CRM module (P10a): leads, contacts, opportunities, activities, pipelines, forecast.

Routes are mounted by ``erp.discovery`` at ``api/v1/crm/``.
"""

from django.apps import AppConfig


class CrmConfig(AppConfig):
    name = "crm"
    label = "crm"
    verbose_name = "CRM"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from crm.api import register_fields

        register_fields()
