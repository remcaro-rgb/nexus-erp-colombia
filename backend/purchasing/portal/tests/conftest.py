"""Fixtures of the F-PORTAL-SUP tests (real PostgreSQL, policy engine, portal identities; see the test kit)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from iam.tests import conftest as iam_fixtures
from purchasing.requisitions.testkit import P11bWorld, make_p11b_world, p11b_services

_clean_cache = iam_fixtures._clean_cache
_issuer_session = iam_fixtures._issuer_session
issuer = iam_fixtures.issuer
notifications = iam_fixtures.notifications
world = iam_fixtures.world


@pytest.fixture
def fakes() -> Iterator[dict[str, Any]]:
    with p11b_services() as f:
        yield f


@pytest.fixture
def w(world: Any, issuer: Any, fakes: dict[str, Any]) -> P11bWorld:
    return make_p11b_world(world, issuer)
