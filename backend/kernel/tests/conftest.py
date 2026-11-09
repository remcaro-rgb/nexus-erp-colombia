from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, connections

from kernel.models import Sequence
from kernel.testing.tenancy import bound, first_company, make_context


@pytest.fixture
def ctx():
    return make_context()


@pytest.fixture
def company(ctx):
    return first_company(ctx)


@pytest.fixture
def make_sequence(ctx, company):
    def _make(**kw: Any) -> Sequence:
        kw.setdefault("sequence_code", "FV")
        with bound(ctx):  # kernel_sequence has forced RLS (kernel migration 0002)
            return Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, **kw)

    return _make


def run_threads(n: int, target: Callable[[int], Any]) -> tuple[list[Any], list[BaseException]]:
    """Run ``target(i)`` in ``n`` threads, each with its own DB connection."""
    results: list[Any] = [None] * n
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def wrapper(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = target(i)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results, errors


@pytest.fixture
def raw_sql():
    def _run(sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        with connection.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall() if cur.description else []

    return _run
