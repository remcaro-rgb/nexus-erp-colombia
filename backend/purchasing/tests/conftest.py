"""Fixtures of the purchasing tests (real PostgreSQL; kernel contract doubles for other modules)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kernel import clock
from purchasing.testing.support import NOW, ApiClient, Fakes, World, fake_services, make_world


@pytest.fixture
def fakes() -> Iterator[Fakes]:
    with clock.frozen(NOW), fake_services() as f:
        yield f


@pytest.fixture
def world(db: Any, fakes: Fakes) -> World:
    return make_world(fakes)


@pytest.fixture
def api(settings: Any, world: World) -> ApiClient:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "purchasing.testing.support.resolve_context"
    return ApiClient(world)
