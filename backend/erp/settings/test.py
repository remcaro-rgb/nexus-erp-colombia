"""Test profile used by pytest (real PostgreSQL, no external I/O)."""

import os

os.environ.setdefault("ERP_ENVIRONMENT", "test")

import base64
import secrets

from erp.settings.base import *  # noqa: F403
from erp.settings.base import field_encryption_key

ERP_ENVIRONMENT = "test"
SECRET_KEY = "test-only-not-a-secret"  # noqa: S105 - test profile, never deployed
CELERY_TASK_ALWAYS_EAGER = True
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Ephemeral per-process key unless one is provided (test databases are thrown away).
ERP_FIELD_ENCRYPTION_KEY = field_encryption_key(os.environ, required=False) or base64.urlsafe_b64encode(
    secrets.token_bytes(32)
).decode("ascii")
