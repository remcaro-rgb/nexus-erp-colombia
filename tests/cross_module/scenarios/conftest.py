"""Fixtures of the deterministic seed scenarios S1-S12 (shared/11, prompt P22 §1-§3).

* ``andina``: the demo tenant (companies A and B) on the **real** ledger (P05), audit (P07), sequences,
  idempotency and outbox (P01), in the per-test transaction of pytest-django.
* ``stock``: the DB-backed reference ``StockService`` double (``qa_harness.refstock``) the reference legs
  drive; ``stock.implementation`` is written into the scenario ledger evidence. The module legs
  (``test_*_module_leg_*``) use the real P09/P10/P11... services once installed (``defects.require``).
* ``andina_tx``: the same world committed for real (``transactional_db``) for the threaded concurrency
  scenarios; the scratch tables are dropped afterwards.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
from qa_harness import andina as andina_mod
from qa_harness import refdocs, refstock

#: The scenario business clock (UTC instant of 2026-11-09 10:00 America/Bogota).
NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)


def _stock_service() -> Any:
    return refstock.ReferenceStock()


@pytest.fixture
def scenario_services() -> Iterator[None]:
    from kernel import clock

    with andina_mod.service_overrides(), clock.frozen(NOW):
        yield


@pytest.fixture
def andina(db: Any, scenario_services: None) -> andina_mod.Andina:
    world = andina_mod.bootstrap(years=(2026, 2027))
    world.services["stock"] = _stock_service()
    return world


@pytest.fixture
def stock(andina: andina_mod.Andina) -> Any:
    return andina.services["stock"]


@pytest.fixture
def andina_tx(transactional_db: Any, scenario_services: None) -> Iterator[andina_mod.Andina]:
    world = andina_mod.bootstrap(years=(2026,))
    world.services["stock"] = _stock_service()
    try:
        yield world
    finally:
        from django.db import connections

        connections.close_all()
        refstock.uninstall()
        refdocs.uninstall()
