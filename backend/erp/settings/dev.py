"""DEV runtime profile (the only runtime profile of this edition)."""

from erp.settings.base import *  # noqa: F403
from erp.settings.base import env

ERP_ENVIRONMENT = "dev"
SECRET_KEY = env.str("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    from django.core.management.utils import get_random_secret_key

    # Per-process random key: sessions do not survive a restart unless the
    # operator provides DJANGO_SECRET_KEY from the generated secrets file.
    SECRET_KEY = get_random_secret_key()
