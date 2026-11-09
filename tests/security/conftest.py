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
