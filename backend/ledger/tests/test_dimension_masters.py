"""T-ACC-DIM-01 / T-ACC-DIM-03: dimension masters and hierarchy rules."""

from __future__ import annotations

import pytest

from kernel.errors import ValidationFailed
from ledger.dimensions import service as dims
from ledger.errors import LedgerError
from ledger.models import DIMENSIONS, DimensionClosure, DimensionNode

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_T_ACC_DIM_01_create_group_and_leaf_unique_code_per_company(world, dimension):
    group = world.node(dimension, "ADM", "AGRUPADOR")
    leaf = world.node(dimension, "ADM-01", "DETALLE", parent=group)
    assert (group.node_type, group.depth, group.path) == ("AGRUPADOR", 1, "ADM")
    assert (leaf.node_type, leaf.depth, leaf.path, leaf.parent_id) == ("DETALLE", 2, "ADM/ADM-01", group.id)
    assert leaf.status == "ACTIVE"

    # Unique per (company, dimension), case-insensitive and trimmed.
    for clash in ("adm", " Adm-01 ", "ADM-01"):
        with pytest.raises(LedgerError) as exc:
            world.node(dimension, clash, "DETALLE")
        assert exc.value.code == "dimension_code_duplicate"

    # The same code is allowed in another company of the same tenant …
    other = world.node(dimension, "ADM", "AGRUPADOR", company=world.other_company)
    assert other.company_id == world.other_company and other.id != group.id
    # … and in another dimension of the same company.
    another_dim = next(d for d in DIMENSIONS if d != dimension)
    assert world.node(another_dim, "adm", "AGRUPADOR").code == "adm"

    with world.scope():
        assert DimensionNode.objects.filter(company_id=world.company, dimension=dimension).count() == 2
        closure = set(
            DimensionClosure.objects.filter(company_id=world.company, descendant_id=leaf.id).values_list(
                "ancestor_id", "distance"
            )
        )
    assert closure == {(leaf.id, 0), (group.id, 1)}


def test_T_ACC_DIM_01_input_validation(world):
    with pytest.raises(ValidationFailed):
        world.node("cost_center", "", "DETALLE")
    with pytest.raises(ValidationFailed):
        world.node("cost_center", "X" * 41, "DETALLE")
    with pytest.raises(ValidationFailed):
        world.node("cost_center", "OK", "HOJA")
    with pytest.raises(ValidationFailed):
        world.node("cost_center", "OK2", "DETALLE", description="")


def _code(fn):
    with pytest.raises((LedgerError, ValidationFailed)) as exc:
        fn()
    return exc.value.code, getattr(exc.value, "extra", {})


def test_T_ACC_DIM_03_hierarchy_rules_on_create(world):
    root = world.node("aux_abierto", "PRY", "AGRUPADOR")
    leaf = world.node("aux_abierto", "PRY-1", "DETALLE", parent=root)

    # DETALLE as parent.
    code, extra = _code(lambda: world.node("aux_abierto", "PRY-1-A", "DETALLE", parent=leaf))
    assert code == "validation_failed" and extra["reason"] == "parent_not_agrupador"

    # Cross-dimension parent (same company, other dimension).
    other_dim_group = world.node("item_contable", "GRP", "AGRUPADOR")
    code, extra = _code(lambda: world.node("aux_abierto", "X1", "DETALLE", parent=other_dim_group))
    assert code == "validation_failed" and extra["reason"] == "parent_other_dimension"

    # Depth > limit (default 6, D-007).
    parent = root
    for level in range(2, 7):
        parent = world.node("aux_abierto", f"L{level}", "AGRUPADOR", parent=parent)
    assert parent.depth == 6
    code, _ = _code(lambda: world.node("aux_abierto", "L7", "DETALLE", parent=parent))
    assert code == "dimension_depth_exceeded"

    # A configurable lower limit applies as well.
    world.run(lambda u: dims.set_max_depth(u, world.company, "item_contable", 1))
    code, _ = _code(lambda: world.node("item_contable", "GRP-1", "DETALLE", parent=other_dim_group))
    assert code == "dimension_depth_exceeded"
    with pytest.raises(ValidationFailed):
        world.run(lambda u: dims.set_max_depth(u, world.company, "item_contable", 11))


def test_T_ACC_DIM_03_hierarchy_rules_on_move(world):
    a = world.node("cost_center", "A", "AGRUPADOR")
    b = world.node("cost_center", "B", "AGRUPADOR", parent=a)
    c = world.node("cost_center", "C", "AGRUPADOR", parent=b)
    leaf = world.node("cost_center", "C-1", "DETALLE", parent=c)
    other = world.node("business_unit", "BU", "AGRUPADOR")

    def move(node, parent):
        def fn(u):
            n = dims.get_node(u.ctx, world.company, "cost_center", node.id, lock=True)
            return dims.move_node(u, n, str(parent.id) if parent is not None else None)

        return world.run(fn)

    # Cycle: under itself and under a descendant.
    assert _code(lambda: move(a, a))[0] == "dimension_cycle"
    assert _code(lambda: move(a, c))[0] == "dimension_cycle"
    # DETALLE as the new parent.
    assert _code(lambda: move(b, leaf))[1]["reason"] == "parent_not_agrupador"
    # Cross-dimension parent.
    assert _code(lambda: move(b, other))[1]["reason"] == "parent_other_dimension"

    # Depth: chain d1..d4 (4 levels) + moving subtree a (4 levels incl. leaf) under d4 → depth 8 > 6.
    d = None
    for i in range(1, 5):
        d = world.node("cost_center", f"D{i}", "AGRUPADOR", parent=d)
    assert _code(lambda: move(a, d))[0] == "dimension_depth_exceeded"

    # A valid move rebuilds paths/depths and the closure; nothing else changes.
    moved = move(c, a)
    assert (moved.parent_id, moved.depth, moved.path) == (a.id, 2, "A/C")
    with world.scope():
        refreshed = DimensionNode.objects.get(id=leaf.id)
        current = set(
            DimensionClosure.objects.filter(descendant_id=leaf.id, valid_to__isnull=True).values_list(
                "ancestor_id", "distance"
            )
        )
    assert (refreshed.depth, refreshed.path) == (3, "A/C/C-1")
    assert current == {(leaf.id, 0), (c.id, 1), (a.id, 2)}
    assert set(dims.subtree_ids(world.company, b.id)) == {b.id}
