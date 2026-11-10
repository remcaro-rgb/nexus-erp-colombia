"""Failure injection for the financial / stock consistency suites (T-P22-FIN-01, T-P22-STK-01).

``fail_after(n)`` installs a Django ``execute_wrapper`` that raises :class:`InjectedFailure` right
*before* the n-th SQL statement (1-based) executed on the connection. ``AtomicityProbe`` snapshots
row counts (and an order-independent content digest) of the tables a command may touch, so a test
asserts **all or nothing**: after an injected failure every table is byte-identical to the snapshot;
after success the expected tables all changed (document + entry + sub-ledger + outbox).

``sweep(command, ...)`` runs the command once uninjected to count its statements S, then re-runs it
with a failure before each statement k = 1..S (or a random sample), restoring the probe each time.
"""

from __future__ import annotations

import contextlib
import hashlib
import random
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

# Statements that are transaction control, not work: never count them as injection points.
_CONTROL_PREFIXES = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT", "BEGIN", "COMMIT", "ROLLBACK")


class InjectedFailure(RuntimeError):
    """Raised by the injector; never caught by application code (it is not a DatabaseError)."""


@dataclass
class StatementCounter:
    fail_at: int | None = None
    count: int = 0
    statements: list[str] = field(default_factory=list)
    fired: bool = False

    def __call__(self, execute: Callable[..., Any], sql: str, params: Any, many: bool, context: Any) -> Any:
        head = sql.lstrip().upper()
        if not head.startswith(_CONTROL_PREFIXES):
            self.count += 1
            self.statements.append(sql.split()[0].upper() if sql.split() else "")
            if self.fail_at is not None and self.count == self.fail_at:
                self.fired = True
                raise InjectedFailure(f"injected failure before SQL statement #{self.count}: {sql[:120]}")
        return execute(sql, params, many, context)


@contextlib.contextmanager
def fail_after(n: int | None, using: str = "default") -> Iterator[StatementCounter]:
    """Raise before the n-th non-control statement (``None`` = count only)."""
    from django.db import connections

    counter = StatementCounter(fail_at=n)
    with connections[using].execute_wrapper(counter):
        yield counter


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class TableState:
    rows: int
    digest: str


class AtomicityProbe:
    """Snapshot of tables; ``diff()`` lists tables whose content changed since ``snapshot()``."""

    def __init__(self, tables: Sequence[str], using: str = "default", ctx: Any = None) -> None:
        """``ctx`` (a ``kernel.tenancy.TenantContext``): read the tables bound to that tenant. Kernel and
        module tables have FORCED row-level security (ADR-003), so an unbound read sees no rows and would
        hide every partial write; scratch tables without RLS read the same either way."""
        self.tables = tuple(tables)
        self.using = using
        self.ctx = ctx
        self.baseline: dict[str, TableState] = {}

    @classmethod
    def for_all_tables(cls, using: str = "default", exclude_prefixes: Sequence[str] = ("django_",)) -> AtomicityProbe:
        from django.db import connections

        with connections[using].cursor() as cur:
            names = connections[using].introspection.table_names(cur)
        return cls([t for t in names if not t.startswith(tuple(exclude_prefixes))], using)

    def _state(self, table: str) -> TableState:
        from django.db import connections

        with connections[self.using].cursor() as cur:
            # Order-independent digest of every row's text form (synthetic test tables only).
            cur.execute(
                f"SELECT count(*), coalesce(md5(string_agg(md5(t::text), ',' ORDER BY md5(t::text))), '') "
                f"FROM {_quote(table)} AS t"
            )
            count, digest = cur.fetchone()
        return TableState(rows=int(count), digest=str(digest))

    def capture(self) -> dict[str, TableState]:
        if self.ctx is None:
            return {t: self._state(t) for t in self.tables}
        from django.db import transaction

        from kernel import tenancy

        with transaction.atomic(using=self.using), tenancy.activate(self.ctx):
            tenancy.bind(self.ctx, self.using)
            return {t: self._state(t) for t in self.tables}

    def snapshot(self) -> dict[str, TableState]:
        self.baseline = self.capture()
        return self.baseline

    def diff(self) -> dict[str, tuple[TableState, TableState]]:
        now = self.capture()
        return {t: (self.baseline[t], now[t]) for t in self.tables if self.baseline.get(t) != now[t]}

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for t, s in sorted(self.capture().items()):
            h.update(f"{t}:{s.rows}:{s.digest};".encode())
        return h.hexdigest()


@dataclass
class SweepResult:
    statements: int
    points: list[int]
    clean_changes: set[str]
    violations: list[str]

    @property
    def ok(self) -> bool:
        return not self.violations


def sweep(
    command: Callable[[], Any],
    probe: AtomicityProbe,
    *,
    expect_changed: Sequence[str] = (),
    reset: Callable[[], None] | None = None,
    sample: int | None = None,
    seed: int = 20261109,
    using: str = "default",
) -> SweepResult:
    """All-or-nothing check of ``command`` under failure injection at every statement.

    ``reset`` must restore the database to the baseline after a *successful* run (tests usually
    delete the rows the command created, or run inside an outer savepoint they roll back).
    """
    probe.snapshot()
    with fail_after(None, using) as counter:
        command()
    total = counter.count
    changed = set(probe.diff())
    violations: list[str] = []
    missing = [t for t in expect_changed if t not in changed]
    if missing:
        violations.append(f"successful run did not change expected tables {missing}")
    if reset is not None:
        reset()
    leftover = probe.diff()
    if leftover:
        violations.append(f"reset did not restore tables {sorted(leftover)}")
        return SweepResult(total, [], changed, violations)
    points = list(range(1, total + 1))
    if sample is not None and sample < total:
        points = sorted(random.Random(seed).sample(points, sample))
    for k in points:
        with fail_after(k, using) as inj:
            with contextlib.suppress(InjectedFailure):
                command()
        if not inj.fired:
            violations.append(f"injection #{k} never fired (statement count not deterministic)")
            if reset is not None:
                reset()
            continue
        partial = probe.diff()
        if partial:
            violations.append(f"failure before statement #{k} left partial writes in {sorted(partial)}")
            if reset is not None:
                reset()
    return SweepResult(total, points, changed, violations)
