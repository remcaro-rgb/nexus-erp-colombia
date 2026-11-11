"""Fixtures of the documents API tests (template CRUD / preview / batch) — the reporting test world (real
PostgreSQL, deny-by-default policy double). RC/CE and label tests have their own worlds in sub-packages."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from reporting.testing.support import ApiClient, World, reporting_world


@pytest.fixture
def rworld(db: Any, settings: Any) -> Iterator[World]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "reporting.testing.support.resolve_context"
    with reporting_world() as w:
        yield w


@pytest.fixture
def dapi(rworld: World) -> ApiClient:
    return ApiClient(rworld.principals, "analyst")
