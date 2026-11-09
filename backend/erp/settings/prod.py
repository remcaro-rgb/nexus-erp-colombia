"""Production profile guard.

This edition is DEV-only (CCR-DEV-001): there is no production configuration.
Importing this module runs the production boot validations and then refuses to
boot. The validations themselves live in :mod:`kernel.boot` and are tested
(T-P01-09) so that a future production edition inherits them.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from kernel.boot import validate_production_settings

validate_production_settings(
    debug=os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes"),
    secret_key=os.environ.get("DJANGO_SECRET_KEY", ""),
    fiscal_mode=os.environ.get("ERP_FISCAL_MODE", "simulated"),
    gate_records_path=os.environ.get("ERP_FISCAL_GATE_RECORDS", ""),
)
raise ImproperlyConfigured("Production profile is out of scope for this DEV-only edition (CCR-DEV-001).")
