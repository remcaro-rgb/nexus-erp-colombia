"""Fixtures of the POS tests: real PostgreSQL with RLS, REAL P09 stock + REAL P05 ledger, kernel doubles otherwise."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kernel import clock
from pos.testing.support import PosEnv, build, pos_services
from sales.testing.support import NOW, ApiClient


@pytest.fixture
def pos_env(db: Any, settings: Any) -> Iterator[PosEnv]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "sales.testing.support.resolve_context"
    with clock.frozen(NOW), pos_services() as fakes:
        yield build(fakes)


@pytest.fixture
def failing_env(db: Any, settings: Any) -> Iterator[PosEnv]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "sales.testing.support.resolve_context"
    with clock.frozen(NOW), pos_services(failing_stock=True) as fakes:
        yield build(fakes)


@pytest.fixture
def api(pos_env: PosEnv) -> ApiClient:
    return ApiClient(pos_env.world, module="pos")
