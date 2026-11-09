import datetime as dt
import threading

import pytest

from kernel import sequences, uow
from kernel.errors import FeatureDisabled, SequenceExhausted, SequenceNotFound, SequenceNotValid, UnitOfWorkRequired
from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound

from .conftest import run_threads


@pytest.mark.django_db(transaction=True)
def test_T_P01_06_gap_free_under_50_concurrent_allocations(ctx, company, make_sequence):
    make_sequence(prefix="SETP", padding=6, range_from=1, range_to=1000, is_fiscal=True)
    failures = threading.Lock()
    rolled_back = []

    def target(i):
        try:
            return uow.run_in_unit_of_work(lambda u: _alloc_maybe_fail(company, i), ctx)
        except _Rollback:
            with failures:
                rolled_back.append(i)
            return None

    results, errors = run_threads(50, target)
    assert errors == []
    numbers = sorted(r.number for r in results if r is not None)
    assert len(rolled_back) == 10
    assert numbers == list(range(1, 41))  # committed numbers have no gaps despite 10 rollbacks
    assert all(r.formatted.startswith("SETP0000") for r in results if r is not None)


class _Rollback(Exception):
    pass


def _alloc_maybe_fail(company, i):
    n = sequences.allocate(company, "FV", "SETP", on_date=dt.date(2026, 11, 9))
    if i % 5 == 0:
        raise _Rollback()  # the number goes back to the pool with the rollback
    return n


@pytest.mark.django_db(transaction=True)
def test_T_P01_06_range_validity_thresholds_and_exhaustion(ctx, company, make_sequence):
    make_sequence(
        prefix="R",
        range_from=91,
        range_to=100,
        valid_from=dt.date(2026, 1, 1),
        valid_to=dt.date(2026, 12, 31),
        resolution_number="SYN-RES-0001",
    )
    day = dt.date(2026, 6, 1)
    got = [
        uow.run_in_unit_of_work(lambda u: sequences.allocate(company, "FV", "R", on_date=day), ctx) for _ in range(10)
    ]
    assert [g.number for g in got] == list(range(91, 101))
    assert got[0].resolution_number == "SYN-RES-0001"
    with bound(ctx):
        thresholds = [
            m.envelope["payload"]["threshold"]
            for m in OutboxMessage.objects.filter(event_type=sequences.THRESHOLD_EVENT)
        ]
    assert sorted(thresholds) == ["0.80", "0.95"]
    with pytest.raises(SequenceExhausted):
        uow.run_in_unit_of_work(lambda u: sequences.allocate(company, "FV", "R", on_date=day), ctx)
    with pytest.raises(SequenceNotValid):
        uow.run_in_unit_of_work(lambda u: sequences.allocate(company, "FV", "R", on_date=dt.date(2027, 1, 1)), ctx)
    with pytest.raises(SequenceNotFound):
        uow.run_in_unit_of_work(lambda u: sequences.allocate(company, "NOPE"), ctx)


@pytest.mark.django_db
def test_T_P01_06_allocation_requires_server_uow_and_company(ctx, company, make_sequence):
    make_sequence()
    with pytest.raises(UnitOfWorkRequired):
        sequences.allocate(company, "FV")
    with uow.unit_of_work(ctx):
        n = sequences.allocate(company, "FV")
        assert (n.number, n.formatted, n.is_fiscal) == (1, "1", False)
    seq = make_sequence(sequence_code="INACTIVE", active=False)
    with pytest.raises(SequenceNotValid), uow.unit_of_work(ctx):
        sequences.allocate(company, seq.sequence_code)


@pytest.mark.django_db
def test_T_P01_06_terminal_block_allocation_fails_closed(ctx, company):
    with pytest.raises(FeatureDisabled), uow.unit_of_work(ctx):
        sequences.allocate_terminal_block(company, "POS", 10)
    from kernel.models import FeatureFlagOverride

    with bound(ctx):
        FeatureFlagOverride.objects.create(
            tenant_id=ctx.tenant_id, company_id=company, code="pos_offline_fiscal_selling", enabled=True
        )
    with pytest.raises(FeatureDisabled, match="not available"), uow.unit_of_work(ctx):
        sequences.allocate_terminal_block(company, "POS", 10)
