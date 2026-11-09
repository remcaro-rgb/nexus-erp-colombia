"""Data-subject requests under Ley 1581/2012 arts. 14–15.

* consulta: answer within 10 business days, extendable once by 5 (art. 14).
* reclamo: answer within 15 business days, extendable once by 8 (art. 15).
  (Term values come from the statute text cited in the pack, S-24.)
* identity verification before any data is disclosed;
* discovery across modules through ``dsr_registry``; export package;
  suppression denied (with the legal reason) where retention applies.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from workflow.audit.service import record_change
from workflow.dsr.calendar import HolidayCalendar, add_business_days, calendar
from workflow.dsr.registry import sources_for
from workflow.policy import policy

TERMS = {"consulta": (10, 5), "reclamo": (15, 8)}
SUBJECT_TYPES = ("employee", "customer", "supplier", "contact")
RIGHTS = ("access", "rectification", "suppression", "revocation")


def sla(kind: str, received_on: dt.date, cal: HolidayCalendar) -> tuple[dt.date, dt.date]:
    """(due date, maximum date after the single allowed extension)."""
    if kind not in TERMS:
        raise ValidationFailed("kind must be consulta or reclamo")
    base, extension = TERMS[kind]
    due = add_business_days(received_on, base, cal)
    return due, add_business_days(due, extension, cal)


class DsrService:
    def __init__(self, cal: HolidayCalendar | None = None) -> None:
        self._cal = cal

    @property
    def cal(self) -> HolidayCalendar:
        return self._cal or calendar()

    def _get(self, ctx: TenantContext, request_id: uuid.UUID, lock: bool = False) -> Any:
        from workflow.models import DsrRequest

        qs = DsrRequest.objects.select_for_update() if lock else DsrRequest.objects
        req = qs.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=request_id).first()
        if req is None:
            raise NotFound("DSR request not found")
        return req

    def intake(
        self, ctx: TenantContext, company_id: uuid.UUID, *, kind: str, right: str, subject_type: str, subject_ref: str,
        requester_name: str, requester_email: str = "", description: str = "", received_on: dt.date | None = None,
        idempotency_key: str | None = None,
    ) -> Any:  # fmt: skip
        from workflow.models import DsrRequest

        ctx.require_company(company_id)
        policy().require(ctx, "workflow.dsr_request.create", {"company_id": company_id})
        if subject_type not in SUBJECT_TYPES or right not in RIGHTS:
            raise ValidationFailed("unknown subject type or right")
        received = received_on or clock.business_date()
        due, max_due = sla(kind, received, self.cal)

        def work(uow: UnitOfWork) -> Any:
            req = DsrRequest.objects.create(
                tenant_id=ctx.tenant_id, company_id=company_id, kind=kind, right=right, subject_type=subject_type,
                subject_ref=subject_ref[:80], requester_name=requester_name[:200], requester_email=requester_email[:200],
                description=description, received_on=received, due_on=due, max_due_on=max_due,
                created_by=ctx.principal_id, updated_by=ctx.principal_id,
            )  # fmt: skip
            record_change("workflow.dsr_request.create", "workflow.dsr_request", req.id, company_id=company_id,
                          after={"kind": kind, "right": right, "requester_name": requester_name, "due_on": due},
                          uow=uow)  # fmt: skip
            return req

        return run_in_unit_of_work(work, ctx, idempotency_key=idempotency_key)

    def _transition(self, ctx: TenantContext, request_id: uuid.UUID, action: str, fn: Any) -> Any:
        def work(uow: UnitOfWork) -> Any:
            req = self._get(ctx, request_id, lock=True)
            if req.state in ("CLOSED", "DENIED"):
                raise Conflict(f"the request is {req.state}")
            before = {"state": req.state}
            fn(req)
            req.save_versioned(ctx.principal_id, [
                "state", "identity_verified_at", "identity_method", "due_on", "extended", "extension_reason",
                "response", "denial_reason", "export", "closed_at",
            ])  # fmt: skip
            record_change(f"workflow.dsr_request.{action}", "workflow.dsr_request", req.id, company_id=req.company_id,
                          before=before, after={"state": req.state}, uow=uow)
            return req

        return run_in_unit_of_work(work, ctx)

    def verify_identity(self, ctx: TenantContext, request_id: uuid.UUID, method: str) -> Any:
        policy().require(ctx, "workflow.dsr_request.update")

        def fn(req: Any) -> None:
            req.identity_verified_at, req.identity_method, req.state = clock.now(), method[:60], "VERIFIED"

        return self._transition(ctx, request_id, "verify_identity", fn)

    def extend(self, ctx: TenantContext, request_id: uuid.UUID, reason: str) -> Any:
        policy().require(ctx, "workflow.dsr_request.update")
        if not reason.strip():
            raise ValidationFailed("the extension must state its reasons to the data subject")

        def fn(req: Any) -> None:
            if req.extended:
                raise Conflict("the term can be extended only once")
            req.extended, req.extension_reason, req.due_on = True, reason, req.max_due_on

        return self._transition(ctx, request_id, "extend", fn)

    def discover(self, ctx: TenantContext, request_id: uuid.UUID) -> dict[str, Any]:
        req = self._get(ctx, request_id)
        policy().require(ctx, "workflow.dsr_request.read", req)
        if req.identity_verified_at is None:
            raise Conflict("verify the identity of the data subject first", code="dsr_identity_unverified")
        found: dict[str, Any] = {}
        for source in sources_for(req.subject_type):
            found[source.code] = {"fields": list(source.fields), "records": source.find(ctx, req.subject_ref)}
        return found

    def fulfil(self, ctx: TenantContext, request_id: uuid.UUID, response: str = "") -> Any:
        policy().require(ctx, "workflow.dsr_request.post")
        req = self._get(ctx, request_id)
        package = self.discover(ctx, request_id) if req.right == "access" else {}
        denials: list[str] = []
        suppressed = 0
        if req.right == "suppression":
            for source in sources_for(req.subject_type):
                if source.retention:
                    denials.append(f"{source.code}: {source.retention}")
                elif source.suppress is not None:
                    suppressed += source.suppress(ctx, req.subject_ref)

        def fn(r: Any) -> None:
            r.export = {"package": package, "suppressed": suppressed, "denied": denials}
            r.response = response
            if denials:
                r.denial_reason = "Supresión parcialmente denegada por deber legal de conservación: " + "; ".join(denials)
            r.state, r.closed_at = "CLOSED", clock.now()

        return self._transition(ctx, request_id, "fulfil", fn)

    def reject(self, ctx: TenantContext, request_id: uuid.UUID, reason: str) -> Any:
        policy().require(ctx, "workflow.dsr_request.approve")
        if not reason.strip():
            raise ValidationFailed("a denial needs a reason")

        def fn(req: Any) -> None:
            req.state, req.denial_reason, req.closed_at = "DENIED", reason, clock.now()

        return self._transition(ctx, request_id, "reject", fn)

    def overdue(self, ctx: TenantContext, today: dt.date | None = None) -> list[Any]:
        from workflow.models import DsrRequest

        today = today or clock.business_date()
        return list(
            DsrRequest.objects.filter(
                tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), due_on__lt=today
            ).exclude(state__in=["CLOSED", "DENIED"])
        )
