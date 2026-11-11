"""P22 cross-module suite configuration (P22a harness; scenarios S1-S12 arrive with P22b).

* makes ``qa_harness`` importable (this directory on ``sys.path``);
* registers the ``req`` marker used by the traceability generator (``@pytest.mark.req("UR-07")``).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

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
