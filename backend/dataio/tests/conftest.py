"""Fixtures of the dataio tests (real PostgreSQL, synthetic multi-module world)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

# P11b's requisition migrations join the purchasing app when the part package is imported (CCR P11b-1);
# import it at collection so the test database has its tables whatever subset of tests runs.
import purchasing.requisitions  # noqa: F401
from dataio.testing.world import DataWorld, dataio_services, make_data_world


@pytest.fixture
def svc() -> Iterator[dict[str, Any]]:
    with dataio_services() as s:
        yield s


@pytest.fixture
def w(db: Any, svc: dict[str, Any]) -> DataWorld:
    return make_data_world(svc)
