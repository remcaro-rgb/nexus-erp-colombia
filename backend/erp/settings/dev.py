"""DEV runtime profile (the only runtime profile of this edition)."""

import os

from erp.settings.base import *  # noqa: F403
from erp.settings.base import env, field_encryption_key

ERP_ENVIRONMENT = "dev"
SECRET_KEY = env.str("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    from django.core.management.utils import get_random_secret_key

    # Per-process random key: sessions do not survive a restart unless the
    # operator provides DJANGO_SECRET_KEY from the generated secrets file.
    SECRET_KEY = get_random_secret_key()

# Required at runtime (FIX-D-W01-1): a clear ImproperlyConfigured names the variable when absent.
ERP_FIELD_ENCRYPTION_KEY = field_encryption_key(os.environ, required=True)
