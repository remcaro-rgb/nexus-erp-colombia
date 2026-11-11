"""P22 suite configuration: the shared harness lives in ``tests/cross_module/qa_harness``."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_PARENT = Path(__file__).resolve().parents[1] / "cross_module"
if str(_HARNESS_PARENT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_PARENT))

from qa_harness.pytest_support import register_markers  # noqa: E402


def pytest_configure(config):
    register_markers(config)


def pytest_collection_finish(session):
    """CCR P11b-1: the P11b models/migrations join the ``purchasing`` app only once their packages are imported.

    Import them after collection (Django is configured) and before the session's test database is created, so a
    fresh test database always has every table, whatever subset of tests runs."""
    import importlib

    for name in ("purchasing.requisitions", "purchasing.portal"):
        importlib.import_module(name)
