"""Fixtures of the F-PORTAL-CUS tests (real PostgreSQL, policy engine, portal identities; see the test kit)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache

from iam.oidc import verifier
from iam.oidc.local import LocalIssuer
from iam.testing.world import World, make_world
from sales.portal.tests.kit import ISSUER, P10bWorld, make_p10b_world, p10b_services


@pytest.fixture(scope="session")
def _issuer_session() -> LocalIssuer:
    return LocalIssuer(ISSUER)


@pytest.fixture
def issuer(_issuer_session: LocalIssuer) -> Iterator[LocalIssuer]:
    verifier.set_key_provider(_issuer_session, ISSUER)
    try:
        yield _issuer_session
    finally:
        verifier.set_key_provider(None)


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def world(db: Any) -> World:
    return make_world()


@pytest.fixture
def fakes() -> Iterator[dict[str, Any]]:
    with p10b_services() as f:
        yield f


@pytest.fixture
def w(world: Any, issuer: Any, fakes: dict[str, Any]) -> P10bWorld:
    return make_p10b_world(world, issuer)
