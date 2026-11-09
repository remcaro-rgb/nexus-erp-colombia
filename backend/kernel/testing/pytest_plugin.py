"""Pytest plugin shared by every module (``-p kernel.testing.pytest_plugin``).

Kernel modules are imported lazily inside fixtures so that coverage, which
starts after plugins load, measures them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from hypothesis import settings as hypothesis_settings

if TYPE_CHECKING:
    from kernel.clock import FrozenClock
    from kernel.tenancy import TenantContext

# No on-disk example database: ``.hypothesis/`` is not gitignored (protected file).
hypothesis_settings.register_profile("erp", database=None, deadline=None)
hypothesis_settings.load_profile("erp")


@pytest.fixture
def tenant_ctx() -> TenantContext:
    from kernel.testing.tenancy import make_context

    return make_context()


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    import datetime as dt

    from kernel import clock

    with clock.frozen(dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)) as fc:
        yield fc
