"""Django app configuration for Colombian localization (owner P06)."""

from django.apps import AppConfig


class LocalizationCoConfig(AppConfig):
    name = "localization_co"
    label = "localization_co"
    verbose_name = "Localización Colombia"
    default_auto_field = "django.db.models.BigAutoField"
