"""In-memory ledger double for consumers (P09–P16) until they test against the real engine.

:class:`FakeLedger` = kernel's balanced-entry recorder (:class:`kernel.testing.fakes.FakePostingService`:
balanced in COP, closed periods, idempotent per source) **plus** the dimension checks of the real
engine: DIM-R01 (only DETALLE), DIM-R04 (same company), archived nodes and a simple REQUIRED /
FORBIDDEN matrix by account role. Error codes are the real ones. Synthetic data only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from kernel.errors import DomainError
from kernel.interfaces.posting import PostingRequest, PostingResult
from kernel.testing.fakes import FakePeriodService, FakePostingService
from kernel.uow import UnitOfWork

SLOTS = {
    "cost_center_id": "cost_center",
    "business_unit_id": "business_unit",
    "aux_abierto_id": "aux_abierto",
    "item_dimension_id": "item_contable",
}


class FakeLedgerError(DomainError):
    code, status, title = "ledger_error", 422, "Ledger rule violated"


@dataclass(frozen=True, slots=True)
class FakeNode:
    id: uuid.UUID
    company_id: uuid.UUID
    dimension: str
    code: str
    node_type: str = "DETALLE"
    active: bool = True


class FakeLedger(FakePostingService):
    def __init__(self, periods: FakePeriodService | None = None) -> None:
        super().__init__(periods)
        self.nodes: dict[uuid.UUID, FakeNode] = {}
        # (account_role, dimension) -> "REQUIRED" | "FORBIDDEN"
        self.rules: dict[tuple[str, str], str] = {}

    def add_node(self, node: FakeNode) -> FakeNode:
        self.nodes[node.id] = node
        return node

    def _check_dimensions(self, request: PostingRequest) -> None:
        for i, line in enumerate(request.lines):
            for slot, dimension in SLOTS.items():
                value = getattr(line.dimensions, slot)
                mode = self.rules.get((line.account_role, dimension))
                if value is None:
                    if mode == "REQUIRED":
                        raise FakeLedgerError(f"line {i + 1} requires {dimension}", code="dimension_required")
                    continue
                if mode == "FORBIDDEN":
                    raise FakeLedgerError(f"line {i + 1} must not carry {dimension}", code="dimension_forbidden")
                node = self.nodes.get(value)
                if node is None or node.company_id != request.company_id or node.dimension != dimension:
                    raise FakeLedgerError(
                        f"{dimension} node is not a node of this company", code="dimension_cross_company"
                    )
                if node.node_type != "DETALLE":
                    raise FakeLedgerError(
                        f"{dimension} {node.code} is an AGRUPADOR", code="dimension_node_not_postable"
                    )
                if not node.active:
                    raise FakeLedgerError(f"{dimension} {node.code} is archived", code="dimension_archived")

    def simulate(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        self._check_dimensions(request)
        return super().simulate(uow, request)

    def post(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        self._check_dimensions(request)
        return super().post(uow, request)
