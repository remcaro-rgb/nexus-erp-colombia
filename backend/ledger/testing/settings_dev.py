"""Dev/test settings with the ledger installed (until CCR P05-1 adds it to erp.settings).

Used for ``makemigrations ledger`` and for running the ledger API locally:
``DJANGO_SETTINGS_MODULE=ledger.testing.settings_dev``. It changes nothing about
authentication or authorization (deny by default stays).
"""

from erp.settings.dev import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "ledger"]  # noqa: F405
ROOT_URLCONF = "ledger.testing.urls"
