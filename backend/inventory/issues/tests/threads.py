"""Barrier-started threads, one DB connection each (same pattern as the P09a concurrency tests)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from django.db import connections


def run_threads(n: int, target: Callable[[int], Any]) -> tuple[list[Any], list[BaseException]]:
    results: list[Any] = [None] * n
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def wrapper(i: int) -> None:
        try:
            barrier.wait(timeout=60)
            results[i] = target(i)
        except BaseException as exc:  # collected and asserted by the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    return results, errors
