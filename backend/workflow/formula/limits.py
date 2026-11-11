"""Sandbox limits (D-008). Callers may tighten them, never loosen them."""

from __future__ import annotations

import contextvars
import gc
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FunctionType

from workflow.formula.errors import FormulaBudgetExceeded, FormulaError

MAX_EXPRESSION_CHARS = 4000
MAX_AST_NODES = 500
MAX_NESTING = 32
MAX_GRAPH_FORMULAS = 200
MAX_NODE_VISITS = 10_000
MAX_CPU_MS = 50
DECIMAL_PRECISION = 28
#: Largest absolute decimal exponent of any value (|x| < 10**MAX_MAGNITUDE).
MAX_MAGNITUDE = 30
MAX_LITERAL_DIGITS = 40
MAX_STRING_CHARS = 1000
MAX_ROUND_PLACES = 12
MAX_TRACE_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class Limits:
    max_expression_chars: int = MAX_EXPRESSION_CHARS
    max_ast_nodes: int = MAX_AST_NODES
    max_nesting: int = MAX_NESTING
    max_graph_formulas: int = MAX_GRAPH_FORMULAS
    max_node_visits: int = MAX_NODE_VISITS
    max_cpu_ms: float = MAX_CPU_MS

    def __post_init__(self) -> None:
        defaults = DEFAULT_LIMITS_TUPLE
        mine = (
            self.max_expression_chars,
            self.max_ast_nodes,
            self.max_nesting,
            self.max_graph_formulas,
            self.max_node_visits,
            self.max_cpu_ms,
        )
        if any(m > d for m, d in zip(mine, defaults, strict=True)) or any(m <= 0 for m in mine):
            raise FormulaError("formula limits can only be tightened (D-008)")


DEFAULT_LIMITS_TUPLE = (
    MAX_EXPRESSION_CHARS,
    MAX_AST_NODES,
    MAX_NESTING,
    MAX_GRAPH_FORMULAS,
    MAX_NODE_VISITS,
    MAX_CPU_MS,
)
DEFAULT_LIMITS = Limits()


class GcPauseAccount:
    """Thread CPU spent in cyclic-GC pauses during one formula evaluation.

    :meth:`Budget.start` installs a fresh account in the current ``contextvars`` context, so
    only collections that run in the evaluating thread, during that evaluation, are recorded.
    Every thread runs in its own context, and formula code can neither start threads nor hand
    its context to one (T-FORMULA-ENGINE-02 static scan), so another request's collections
    never reach this account.
    """

    __slots__ = ("depth", "spent", "started")

    def __init__(self) -> None:
        self.depth = 0
        self.started = 0.0
        self.spent = 0.0

    def seconds(self) -> float:
        if self.depth > 0:  # read from inside a collection: count the running pause too
            return self.spent + max(0.0, _raw_clock() - self.started)
        return self.spent


# Raw thread CPU clock read by the GC hook and by the default budget clock. It is the test seam:
# tests replace it with a fake clock to drive the hook deterministically. Production always reads
# ``time.thread_time`` (CPU of the calling thread only).
_raw_clock: Callable[[], float] = time.thread_time
_current_gc_account: contextvars.ContextVar[GcPauseAccount | None] = contextvars.ContextVar(
    "formula_gc_pause_account", default=None
)


def _gc_pause_hook(phase: str, info: dict[str, int]) -> None:
    # CPython runs the cyclic collector in the thread that triggered it, and the collection's
    # CPU lands on that thread's clock. Record it so the formula is charged only for its own work.
    acc = _current_gc_account.get()
    if acc is None:  # no formula evaluation in this context
        return
    if phase == "start":
        if acc.depth == 0:
            acc.started = _raw_clock()
        acc.depth += 1
    elif phase == "stop" and acc.depth > 0:
        acc.depth -= 1
        if acc.depth == 0:
            acc.spent += max(0.0, _raw_clock() - acc.started)


def _register_gc_hook() -> None:
    """Install the GC-pause hook exactly once, also across ``importlib.reload`` of this module."""
    for cb in list(gc.callbacks):
        if isinstance(cb, FunctionType) and cb.__qualname__ == "_gc_pause_hook" and cb.__module__ == __name__:
            if cb is _gc_pause_hook:
                return
            gc.callbacks.remove(cb)  # the hook of a previous load of this module
    gc.callbacks.append(_gc_pause_hook)


_register_gc_hook()


def gc_seconds_in_current_evaluation() -> float:
    """Thread CPU (seconds) of the cyclic-GC pauses recorded for the current evaluation."""
    acc = _current_gc_account.get()
    return 0.0 if acc is None else acc.seconds()


def _cpu_clock() -> float:
    """Default budget clock: the formula's own CPU (seconds).

    That is the evaluating thread's CPU time minus the cyclic-GC pauses that ran in that same
    thread during the evaluation. D-008 charges "50 ms CPU per formula", not collection of the
    whole interpreter heap. Other requests' threads are never charged, and their GC pauses are
    never subtracted.
    """
    return _raw_clock() - gc_seconds_in_current_evaluation()


@dataclass(slots=True)
class Budget:
    """Per-formula evaluation budget: node visits and the formula's own thread CPU time."""

    limits: Limits = DEFAULT_LIMITS
    clock: Callable[[], float] = field(default=_cpu_clock)
    visits: int = 0
    _start: float = 0.0
    _deadline: float = 0.0
    _gc: GcPauseAccount | None = None

    def start(self) -> Budget:
        self.visits = 0
        # A fresh GC-pause account for this evaluation, in this thread's context only.
        self._gc = GcPauseAccount()
        _current_gc_account.set(self._gc)
        self._start = self.clock()
        self._deadline = self._start + self.limits.max_cpu_ms / 1000.0
        return self

    def tick(self) -> None:
        self.visits += 1
        if self.visits > self.limits.max_node_visits:
            raise FormulaBudgetExceeded(f"more than {self.limits.max_node_visits} node visits", budget="node_visits")
        # Checking the clock on every visit is cheap (thread_time is a syscall-free vDSO read
        # on Linux) and bounds the overshoot to one node.
        if self.clock() > self._deadline:
            raise FormulaBudgetExceeded(f"more than {self.limits.max_cpu_ms} ms of CPU", budget="cpu_ms")

    @property
    def elapsed_ms(self) -> float:
        return (self.clock() - self._start) * 1000.0

    @property
    def gc_ms(self) -> float:
        """Thread CPU (ms) of the cyclic-GC pauses in this thread since :meth:`start`.

        Not charged to the budget by the default clock; recorded for support traces.
        """
        return 0.0 if self._gc is None else self._gc.seconds() * 1000.0
