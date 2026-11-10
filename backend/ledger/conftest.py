"""Installs the ledger app for pytest until the settings CCR (P05-1) lands.

``backend/erp/settings`` belongs to P01; the ledger cannot edit
``INSTALLED_APPS``. This conftest is loaded while pytest collects
``backend/ledger`` (before any test database exists), so the app, its models and
its migrations are part of the test database. Once ``ledger`` is in
``INSTALLED_APPS`` this is a no-op.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings

if "ledger" not in settings.INSTALLED_APPS:
    settings.ERP_LEDGER_AUTOREGISTER = False
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "ledger"]
    apps.set_installed_apps(settings.INSTALLED_APPS)
