"""WorkflowService (owner P07): approvals with segregation of duties.

Contract: the requester never approves (D-023); decisions are audited; an
approval never posts by itself — the owning module posts in its own UoW.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork

ApprovalState = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: uuid.UUID
    document: DocumentRef
    state: ApprovalState
    requested_by: uuid.UUID


@runtime_checkable
class WorkflowService(Protocol):
    def request_approval(
        self, uow: UnitOfWork, company_id: uuid.UUID, document: DocumentRef, policy_code: str, reason: str = ""
    ) -> ApprovalRequest:
        """Start an approval; returns the PENDING request (idempotent per document+policy)."""
        ...

    def decide(self, uow: UnitOfWork, request_id: uuid.UUID, approve: bool, reason: str) -> ApprovalRequest:
        """Approve/reject; ``sod_violation`` if the decider is the requester."""
        ...

    def state(self, request_id: uuid.UUID) -> ApprovalRequest: ...
