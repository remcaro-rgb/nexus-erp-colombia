"""Celery application. Tasks bind tenancy through :class:`kernel.tasks.TenantTask`."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "erp.settings.dev")

app = Celery("erp")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
