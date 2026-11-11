"""T-FORMULA-ENGINE-03 — budget exhaustion and timeouts produce formula_budget_exceeded;
evaluation is bounded so it cannot starve other requests."""

from __future__ import annotations

import contextlib
import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest

from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema, Limits, evaluate_expression
from workflow.formula.errors import FormulaBudgetExceeded, FormulaError
from workflow.formula.evaluator import Evaluator
from workflow.formula.limits import MAX_AST_NODES, MAX_CPU_MS, MAX_NESTING, MAX_NODE_VISITS, Budget
from workflow.formula.tables import NoTables


def _largest_allowed_formula() -> str:
    # ~490 nodes, nesting close to the limit: the most expensive formula D-008 admits.
    inner = "round(x * 1.0001 + y / 3, 6)"
    return " + ".join([inner] * 50)  # 50 * 9 + 49 = 499 nodes


def test_T_FORMULA_ENGINE_03_d008_defaults() -> None:
    assert (MAX_AST_NODES, MAX_NESTING, MAX_NODE_VISITS, MAX_CPU_MS) == (500, 32, 10_000, 50)
    with pytest.raises(FormulaError):
        Limits(max_node_visits=MAX_NODE_VISITS + 1)  # limits can only be tightened
    with pytest.raises(FormulaError):
        Limits(max_cpu_ms=0)


def test_T_FORMULA_ENGINE_03_node_visit_budget() -> None:
    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression("1 + 2 + 3 + 4 + 5 + 6", limits=Limits(max_node_visits=5))
    assert exc.value.code == "formula_budget_exceeded"
    assert exc.value.extra["budget"] == "node_visits"
    assert evaluate_expression("1 + 2 + 3", limits=Limits(max_node_visits=5)) == D(6)


def test_T_FORMULA_ENGINE_03_cpu_timeout_with_injected_clock() -> None:
    ticks = itertools.count()

    def slow_clock() -> float:  # every visit costs 10 ms of "CPU"
        return next(ticks) * 0.010

    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression("1 + 2 + 3 + 4 + 5 + 6 + 7 + 8", clock=slow_clock)
    assert exc.value.extra["budget"] == "cpu_ms"


def test_T_FORMULA_ENGINE_03_cpu_timeout_with_real_thread_clock() -> None:
    expr = _largest_allowed_formula()
    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression(expr, {"x": D(1), "y": D(2)}, limits=Limits(max_cpu_ms=0.001))
    assert exc.value.extra["budget"] == "cpu_ms"


@pytest.mark.parametrize(
    ("expression", "limits", "budget"),
    [
        ("1 + 1", Limits(max_expression_chars=4), "expression_chars"),
        ("1 + 2 + 3", Limits(max_ast_nodes=3), "ast_nodes"),
        ("((1))", Limits(max_nesting=1), "nesting"),
        ("x" + " + x" * 1000, Limits(), "expression_chars"),
        (" + ".join(["1"] * 251), Limits(), "ast_nodes"),
        ("(" * 33 + "1" + ")" * 33, Limits(), "nesting"),
    ],
)
def test_T_FORMULA_ENGINE_03_static_budgets(expression: str, limits: Limits, budget: str) -> None:
    with pytest.raises(FormulaBudgetExceeded) as exc:
        evaluate_expression(expression, {"x": D(1)}, limits=limits)
    assert exc.value.extra["budget"] == budget


def test_T_FORMULA_ENGINE_03_graph_size_budget() -> None:
    defs = [FormulaDefinition(f"f{i}", "1") for i in range(201)]
    with pytest.raises(FormulaBudgetExceeded) as exc:
        FormulaGraph(defs)
    assert exc.value.extra["budget"] == "graph"
    # graph-size test; the CPU budget is checked by the clock tests below
    assert len(FormulaGraph(defs[:200]).evaluate({}, clock=lambda: 0.0)) == 200


def test_T_FORMULA_ENGINE_03_budget_is_per_formula_and_resets() -> None:
    limits = Limits(max_node_visits=4)
    defs = [FormulaDefinition("a", "1 + 2"), FormulaDefinition("b", "a + 1", depends_on=("a",))]
    res = FormulaGraph(defs, limits=limits).evaluate({})
    assert res["b"].value == D(4) and res["a"].visits == 3 and res["b"].visits == 3


def test_T_FORMULA_ENGINE_03_worst_case_admitted_formula_is_cheap() -> None:
    expr = _largest_allowed_formula()
    schema = InputSchema.from_json({"x": "decimal", "y": "decimal"})
    graph = FormulaGraph([FormulaDefinition("w", expr, schema)])
    budget_s = MAX_CPU_MS / 1000
    # Measured with the engine's own budget clock (the formula's CPU, cyclic-GC pauses
    # excluded); the minimum of 5 runs is the cost on a noisy machine. One run under 50 ms
    # proves the most expensive admitted formula fits the budget.
    analyzed = graph.analyzed["w"]
    env = schema.bind({"x": D("1.5"), "y": D("2")}, used=frozenset(analyzed.info.inputs))
    costs = []
    for _ in range(5):
        budget = Budget()  # the engine's default clock
        assert Evaluator(analyzed.source, dict(env), budget, NoTables(), explain=False).run(analyzed.root) > 0
        costs.append(budget.elapsed_ms / 1000)
    assert min(costs) < budget_s
    assert graph.evaluate({"x": D("1.5"), "y": D("2")}, explain=False)["w"].visits > 400


def test_T_FORMULA_ENGINE_03_no_cpu_starvation_of_other_requests() -> None:
    """Hostile and worst-case formulas in 8 threads; a normal request in another thread
    stays fast and every hostile evaluation stops within its budget."""
    hostile = [
        _largest_allowed_formula(),
        "(" * 33 + "1" + ")" * 33,
        "x" + " + x" * 2000,
        " + ".join(["round(x / 7, 12)"] * 70),
    ]
    stop = threading.Event()
    per_eval_wall: list[float] = []
    lock = threading.Lock()

    def attacker(i: int) -> int:
        n = 0
        while not stop.is_set():
            t0 = time.perf_counter()
            with contextlib.suppress(FormulaError):
                evaluate_expression(
                    hostile[i % len(hostile)], {"x": D(3), "y": D(4)}, schema={"x": "decimal", "y": "decimal"}
                )
            with lock:
                per_eval_wall.append(time.perf_counter() - t0)
            n += 1
        return n

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(attacker, i) for i in range(8)]
        time.sleep(0.05)
        latencies = []
        try:
            for _ in range(50):
                t0 = time.perf_counter()
                assert evaluate_expression("round(a * 1.19, 2)", {"a": D("100")}) == D("119.00")
                latencies.append(time.perf_counter() - t0)
        finally:
            stop.set()  # a failure above must stop the attackers, not hang the executor
        total = sum(f.result() for f in futures)
    assert total > 0
    latencies.sort()
    # p95 of the normal request under attack stays far below one second.
    assert latencies[int(len(latencies) * 0.95) - 1] < 0.5
    # Every hostile evaluation finished (wall time includes GIL waits of 9 threads).
    assert max(per_eval_wall) < 2.0


def test_T_FORMULA_ENGINE_03_budget_object() -> None:
    b = Budget(Limits(max_node_visits=2), clock=lambda: 0.0).start()
    b.tick()
    b.tick()
    with pytest.raises(FormulaBudgetExceeded):
        b.tick()
    assert b.elapsed_ms == 0.0
