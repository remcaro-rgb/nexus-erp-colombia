# FIX-P07-CPU-W05 — formula CPU budget charged for cyclic-GC pauses

Scope: `backend/workflow/formula/limits.py`, `backend/workflow/tests/**`. No other module, no
dependency change, D-008 unchanged.

## Root cause

### CB-1 (production): GC pauses charged to the formula

Evidence: `docs/run/evidence/W05/parity-gate/P09b-3.log` —
`test_T_FORMULA_ENGINE_04_long_chain_with_back_edge` raised
`FormulaBudgetExceeded: more than 50 ms of CPU` at `visits=1`, i.e. on the first node of
`f{i-1} + 1`. No formula work explains 50 ms before the first node. The budget clock was the
raw `time.thread_time()`. CPython runs the cyclic collector in the thread that triggers it, here
through the allocations in `Evaluator.run` (`localcontext(decimal_context())`). A full gen-2
collection of a large heap (end of a 1,386-test run, a long-lived API worker) costs hundreds of
ms of that thread's CPU, and the formula was charged for it.

Own reproduction on unchanged `main` (clean worktree of `origin/main` 9c45f8f) with the
fault-injection plugin from the prompt (1.5 M-object cyclic heap, one `gc.collect()` after every
25th `Budget.start()`; one collection measured here at about 0.18 s of thread CPU):

* full command: it **hangs** in `test_T_FORMULA_ENGINE_03_no_cpu_starvation_of_other_requests`.
  The main-thread evaluation raises `FormulaBudgetExceeded` and the exception leaves the loop
  before `stop.set()`. The `ThreadPoolExecutor` then waits for the attacker threads forever.
* the same command with only that test deselected (diagnosis only):
  `16 failed, 499 passed, 1 deselected in 12.31s`. All 16 failures are
  `E workflow.formula.errors.FormulaBudgetExceeded: more than 50 ms of CPU` (graph, golden,
  adversarial and other tests). This confirms CB-1.

### CB-2 (tests): timing dependence where none is needed

* The long-chain topology test evaluated 199 formulas against the real CPU clock: 199 chances
  to absorb an unrelated pause (GC, VM CPU steal).
* `worst_case_admitted_formula_is_cheap` compared one raw `time.thread_time()` sample with 50 ms.
* `graph_size_budget` also evaluated 200 formulas against the real clock, although it only
  tests the graph size.

## Clock design (CB-1)

* `_cpu_clock()` (still the default `Budget.clock`) returns
  `_raw_clock() - gc_seconds_in_current_evaluation()`. `_raw_clock` is `time.thread_time`.
* `Budget.start()` creates a fresh `GcPauseAccount` and stores it in a module
  `contextvars.ContextVar`. The single module-level `gc.callbacks` hook `_gc_pause_hook` reads
  the current account. On `"start"` it records `_raw_clock()`, and on `"stop"` it adds the
  delta. A depth counter guards re-entry, and deltas are clamped at ≥ 0. With no account in
  the context (no evaluation), the hook returns at once.
* **Why a ContextVar and not `threading.local`:** the T-FORMULA-ENGINE-02 static scan
  (`test_formula_adversarial.py::test_T_FORMULA_ENGINE_02_static_scan_engine_has_no_io`) bans
  importing `threading` (and calling `getattr`) in `formula/*.py`. That check was kept, not
  weakened. Every thread runs in its own context. Because the account is fresh per evaluation,
  a context copied to another thread *before* the evaluation (asyncio.to_thread,
  sync_to_async) only carries a stale account of a finished evaluation and can never credit
  the running one. Only code running *inside* the evaluation could hand its context to
  another thread, and formula code cannot do that (no threads, no IO, which the same scan
  enforces). Budgets interleaved in one thread are charged conservatively: the older budget
  sees only the GC recorded since the newer one started, so it is over-charged at worst,
  never under-charged.
* Registration is idempotent: `_register_gc_hook()` keeps the current function, removes a hook
  left by a previous load of this module (same `__module__`/`__qualname__`), and appends
  once. Re-importing or reloading keeps exactly one hook.
* Test seam: tests monkeypatch `limits._raw_clock` with a fake per-thread clock. The hook and
  the default clock both read it, so a GC pause can be simulated exactly between the hook's
  start and stop.
* Additive: `Budget.gc_ms` (the GC ms excluded in this evaluation, for support traces). The
  public API is unchanged: `Budget(limits, clock=…)`, `start()`, `tick()`, `elapsed_ms`,
  `EvaluationResult` and the API/OpenAPI shape. `engine.py`, payroll and sales are untouched.
* No `gc.disable()`, `gc.freeze()` or threshold changes in production code. `gc.disable()`
  appears only in the test fixture, so that no automatic collection interleaves with the fake
  clocks.

