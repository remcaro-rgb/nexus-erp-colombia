"""Test profile used by pytest (real PostgreSQL, no external I/O)."""

import os

os.environ.setdefault("ERP_ENVIRONMENT", "test")

from erp.settings.base import *  # noqa: F403

ERP_ENVIRONMENT = "test"
SECRET_KEY = "test-only-not-a-secret"  # noqa: S105 - test profile, never deployed
CELERY_TASK_ALWAYS_EAGER = True
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
