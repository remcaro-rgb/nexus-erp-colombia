"""Role scope by cost-center subtree (F-ACC-DIM contributor, T-ACC-DIM-09).

The cost-center master is P05's (``kernel.interfaces.Dimensions``). A role assignment
with ``scope.cost_center_ids = [root, …]`` grants the subtrees of those nodes. The same
check limits every channel:

* list / search: :func:`secure_nodes` (and ``scope_queryset`` for tables with ``cost_center_id``);
* tree: :func:`secure_tree` (nodes outside the grant are dropped, granted roots become roots);
* export / reports / AI / MCP: :func:`iam.policy.secure_rows` with rows carrying ``cost_center_id``.

Listing needs ``list_nodes(company_id, dimension)`` on the Dimensions provider (CCR P04-3);
without it the list is empty (fail closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from iam.policy.engine import get_engine
from kernel import services
from kernel.interfaces.dimensions import DimensionNode, Dimensions
from kernel.tenancy import TenantContext


def visible_ids(ctx: TenantContext, action: str, company_id: uuid.UUID) -> frozenset[uuid.UUID] | None:
    """Cost-center ids visible for ``action`` in ``company_id``; ``None`` = unrestricted; empty = nothing."""
    eff = get_engine().effective(ctx)
    grants = [g for g in eff.allowing(action) if g.company_id == company_id]
    if not grants or company_id not in ctx.company_ids:
        return frozenset()
    visible: set[uuid.UUID] = set()
    for g in grants:
        ids = g.scope("cost_center_ids")
        if ids is None:
            return None
        visible |= ids
    return frozenset(visible)


def all_nodes(company_id: uuid.UUID, dimension: str = "cost_center") -> list[DimensionNode]:
    dims = services.optional(Dimensions)
    lister = getattr(dims, "list_nodes", None)
    if dims is None or lister is None:
        return []
    return [n for n in lister(company_id, dimension) if n.company_id == company_id]


def secure_nodes(
    ctx: TenantContext, action: str, nodes: Iterable[DimensionNode], query: str = ""
) -> list[DimensionNode]:
    out: list[DimensionNode] = []
    cache: dict[uuid.UUID, frozenset[uuid.UUID] | None] = {}
    q = query.strip().lower()
    for n in nodes:
        if n.company_id not in cache:
            cache[n.company_id] = visible_ids(ctx, action, n.company_id)
        allowed = cache[n.company_id]
        if allowed is not None and n.id not in allowed:
            continue
        if q and q not in n.code.lower() and q not in n.description.lower():
            continue
        out.append(n)
    return out


def secure_tree(ctx: TenantContext, action: str, nodes: Iterable[DimensionNode]) -> list[dict[str, Any]]:
    """Nested tree of the visible nodes (``{"id", "code", "description", "node_type", "children"}``)."""
    visible = secure_nodes(ctx, action, nodes)
    ids = {n.id for n in visible}
    children: dict[uuid.UUID | None, list[DimensionNode]] = {}
    for n in visible:
        parent = n.parent_id if n.parent_id in ids else None
        children.setdefault(parent, []).append(n)

    def build(parent: uuid.UUID | None) -> list[dict[str, Any]]:
        return [
            {
                "id": str(n.id),
                "code": n.code,
                "description": n.description,
                "node_type": n.node_type,
                "children": build(n.id),
            }
            for n in sorted(children.get(parent, []), key=lambda x: x.code)
        ]

    return build(None)
