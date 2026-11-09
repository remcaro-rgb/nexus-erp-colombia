"""UUIDv7 identifiers (RFC 9562), monotonic within a process.

Layout: 48-bit unix ms | ver(7) | 12-bit counter | var(10) | 62 random bits.
Within the same millisecond the 12-bit counter increments (RFC 9562 §6.2
method 1); on counter overflow the timestamp is advanced by one millisecond so
generated ids stay strictly increasing.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0
_MAX_COUNTER = 0xFFF


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        ms = _now_ms()
        if ms > _last_ms:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # leave headroom
        else:
            _counter += 1
            if _counter > _MAX_COUNTER:
                _last_ms += 1
                _counter = 0
        ms, counter = _last_ms, _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def new_id() -> uuid.UUID:
    """Default primary-key generator for every model (C2-3)."""
    return uuid7()


def timestamp_ms(value: uuid.UUID) -> int:
    """Unix milliseconds embedded in a UUIDv7."""
    if value.version != 7:
        raise ValueError("not a UUIDv7")
    return value.int >> 80
