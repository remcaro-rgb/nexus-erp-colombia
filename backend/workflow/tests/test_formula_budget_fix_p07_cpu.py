"""T-FORMULA-ENGINE-03 (FIX-P07-CPU-W05) — the CPU budget charges the formula's own CPU.

The default budget clock is the evaluating thread's CPU time minus the cyclic-GC pauses that
ran in that same thread. These tests drive the real ``gc.callbacks`` hook with fake raw thread
clocks: no sleeps and no dependence on how fast this machine is.
"""

from __future__ import annotations

import contextvars
import gc
import importlib
import threading
import time
from collections.abc import Callable, Iterator
from decimal import Decimal as D
from typing import Any

import pytest

from workflow.formula import Limits, evaluate_expression
from workflow.formula import limits as limits_mod
from workflow.formula.errors import FormulaBudgetExceeded
from workflow.formula.limits import MAX_CPU_MS, Budget, gc_seconds_in_current_evaluation

GC_PAUSE_S = 0.200  # a full collection of a big heap: 4x the whole formula budget


class FakeThreadClocks:
    """One fake CPU clock per thread (like ``time.thread_time``); each read costs ``step``.

    ``arm_gc_pause()`` makes the next cyclic-GC collection in the arming thread consume
    ``GC_PAUSE_S`` of that thread's fake CPU, between the hook's ``start`` and ``stop``.
    """

    def __init__(self, step: float = 1e-6) -> None:
        self.step = step
        self.now: dict[int, float] = {}
        self.pause_armed: set[int] = set()
        self.collect_on_read: dict[int, int] = {}
        self.reads: dict[int, int] = {}

    def __call__(self) -> float:
        me = threading.get_ident()
        self.reads[me] = self.reads.get(me, 0) + 1
        if self.collect_on_read.get(me) == self.reads[me]:
            del self.collect_on_read[me]
            self.arm_gc_pause()
            gc.collect()  # a collection triggered from inside the evaluation
        t = self.now.get(me, 1000.0) + self.step
        self.now[me] = t
        return t

    def advance(self, seconds: float) -> None:
        me = threading.get_ident()
        self.now[me] = self.now.get(me, 1000.0) + seconds

    def arm_gc_pause(self) -> None:
        self.pause_armed.add(threading.get_ident())

    def gc_callback(self, phase: str, info: dict[str, int]) -> None:
        # Runs after the budget's hook (registered at import), so the pause lands between the
        # hook's "start" and "stop" reads, exactly like a real collection's CPU.
        me = threading.get_ident()
        if phase == "start" and me in self.pause_armed:
            self.pause_armed.discard(me)
            self.advance(GC_PAUSE_S)


@pytest.fixture
def fake_clocks(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeThreadClocks]:
    clocks = FakeThreadClocks()
    monkeypatch.setattr(limits_mod, "_raw_clock", clocks)
    was_enabled = gc.isenabled()
    gc.disable()  # test-only: no automatic collection may interleave with the fake clocks
    gc.callbacks.append(clocks.gc_callback)
    try:
        yield clocks
    finally:
        gc.callbacks.remove(clocks.gc_callback)
        if was_enabled:
            gc.enable()


def _hooks() -> list[Callable[..., Any]]:
    return [cb for cb in gc.callbacks if getattr(cb, "__qualname__", "") == "_gc_pause_hook"]


def test_T_FORMULA_ENGINE_03_FIX_P07_CPU_gc_pause_not_charged(fake_clocks: FakeThreadClocks) -> None:
    # A full collection consuming 200 ms of this thread's CPU starts on the 3rd clock read,
    # i.e. inside the evaluation (after Budget.start()). The formula is not charged for it.
    fake_clocks.collect_on_read[threading.get_ident()] = 3
    assert evaluate_expression("1 + 2 + 3 + 4 + 5", {}) == D(15)
    assert fake_clocks.now[threading.get_ident()] - 1000.0 > GC_PAUSE_S  # the raw clock did jump

    # The same through the Budget object, which also records the excluded GC time.
    b = Budget().start()
    fake_clocks.arm_gc_pause()
    gc.collect()
    b.tick()
    assert b.visits == 1 and b.elapsed_ms < MAX_CPU_MS
    assert b.gc_ms == pytest.approx(GC_PAUSE_S * 1000, abs=0.01)


