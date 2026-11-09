"""``kernel.interfaces.Dimensions`` implementation (consumer: P04 role scopes)."""

from __future__ import annotations

import datetime as dt
import uuid

from kernel import tenancy
from kernel.errors import NotFound
from kernel.interfaces.dimensions import DimensionNode as NodeInfo
from ledger.dimensions import service
from ledger.models import DimensionNode


def _info(n: DimensionNode) -> NodeInfo:
    return NodeInfo(
        n.id,
        n.company_id,
        n.dimension,
        n.code,
        n.description,
        n.node_type,
        n.parent_id,
        n.status == "ACTIVE",  # type: ignore[arg-type]
    )


class LedgerDimensions:
    def get_node(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID) -> NodeInfo:
        ctx = tenancy.require_current()
        return _info(service.get_node(ctx, company_id, service.check_dimension(dimension), node_id))

    def subtree(
        self, company_id: uuid.UUID, dimension: str, root_id: uuid.UUID, valid_on: dt.date | None = None
    ) -> list[uuid.UUID]:
        ctx = tenancy.require_current()
        service.get_node(ctx, company_id, service.check_dimension(dimension), root_id)
        return service.subtree_ids(company_id, root_id, valid_on)

    def assert_postable(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID, on: dt.date) -> None:
        ctx = tenancy.require_current()
        ctx.require_company(company_id)
        if dimension not in service.DIMENSIONS:
            raise NotFound("unknown dimension")
        service.check_line_reference(ctx, company_id, dimension, node_id, on)
