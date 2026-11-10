"""Django app configuration for the payroll module (owner P13, part P13b)."""

from django.apps import AppConfig


class PayrollConfig(AppConfig):
    name = "payroll"
    label = "payroll"
    verbose_name = "Nómina"
    default_auto_field = "django.db.models.BigAutoField"
