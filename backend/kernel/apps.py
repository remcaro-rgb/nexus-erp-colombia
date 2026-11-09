"""Django app configuration for the kernel."""

from django.apps import AppConfig


class KernelConfig(AppConfig):
    name = "kernel"
    label = "kernel"
    verbose_name = "Nexus ERP kernel"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from kernel import boot, io_guard

        io_guard.install()
        boot.connect_db_role_check()
