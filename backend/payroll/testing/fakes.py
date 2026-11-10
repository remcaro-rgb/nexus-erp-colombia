"""Typed fakes: the HR interface (until P13a's provider is registered) and a field-aware policy."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from kernel.errors import PermissionDenied
from kernel.interfaces.policy import Decision, FieldMask
from kernel.tenancy import TenantContext
from payroll.interfaces import PayrollEmployee


@dataclass
class FakeEmployeeSource:
    """Implements :class:`payroll.interfaces.PayrollEmployeeSource` over an in-memory list (synthetic data)."""

    employees: list[PayrollEmployee] = field(default_factory=list)

    def add(self, employee: PayrollEmployee) -> PayrollEmployee:
        self.employees.append(employee)
        return employee

    def employees_for_period(
        self, company_id: uuid.UUID, start: dt.date, end: dt.date, employee_ids: list[uuid.UUID] | None = None
    ) -> list[PayrollEmployee]:
        out = []
        for e in self.employees:
            if e.company_id != company_id:
                continue
            if employee_ids is not None and e.employee_id not in employee_ids:
                continue
            if (e.start_date and e.start_date > end) or (e.end_date and e.end_date < start):
                continue
            out.append(e)
        return out


class FieldPolicy:
    """Deny-by-default PolicyService fake with per-principal grants and readable fields per resource."""

    def __init__(self) -> None:
        self.grants: set[tuple[uuid.UUID, str]] = set()
        self.readable: dict[tuple[uuid.UUID, str], frozenset[str]] = {}
        self.version = 1

    def grant(self, principal: uuid.UUID, *actions: str) -> None:
        self.grants.update((principal, a) for a in actions)
        self.version += 1

    def allow_fields(self, principal: uuid.UUID, resource_type: str, *fields: str) -> None:
        self.readable[(principal, resource_type)] = frozenset(fields)
        self.version += 1

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        allowed = (ctx.principal_id, action) in self.grants
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self.authorize(ctx, action, resource).allowed:
            raise PermissionDenied(f"missing permission {action}", code="policy_denied")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        return FieldMask(self.readable.get((ctx.principal_id, resource_type), frozenset()), frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version
