"""Django app configuration for the HR module (owner P13, part P13a)."""

from typing import ClassVar

from django.apps import AppConfig


class HrConfig(AppConfig):
    name = "hr"
    label = "hr"
    verbose_name = "Talento humano"
    default_auto_field = "django.db.models.BigAutoField"
    #: hr.urls holds absolute routes (internal ``api/v1/hr/…`` and portal ``api/portal/v1/employee/…``)
    URL_MOUNT: ClassVar[str] = ""

    def ready(self) -> None:
        from hr import wiring

        wiring.register_services()