def test_T_FORMULA_ENGINE_03_FIX_P07_CPU_formula_work_still_charged(fake_clocks: FakeThreadClocks) -> None:
    # No GC at all; every node visit costs 10 ms of fake CPU: 15 visits > 50 ms.
    fake_clocks.step = 0.010
    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression("1 + 2 + 3 + 4 + 5 + 6 + 7 + 8", {})
    assert exc.value.code == "formula_budget_exceeded"
    assert exc.value.extra["budget"] == "cpu_ms"

    # A GC pause does not cancel CPU the formula itself spent before or after it.
    fake_clocks.step = 1e-6
    b = Budget().start()
    fake_clocks.advance(0.030)
    fake_clocks.arm_gc_pause()
    gc.collect()
    fake_clocks.advance(0.030)  # 60 ms of the formula's own CPU around a 200 ms pause
    with pytest.raises(FormulaBudgetExceeded) as exc:
        b.tick()
    assert exc.value.extra["budget"] == "cpu_ms"


def test_T_FORMULA_ENGINE_03_FIX_P07_CPU_gc_in_other_thread_not_subtracted(fake_clocks: FakeThreadClocks) -> None:
    # A context copied before the evaluation (what asyncio.to_thread / sync_to_async hand to a
    # worker thread) and a plain new thread: neither can credit its GC pauses to this budget.
    copied = contextvars.copy_context()
    b = Budget().start()
    other: dict[str, float] = {}

    def other_request(key: str) -> None:
        fake_clocks.arm_gc_pause()
        gc.collect()  # a 200 ms pause charged to the other thread's clock
        other[key] = gc_seconds_in_current_evaluation()  # never this test's account
        other_budget = Budget().start()  # that thread's own evaluation gets its own account
        fake_clocks.arm_gc_pause()
        gc.collect()
        other[key + "_own"] = other_budget.gc_ms

    threads = [
        threading.Thread(target=other_request, args=("plain",)),
        threading.Thread(target=copied.run, args=(other_request, "copied")),
    ]
    for t in threads:
        t.start()
        t.join()
    assert other["plain"] == 0.0  # a new thread has no evaluation account at all
    assert other["plain_own"] == pytest.approx(GC_PAUSE_S * 1000, abs=0.1)
    assert other["copied_own"] == pytest.approx(GC_PAUSE_S * 1000, abs=0.1)
    assert b.gc_ms == 0.0  # nothing credited to this evaluation
    fake_clocks.advance(0.060)  # this thread's own work: 60 ms > 50 ms
    with pytest.raises(FormulaBudgetExceeded) as exc:
        b.tick()
    assert exc.value.extra["budget"] == "cpu_ms"


def test_T_FORMULA_ENGINE_03_FIX_P07_CPU_hook_registered_once() -> None:
    assert len(_hooks()) == 1
    saved = dict(limits_mod.__dict__)
    try:
        importlib.reload(limits_mod)
        importlib.reload(limits_mod)
        assert len(_hooks()) == 1
        assert _hooks()[0] is limits_mod._gc_pause_hook
        importlib.import_module("workflow.formula.limits")  # a plain re-import adds nothing
        assert len(_hooks()) == 1
    finally:
        # Put back the original objects (engine.py holds them) and their hook.
        limits_mod.__dict__.update(saved)
        limits_mod._register_gc_hook()
    assert len(_hooks()) == 1 and _hooks()[0] is limits_mod._gc_pause_hook


def test_T_FORMULA_ENGINE_03_FIX_P07_CPU_real_clock_still_enforced() -> None:
    # The default clock is the real thread CPU clock (GC pauses excluded) and still enforces it.
    assert limits_mod._raw_clock is time.thread_time
    assert Budget().clock is limits_mod._cpu_clock
    expr = " + ".join(["round(x * 1.0001 + y / 3, 6)"] * 50)
    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression(expr, {"x": D(1), "y": D(2)}, limits=Limits(max_cpu_ms=0.001))
    assert exc.value.extra["budget"] == "cpu_ms"
    assert evaluate_expression("round(a * 1.19, 2)", {"a": D("100")}) == D("119.00")
