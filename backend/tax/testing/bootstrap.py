"""Install the P06 apps into the Django app registry for tests.

``INSTALLED_APPS`` lives in ``erp/settings`` (owner P01). Until the integrator
applies CCR P06-1 (add ``"localization_co"`` and ``"tax"``), the test conftests
of P06 call :func:`ensure_installed` at import time — before the test database
is created — so the apps' migrations run like any installed app. Once the
settings list them, this is a no-op.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings

P06_APPS = ("localization_co", "tax")


def ensure_installed() -> bool:
    """Return True when the registry had to be extended."""
    missing = [a for a in P06_APPS if not apps.is_installed(a)]
    if not missing:
        return False
    installed = [*settings.INSTALLED_APPS, *missing]
    settings.INSTALLED_APPS = installed
    apps.set_installed_apps(installed)
    return True
