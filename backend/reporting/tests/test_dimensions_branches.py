"""Semantic reports over the real ledger: hierarchy dimensions (F-ACC-DIM) and branches (F-ACC-BRANCH).

T-ACC-DIM-10 group + child filter selected together does not double count; current vs *as of* hierarchy give the
same totals with different grouping · T-ACC-BRANCH-05 a report filtered by branch equals the sum of its warehouses'
and POS documents (and a branch-scoped principal sees only its branch).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from kernel import clock
from kernel.interfaces.common import DimensionValues
from reporting import engine
from reporting.testing.support import World

D = Decimal
pytestmark = pytest.mark.django_db
OCT = dt.date(2026, 10, 15)


def q(world: World, who: str, query: dict[str, Any]) -> dict[str, Any]:
    return engine.run(world.ctx(who), [world.company], query, engine.QueryOptions(use_cache=False))


def total(result: dict[str, Any], measure: str = "net_credit") -> Decimal:
    return D(result["totals"][measure])


def by(result: dict[str, Any], key: str, measure: str = "net_credit") -> dict[Any, Decimal]:
    return {r.get(f"{key}__label", r[key]) if r[key] else None: D(r[measure]) for r in result["rows"]}


def cc_tree(world: World) -> dict[str, Any]:
    """CC tree created on 2026-10-01: ADMIN{A1, A2}, VENTAS{V1}; posting lines in October on A1, A2, V1."""
    with clock.frozen(dt.datetime(2026, 10, 1, 15, 0, tzinfo=dt.UTC)):
        admin = world.node("cost_center", "ADMIN", "AGRUPADOR")
        ventas = world.node("cost_center", "VENTAS", "AGRUPADOR")
        a1 = world.node("cost_center", "A1", "DETALLE", admin)
        a2 = world.node("cost_center", "A2", "DETALLE", admin)
        v1 = world.node("cost_center", "V1", "DETALLE", ventas)
    for n, (leaf, amount) in enumerate(((a1, "100000"), (a2, "250000"), (v1, "400000"), (a1, "50000"))):
        world.post("test.doc", f"DOC-{n}", amount, day=OCT, dims=DimensionValues(cost_center_id=leaf.id))
    return {"ADMIN": admin, "VENTAS": ventas, "A1": a1, "A2": a2, "V1": v1}


def test_T_ACC_DIM_10_group_and_child_together_no_double_count_and_as_of_vs_current_same_totals(world: World) -> None:
    t = cc_tree(world)
    base = {"entity": "ledger.journal_line", "measures": ["net_credit", "lines"],
            "filters": [{"field": "account_code", "op": "eq", "value": "413505"}]}  # fmt: skip

    # group + child selected together: each leaf once (A1 150,000 + A2 250,000 = 400,000; never 550,000)
    both = q(world, "analyst", {**base, "filters": [*base["filters"],
             {"field": "cost_center", "op": "in_subtree", "value": [str(t["ADMIN"].id), str(t["A1"].id)]}]})
    assert total(both) == D("400000")
    assert both["totals"]["lines"] == 3
    only_group = q(world, "analyst", {**base, "filters": [*base["filters"],
                   {"field": "cost_center", "op": "in_subtree", "value": [str(t["ADMIN"].id)]}]})
    assert total(only_group) == total(both)
    child_twice = q(world, "analyst", {**base, "filters": [*base["filters"],
                    {"field": "cost_center", "op": "in_subtree", "value": [str(t["A1"].id), str(t["A1"].id)]}]})
    assert total(child_twice) == D("150000")
    everything = q(world, "analyst", {**base, "filters": [*base["filters"],
                   {"field": "cost_center", "op": "in_subtree",
                    "value": [str(t["ADMIN"].id), str(t["VENTAS"].id), str(t["A2"].id), str(t["V1"].id)]}]})
    assert total(everything) == D("800000") == total(q(world, "analyst", base))

    # re-parent A2 from ADMIN to VENTAS on 2026-11-09 (posted lines untouched, DIM-R06)
    world.move(t["A2"], t["VENTAS"])
    grouped = {**base, "dimensions": ["cost_center:root"]}
    current = q(world, "analyst", grouped)
    as_of = q(world, "analyst", {**grouped, "as_of": "2026-10-31"})
    assert by(current, "cost_center:root") == {"ADMIN — ADMIN (SINTÉTICO)": D("150000"),
                                               "VENTAS — VENTAS (SINTÉTICO)": D("650000")}
    assert by(as_of, "cost_center:root") == {"ADMIN — ADMIN (SINTÉTICO)": D("400000"),
                                             "VENTAS — VENTAS (SINTÉTICO)": D("400000")}
    assert total(current) == total(as_of) == D("800000")  # totals never change, only the grouping
    # the subtree filter follows the chosen hierarchy too, still without double counting
    sub_now = q(world, "analyst", {**base, "filters": [*base["filters"],
                {"field": "cost_center", "op": "in_subtree", "value": [str(t["VENTAS"].id), str(t["A2"].id)]}]})
    sub_then = q(world, "analyst", {**base, "as_of": "2026-10-31", "filters": [*base["filters"],
                 {"field": "cost_center", "op": "in_subtree", "value": [str(t["VENTAS"].id), str(t["A2"].id)]}]})
    assert total(sub_now) == D("650000") and total(sub_then) == D("650000")  # A2 counted once in both
    # leaf grouping is unaffected by the move
    leaves = q(world, "analyst", {**base, "dimensions": ["cost_center"]})
    assert sorted(by(leaves, "cost_center").values()) == [D("150000"), D("250000"), D("400000")]


def test_T_ACC_BRANCH_05_branch_report_equals_sum_of_its_warehouses_and_pos_documents(world: World) -> None:
    norte, sur = world.branch("NORTE", head=True), world.branch("SUR")
    w_n1, w_n2, w_s1 = world.warehouse("BN1", norte), world.warehouse("BN2", norte), world.warehouse("BS1", sur)
    pos_n = world.pos_config("Caja Norte", norte, w_n1)
    pos_s = world.pos_config("Caja Sur", sur, w_s1)
    docs: list[tuple[Any, str, str]] = []  # (branch, source type, amount)
    for n, (wh, amount) in enumerate(((w_n1, "120000"), (w_n2, "80000"), (w_s1, "300000"), (w_n2, "15500.50"))):
        br = norte if wh.branch_id == norte.id else sur
        world.post("inventory.stock_issue", f"SAL-{n}", amount, debit_role="COGS", credit_role="INVENTORY",
                   branch=br, warehouse=wh)  # fmt: skip
        docs.append((br, "inventory.stock_issue", amount))
    for n, (cfg, amount) in enumerate(((pos_n, "45000"), (pos_s, "60000"), (pos_n, "9999.99"))):
        br = norte if cfg.branch_id == norte.id else sur
        wh = w_n1 if cfg.id == pos_n.id else w_s1
        world.post("pos.ticket", f"POS-{n}", amount, branch=br, warehouse=wh)
        docs.append((br, "pos.ticket", amount))

    def report(branch: Any, extra: list[dict[str, Any]] | None = None, dims: list[str] | None = None,
               who: str = "analyst") -> dict[str, Any]:
        filters = [{"field": "branch_id", "op": "eq", "value": str(branch.id)}] if branch is not None else []
        return q(world, who, {"entity": "ledger.journal_line", "measures": ["debit", "credit", "entries"],
                              "filters": filters + (extra or []), "dimensions": dims or []})  # fmt: skip

    for br in (norte, sur):
        expected = sum((D(a) for b, _, a in docs if b.id == br.id), D(0))
        expected_docs = sum(1 for b, _, _ in docs if b.id == br.id)
        result = report(br)
        # each document is one balanced entry: Σ debit = Σ credit = Σ document amounts of the branch
        assert D(result["totals"]["debit"]) == expected == D(result["totals"]["credit"])
        assert result["totals"]["entries"] == expected_docs
        # = the sum over the branch's warehouses (joined through inventory_warehouse.branch_id)
        per_wh = q(world, "analyst", {"entity": "ledger.journal_line", "measures": ["debit"],
                                      "dimensions": ["warehouse_code", "warehouse_branch_id"]})  # fmt: skip
        wh_sum = sum((D(r["debit"]) for r in per_wh["rows"] if r["warehouse_branch_id"] == str(br.id)), D(0))
        assert wh_sum == expected
        # = stock documents of its warehouses + POS documents of its POS configurations
        by_source = report(br, dims=["source_type"])
        split = {r["source_type"]: D(r["debit"]) for r in by_source["rows"]}
        assert split["inventory.stock_issue"] == sum((D(a) for b, s, a in docs if b.id == br.id and s != "pos.ticket"), D(0))
        assert split["pos.ticket"] == sum((D(a) for b, s, a in docs if b.id == br.id and s == "pos.ticket"), D(0))
    assert D(report(norte)["totals"]["debit"]) + D(report(sur)["totals"]["debit"]) == D(report(None)["totals"]["debit"])

    # row policy by branch: a principal scoped to SUR never sees NORTE, even when it filters for it
    clerk = world.user("clerk_sur", ("reporting.*", "ledger.entry.read"))
    world.policy.branches[clerk.principal_id] = {sur.id}
    assert D(report(None, who="clerk_sur")["totals"]["debit"]) == D(report(sur)["totals"]["debit"])
    hidden = report(norte, who="clerk_sur")
    assert D(hidden["totals"]["debit"]) == D(0) and hidden["totals"]["entries"] == 0
