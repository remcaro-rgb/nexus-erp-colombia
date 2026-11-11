"""Fixtures of the treasury tests (real PostgreSQL, real P05 ledger, real P07 approvals; see testing.support)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from treasury.testing.support import ApiClient, World, treasury_world


@pytest.fixture
def world(db: Any, settings: Any) -> Iterator[World]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "treasury.testing.support.resolve_context"
    with treasury_world() as w:
        yield w


@pytest.fixture
def api(world: World) -> ApiClient:
    return ApiClient(world)
