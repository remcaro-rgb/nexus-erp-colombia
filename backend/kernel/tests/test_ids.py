import threading
import uuid

import pytest

from kernel import ids


def test_T_P01_03_uuid7_monotonic_and_sortable():
    generated = [ids.uuid7() for _ in range(20000)]
    assert generated == sorted(generated)
    assert [g.bytes for g in generated] == sorted(g.bytes for g in generated)
    assert len(set(generated)) == len(generated)
    assert all(g.version == 7 and g.variant == uuid.RFC_4122 for g in generated)


def test_T_P01_03_uuid7_monotonic_across_threads():
    out: list[uuid.UUID] = []
    lock = threading.Lock()

    def work():
        local = [ids.new_id() for _ in range(2000)]
        assert local == sorted(local)
        with lock:
            out.extend(local)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 8000


def test_T_P01_03_counter_overflow_advances_timestamp(monkeypatch):
    monkeypatch.setattr(ids, "_now_ms", lambda: 1_700_000_000_000)
    monkeypatch.setattr(ids, "_last_ms", 0)
    first = ids.uuid7()
    seq = [ids.uuid7() for _ in range(5000)]  # > 4096 per ms forces overflow
    assert seq == sorted(seq) and first < seq[0]
    assert ids.timestamp_ms(seq[-1]) > ids.timestamp_ms(first)
    assert ids.timestamp_ms(first) == 1_700_000_000_000


def test_T_P01_03_timestamp_requires_v7():
    with pytest.raises(ValueError):
        ids.timestamp_ms(uuid.uuid4())
