"""Sandbox limits (D-008). Callers may tighten them, never loosen them."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

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


def _cpu_clock() -> float:
    # CPU time of the evaluating thread: other requests' threads are not charged.
    return time.thread_time()


@dataclass(slots=True)
class Budget:
    """Per-formula evaluation budget: node visits and thread CPU time."""

    limits: Limits = DEFAULT_LIMITS
    clock: Callable[[], float] = field(default=_cpu_clock)
    visits: int = 0
    _start: float = 0.0
    _deadline: float = 0.0

    def start(self) -> Budget:
        self.visits = 0
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