## Why D-008 is kept, not loosened

* `MAX_CPU_MS = 50`, `MAX_NODE_VISITS = 10_000` and the tighten-only `Limits` check are
  unchanged. The budget is still 50 ms of the *formula's own* CPU.
* Formulas cannot create GC-tracked cycles. Values are `Decimal`, `str`, `bool` and `date`,
  which the cyclic collector does not track. So a hostile formula cannot move its own work into
  GC time. Only collector work is excluded, and the work a formula does before or after a pause
  is still charged (`..._formula_work_still_charged` checks 30 ms + 200 ms GC + 30 ms → exceeded).
* The node-visit budget and every static limit (chars, AST nodes, nesting, graph size,
  magnitude) bound the work independently of any clock.
* Other threads' CPU was never charged and still isn't. Their GC pauses are never subtracted
  (`..._gc_in_other_thread_not_subtracted`).

## Tests changed (CB-2) and new tests

* `test_T_FORMULA_ENGINE_04_long_chain_with_back_edge`: same chain, `D(199)` and 200-node
  cycle. Now `evaluate({}, clock=lambda: 0.0)` with the comment "topology test; CPU budget is
  T-FORMULA-ENGINE-03".
* `test_T_FORMULA_ENGINE_03_graph_size_budget`: the 200-formula evaluation uses
  `clock=lambda: 0.0` (graph-size test). The `graph` budget assertion is unchanged.
* `test_T_FORMULA_ENGINE_03_worst_case_admitted_formula_is_cheap`: measured with the engine's
  own `Budget()` (default clock, GC excluded) through `Evaluator.run`, minimum of 5 runs < 50 ms.
  It also checks that the graph really visits > 400 nodes.
* `test_T_FORMULA_ENGINE_03_no_cpu_starvation_of_other_requests`: thresholds unchanged. The
  main-thread loop is now wrapped in `try/finally: stop.set()`, so a failure is reported instead
  of hanging the executor (the hang seen on `main` under fault injection).
* Other loops (golden rounding modes, at most 8 evaluations per test) stay below the ~20
  threshold and were left alone.
* New, deterministic (fake raw clocks, no sleeps), in
  `backend/workflow/tests/test_formula_budget_fix_p07_cpu.py`:
  `test_T_FORMULA_ENGINE_03_FIX_P07_CPU_gc_pause_not_charged`,
  `..._formula_work_still_charged`, `..._gc_in_other_thread_not_subtracted`,
  `..._hook_registered_once` and `..._real_clock_still_enforced` (the real default clock with
  `Limits(max_cpu_ms=0.001)`). The existing `cpu_timeout_with_real_thread_clock` test stays.
* The test-function count in `backend/workflow/tests/` went up by 5. Nothing was skipped,
  xfailed or removed, and no ignores were added.

## Verification (this session)

See `docs/run/reports/FIX-P07-CPU-W05.json` for the exact lines. Summary:

* fault injection before: hang (full command) / 16 failed (starvation test deselected), all
  `more than 50 ms of CPU`. After: 3/3 `516 passed`.
* mypy `Success: no issues found in 611 source files`; `backend/workflow` 600 passed; consumers
  (payroll/sales `-k "formula or pricing or concept"`) 310 passed; `FIX_P07_CPU` tests 5 passed.
* `STRESS-LOOP 30/30 passed`; `CPU-STRESS 10/10 passed` (one busy loop per core).
* full backend suite twice: `1474 passed in 173.67s`, `1474 passed in 182.29s`; `make ci exit=0`.
* parity gate: `PARITY-GATE OK 4/4 (HEAD 07910d9) tree=4646fb59026a63b34930`. The first attempt
  was red only in policy/gitleaks because the Docker daemon was not running yet when that step
  ran. The gate starts dockerd later, in the pytest step. The clone was also shallow
  (`git fetch --unshallow origin`). With both fixed it re-ran green.

## GitHub Actions

Observed through the GitHub MCP: run 43043731722 (`ci`, push) on 07910d9, which has the same code
tree as the tested commit. All 4 jobs passed (`success`): policy, P03 tests, make ci, and the
T-P03-01 compose startup.

## Residual risk / follow-up

* Hypervisor CPU steal can still inflate `thread_time` on some hosts. The node-visit cap keeps
  the cost bounded, and a legitimate formula's own cost is about 1 ms (worst admitted one well
  under 50 ms). That leaves a wide margin but not an absolute guarantee.
* Recommended (not implemented): a CCR to P02/P23 to expose `Budget.gc_ms` (and `elapsed_ms`)
  in the explain/support trace, so a `formula_budget_exceeded` in production can be told apart
  from GC or host noise.
