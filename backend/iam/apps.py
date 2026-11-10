"""Django app configuration for IAM (P04)."""

from django.apps import AppConfig


class IamConfig(AppConfig):
    name = "iam"
    label = "iam"
    verbose_name = "Nexus ERP IAM"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery``: iam routes are absolute (``api/v1/iam/*``, ``api/portal/v1/*``) → mounted at the root.
    URL_MOUNT = ""

    def ready(self) -> None:
        from iam.policy import engine
        from kernel import services
        from kernel.interfaces import PolicyService, UiShellProvider

        policy: PolicyService = engine.get_engine()  # structural check of the engine against the protocol
        services.register(PolicyService, policy, replace=True)
        from iam.ui import IamUiShellProvider

        # FIX-UI-LOGIN-W03: the kernel /api/v1/ui/context and /api/v1/ui/modules views read shell data here.
        shell: UiShellProvider = IamUiShellProvider()  # structural check against the protocol
        services.register(UiShellProvider, shell, replace=True)
