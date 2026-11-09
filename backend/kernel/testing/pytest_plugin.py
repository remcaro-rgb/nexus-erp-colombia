"""Pytest plugin shared by every module (``-p kernel.testing.pytest_plugin``)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kernel import clock
from kernel.tenancy import TenantContext
from kernel.testing.tenancy import make_context


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return make_context()


@pytest.fixture
def frozen_clock() -> Iterator[clock.FrozenClock]:
    import datetime as dt

    with clock.frozen(dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)) as fc:
        yield fc
