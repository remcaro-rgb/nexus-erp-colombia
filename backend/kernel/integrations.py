"""Integration adapter modes (DEV-ADDENDUM D-ALL 2).

Only simulated, sandbox and local-capture modes exist in this edition; any
other value (e.g. ``PRODUCTION``/``LIVE``) is refused at settings load.
"""

from __future__ import annotations

from typing import Final

from django.core.exceptions import ImproperlyConfigured

ALLOWED_MODES: Final = frozenset({"DISABLED", "FIXTURE_NOT_CERTIFIED", "SANDBOX", "LOCAL_CAPTURE"})


def validate_integration_modes(modes: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, mode in modes.items():
        normalized = mode.strip().upper()
        if normalized not in ALLOWED_MODES:
            raise ImproperlyConfigured(
                f"integration {name!r}: mode {mode!r} refused in the DEV-only edition "
                f"(allowed: {', '.join(sorted(ALLOWED_MODES))})"
            )
        out[name] = normalized
    return out
