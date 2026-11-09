"""Shared pytest glue for the P22 suites (imported from each suite's ``conftest.py``)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HARNESS_PARENT = Path(__file__).resolve().parents[1]

MARKERS = (
    "req(*urs): user requirements (UR-xx, shared/08) this test proves; feeds docs/traceability.csv",
    "defect(id): known defect filed by P22 (strict xfail until the owner fixes it)",
)


def ensure_importable() -> None:
    if str(HARNESS_PARENT) not in sys.path:
        sys.path.insert(0, str(HARNESS_PARENT))


def register_markers(config: Any) -> None:
    for line in MARKERS:
        config.addinivalue_line("markers", line)
