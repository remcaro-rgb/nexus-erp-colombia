"""Runtime registration of the IAM app while the settings/URL CCR is open.

``erp.settings`` and ``erp.urls`` are owned by P01. Until CCR P04-1 adds
``"iam"`` to ``INSTALLED_APPS`` and ``include("iam.urls")`` to the root URL
conf, the test suite registers both at collection time through
:func:`ensure_installed` (called from ``backend/iam/conftest.py``). Once the
CCR is applied the function is a no-op.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.urls import clear_url_caches, include, path


def ensure_installed() -> None:
    if not apps.is_installed("iam"):
        installed = [*settings.INSTALLED_APPS, "iam"]
        settings.INSTALLED_APPS = installed
        apps.app_configs.clear()
        apps.apps_ready = apps.models_ready = apps.ready = False
        apps.loading = False
        apps.populate(installed)
    from erp import urls as root_urls

    if not any(getattr(p, "urlconf_name", None) == "iam.urls" for p in root_urls.urlpatterns):
        import iam.urls  # noqa: F401 - imported so include() resolves the module object

        root_urls.urlpatterns.append(path("", include("iam.urls")))
        clear_url_caches()
