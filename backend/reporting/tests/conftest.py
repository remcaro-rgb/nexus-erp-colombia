"""Fixtures of the reporting tests (real PostgreSQL, real P05 ledger and dimension closure; see testing.support)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from reporting.testing.support import ApiClient, World, reporting_world


@pytest.fixture
def world(db: Any, settings: Any) -> Iterator[World]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "reporting.testing.support.resolve_context"
    with reporting_world() as w:
        yield w


@pytest.fixture
def api(world: World) -> ApiClient:
    return ApiClient(world.principals, "analyst")
