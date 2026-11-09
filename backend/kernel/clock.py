"""Injectable clock. Storage in UTC; business dates in the company timezone."""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import Iterator
from typing import Protocol
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Bogota"
UTC = dt.UTC


class Clock(Protocol):
    def now(self) -> dt.datetime:
        """Current aware datetime in UTC."""
        ...


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(UTC)


class FrozenClock:
    """Test clock; ``tick`` advances it explicitly."""

    def __init__(self, at: dt.datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs an aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> dt.datetime:
        return self._at

    def tick(self, delta: dt.timedelta) -> None:
        self._at = self._at + delta


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


@contextlib.contextmanager
def frozen(at: dt.datetime) -> Iterator[FrozenClock]:
    previous = _clock
    fc = FrozenClock(at)
    set_clock(fc)
    try:
        yield fc
    finally:
        set_clock(previous)


def now() -> dt.datetime:
    return _clock.now()


def tz(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TZ)


def business_date(timezone: str | None = None, at: dt.datetime | None = None) -> dt.date:
    """Business date in the company timezone (default America/Bogota)."""
    moment = at or now()
    if moment.tzinfo is None:
        raise ValueError("aware datetime required")
    return moment.astimezone(tz(timezone)).date()


def local_midnight_utc(day: dt.date, timezone: str | None = None) -> dt.datetime:
    """UTC instant of 00:00 local time of ``day`` (start of a business day)."""
    return dt.datetime.combine(day, dt.time.min, tzinfo=tz(timezone)).astimezone(UTC)
