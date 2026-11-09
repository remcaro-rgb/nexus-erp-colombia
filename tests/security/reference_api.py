"""In-memory reference API used to prove the leak framework itself (mutation testing).

``ReferenceApi`` serves a handful of real contract operations over a synthetic :class:`Dataset`
and enforces the policy with its own, independent code (it does not call ``oracle.can_see``).
Each *mutant* removes exactly one control; the framework must flag every mutant.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from qa_harness.leak import Response
from qa_harness.openapi import CLASS_RANK, Channel, Operation
from qa_harness.oracle import Dataset, Row
from qa_harness.principals import Principal

SUPPORTED = frozenset(
    {
        "GET /api/v1/sales/parties",
        "GET /api/v1/sales/parties/{id}",
        "PATCH /api/v1/sales/parties/{id}",
        "DELETE /api/v1/sales/parties/{id}",
        "GET /api/v1/hr/employees",
        "GET /api/v1/hr/employees/{id}",
        "PATCH /api/v1/hr/employees/{id}",
        "GET /api/v1/inventory/moves",
        "GET /api/v1/inventory/moves/{id}",
        "POST /api/v1/inventory/moves/{id}:cancel",
        "GET /api/v1/inventory/issues:export",
        "GET /api/v1/iam/users",
        "GET /api/v1/iam/users/{id}",
        "POST /api/v1/iam/users/{id}:suspend",
    }
)

MUTANTS = (
    "anonymous",  # no authentication check
    "function",  # no permission (BFLA) check
    "tenant",  # no tenant filter
    "company",  # no company filter
    "owner",  # no own-records filter for salespeople
    "warehouse",  # no warehouse scope for warehouse users
    "mask",  # no field masking
    "error_echo",  # 404 body echoes the protected object's data
    "foreign_403",  # foreign objects answer 403 instead of 404 on reads... still allowed (control)
)

OWNER_SCOPED = {"sales.party"}


class ReferenceApi:
    name = "reference"

    def __init__(self, dataset: Dataset, mutant: str | None = None) -> None:
        if mutant is not None and mutant not in MUTANTS:
            raise ValueError(mutant)
        self.dataset = dataset
        self.mutant = mutant

    # Transport protocol -------------------------------------------------------------------------
    def implemented(self, op: Operation) -> bool:
        return op.key in SUPPORTED

    def can_authenticate(self, principal: Principal) -> bool:
        return True

    def request(self, op: Operation, path: str, principal: Principal | None, headers: dict[str, str]) -> Response:
        if principal is None and self.mutant != "anonymous":
            return Response(401, {"code": "unauthenticated"}, {"WWW-Authenticate": "Bearer"})
        if principal is None:
            return Response(200, {"items": [], "next_cursor": None})
        if self.mutant != "function" and not principal.allows(op.permission):
            return Response(403, {"code": "policy_denied"})
        resource = op.resource
        if op.targets_one_object:
            target = self._lookup(path)
            if target is None or not self._visible(principal, target):
                if self.mutant == "foreign_403":
                    return Response(403, {"code": "policy_denied"})
                body: dict[str, Any] = {"code": "not_found"}
                if self.mutant == "error_echo" and target is not None:
                    body["detail"] = f"{target.fields.get('legal_name') or target.fields.get('name')} no visible"
                return Response(404, body)
            if op.method == "get":
                return Response(200, self._mask(principal, op, target.as_json()))
            return Response(200, {"status": "ok"})
        rows = [r for r in self.dataset.of(resource) if self._visible(principal, r)]
        items = [self._mask(principal, op, r.as_json()) for r in rows]
        if op.channel is Channel.EXPORT:
            return Response(200, _csv(items), {"Content-Type": "text/csv"})
        return Response(200, {"items": items, "next_cursor": None})

    # Policy (independent implementation) --------------------------------------------------------
    def _lookup(self, path: str) -> Row | None:
        for seg in path.replace(":", "/").split("/"):
            try:
                rid = uuid.UUID(seg)
            except ValueError:
                continue
            return self.dataset.rows.get(rid)
        return None

    def _visible(self, p: Principal, r: Row) -> bool:
        if self.mutant != "tenant" and r.tenant_id != p.tenant.id:
            return False
        if self.mutant != "company" and r.company_id is not None and r.company_id not in p.company_ids:
            return False
        if self.mutant != "owner" and p.owner_scoped and r.resource in OWNER_SCOPED:
            user = p.on_behalf_of or p
            if r.owner_id != user.id:
                return False
        return not (
            self.mutant != "warehouse"
            and p.warehouse_ids is not None
            and r.resource.startswith("inventory.")
            and r.warehouse_id is not None
            and r.warehouse_id not in p.warehouse_ids
        )

    def _mask(self, p: Principal, op: Operation, data: dict[str, Any]) -> dict[str, Any]:
        if self.mutant == "mask":
            return data
        limit = CLASS_RANK[p.max_classification]
        classes = op.field_classes()
        return {k: v for k, v in data.items() if CLASS_RANK.get(classes.get(k, "internal"), 99) <= limit}


def _csv(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    cols = sorted({k for it in items for k in it})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for it in items:
        w.writerow({k: it.get(k, "") for k in cols})
    return buf.getvalue()
