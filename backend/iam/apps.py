"""Django app configuration for IAM (P04)."""

from django.apps import AppConfig


class IamConfig(AppConfig):
    name = "iam"
    label = "iam"
    verbose_name = "Nexus ERP IAM"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from iam.policy import engine
        from kernel import services
        from kernel.interfaces import PolicyService

        services.register(PolicyService, engine.get_engine(), replace=True)
