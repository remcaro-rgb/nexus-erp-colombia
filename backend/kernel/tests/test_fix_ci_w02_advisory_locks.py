"""FIX-CI-W02: typed transaction-level advisory-lock helpers of kernel.tenancy (used by workflow)."""

from __future__ import annotations

import threading

import pytest
from django.db import connections, transaction

from kernel import tenancy
from kernel.errors import TenancyNotBound

KEY = 0x4E58_C1_0002


@pytest.mark.django_db
def test_FIX_CI_W02_advisory_locks_inside_a_transaction() -> None:
    assert connections["default"].in_atomic_block  # pytest-django wraps the test in a transaction
    tenancy.advisory_xact_lock("default", KEY)
    assert tenancy.try_advisory_xact_lock("default", KEY) is True  # re-entrant for the same transaction


@pytest.mark.django_db(transaction=True)
def test_FIX_CI_W02_advisory_locks_outside_atomic_raise() -> None:
    with pytest.raises(TenancyNotBound):
        tenancy.advisory_xact_lock("default", KEY)
    with pytest.raises(TenancyNotBound):
        tenancy.try_advisory_xact_lock("default", KEY)


@pytest.mark.django_db(transaction=True)
def test_FIX_CI_W02_try_lock_is_exclusive_and_released_at_commit() -> None:
    held = threading.Event()
    release = threading.Event()
    other: dict[str, bool] = {}

    def holder() -> None:
        try:
            with transaction.atomic():
                tenancy.advisory_xact_lock("default", KEY)
                held.set()
                release.wait(10)
        finally:
            connections.close_all()

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(10)
    with transaction.atomic():
        other["while_held"] = tenancy.try_advisory_xact_lock("default", KEY)
    release.set()
    t.join(10)
    with transaction.atomic():
        other["after_commit"] = tenancy.try_advisory_xact_lock("default", KEY)
        other["reentrant"] = tenancy.try_advisory_xact_lock("default", KEY)
    assert other == {"while_held": False, "after_commit": True, "reentrant": True}
