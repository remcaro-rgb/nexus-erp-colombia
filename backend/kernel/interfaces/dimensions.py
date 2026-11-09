"""Dimensions (owner P05; consumer P04 role scopes). FEATURES-ADDENDUM stub.

Contract: lookups are company-scoped; ``subtree`` follows the effective-dated
closure table (default current hierarchy); unknown ids → ``NotFound``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

NodeType = Literal["AGRUPADOR", "DETALLE"]


@dataclass(frozen=True, slots=True)
class DimensionNode:
    id: uuid.UUID
    company_id: uuid.UUID
    dimension: str  # kernel.dimensions key
    code: str
    description: str
    node_type: NodeType
    parent_id: uuid.UUID | None
    active: bool = True


@runtime_checkable
class Dimensions(Protocol):
    def get_node(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID) -> DimensionNode: ...

    def subtree(
        self, company_id: uuid.UUID, dimension: str, root_id: uuid.UUID, valid_on: dt.date | None = None
    ) -> list[uuid.UUID]:
        """Ids of ``root_id`` and all its descendants."""
        ...

    def assert_postable(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID, on: dt.date) -> None:
        """``dimension_node_not_postable`` / ``dimension_cross_company`` / archived."""
        ...
