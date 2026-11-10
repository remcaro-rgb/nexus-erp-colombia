"""Django app configuration for the time keeping module (owner P13, part P13a)."""

from typing import ClassVar

from django.apps import AppConfig


class TimekeepingConfig(AppConfig):
    name = "timekeeping"
    label = "timekeeping"
    verbose_name = "Control de tiempo"
    default_auto_field = "django.db.models.BigAutoField"
    #: timekeeping.urls holds absolute routes (``api/v1/timekeeping/…``)
    URL_MOUNT: ClassVar[str] = ""
