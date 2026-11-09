"""Configurable approvals with segregation of duties (WorkflowService, owner P07).

Policy matching (highest priority first): document type + action, amount range
(COP), party / cost-center lists and an optional boolean condition written in
the safe formula DSL (domain ``approval``). Steps run sequentially; within a
step several assignees decide in parallel until the step's quorum is reached.
Escalation adds the ``escalate_to`` assignee after ``escalation_hours``;
delegation lets an assignee hand a request to someone else (or an absence
delegation covers a period). The requester never decides (``sod_violation``),
and P04's policy is asked for ``workflow.approval.approve`` on every decision.
An approval never posts: the owning module checks :meth:`is_approved` and
posts in its own unit of work.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db.models import Q

from kernel import clock
from kernel.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.workflow import ApprovalRequest as ApprovalRequestDTO
from kernel.money import to_decimal
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, require_uow
from workflow.approvals.directory import directory
from workflow.audit.service import record_change
from workflow.formula import FormulaDefinition, FormulaGraph, InputSchema
from workflow.formula.errors import FormulaError
from workflow.notifications.helpers import notify
from workflow.policy import policy

MAX_STEPS = 10
STEP_KINDS = ("user", "role", "manager")


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValidationFailed(f"an approval policy needs 1–{MAX_STEPS} steps")
    out = []
    for i, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise ValidationFailed(f"step {i + 1} must be an object")
        approvers = step.get("approvers") or [{"kind": step.get("kind"), "ref": step.get("ref")}]
        clean = []
        for a in approvers:
            kind = a.get("kind")
            if kind not in STEP_KINDS:
                raise ValidationFailed(f"step {i + 1}: approver kind must be one of {STEP_KINDS}")
            ref = a.get("ref")
            if kind == "user":
                ref = str(uuid.UUID(str(ref)))
            elif kind == "role" and not (isinstance(ref, str) and ref):
                raise ValidationFailed(f"step {i + 1}: role approvers need a role code")
            clean.append({"kind": kind, "ref": ref})
        quorum = int(step.get("quorum", 1))
        if quorum < 1:
            raise ValidationFailed(f"step {i + 1}: quorum must be ≥ 1")
        out.append({"approvers": clean, "quorum": quorum, "name": str(step.get("name", f"Paso {i + 1}"))[:80]})
    return out


def condition_definition(policy_row: Any) -> FormulaDefinition | None:
    if not policy_row.condition:
        return None
    return FormulaDefinition(
        code="condicion",
        expression=policy_row.condition,
        schema=InputSchema.from_json(policy_row.condition_inputs or {"amount": "money"}),
        result_type="boolean",
    )


def validate_condition(expression: str, inputs: Mapping[str, Any] | None) -> None:
    if not expression:
        return
    d = FormulaDefinition("condicion", expression, InputSchema.from_json(inputs or {"amount": "money"}), "boolean")
    FormulaGraph([d])  # parse + type check + budgets; raises stable formula_* errors


def to_dto(req: Any) -> ApprovalRequestDTO:
    return ApprovalRequestDTO(
        id=req.id,
        document=DocumentRef(req.document_type, req.document_id, req.document_number or None),
        state=req.state,
        requested_by=req.requested_by,
    )


class DbWorkflowService:
    # ------------------------------------------------------------------ matching
    def matching_policy(
        self,
        ctx: TenantContext,
        company_id: uuid.UUID,
        document_type: str,
        action: str,
        facts: Mapping[str, Any],
    ) -> Any | None:
        from workflow.models import ApprovalPolicy

        amount = facts.get("amount")
        amount_d = to_decimal(amount) if amount is not None else None
        candidates = ApprovalPolicy.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, document_type=document_type, action=action, active=True
        ).order_by("priority", "code")
        for p in candidates:
            if amount_d is not None:
                if p.min_amount is not None and amount_d < p.min_amount:
                    continue
                if p.max_amount is not None and amount_d > p.max_amount:
                    continue
            elif p.min_amount is not None or p.max_amount is not None:
                continue
            if p.party_ids and str(facts.get("party_id")) not in {str(x) for x in p.party_ids}:
                continue
            if p.cost_center_ids and str(facts.get("cost_center_id")) not in {str(x) for x in p.cost_center_ids}:
                continue
            definition = condition_definition(p)
            if definition is not None:
                inputs = {k: v for k, v in facts.items() if k in definition.schema.fields}
                try:
                    if not FormulaGraph([definition]).evaluate(inputs, explain=False)["condicion"].value:
                        continue
                except FormulaError:
                    # A condition that cannot be evaluated matches (fail safe: ask for approval).
                    pass
            return p
        return None

    # ------------------------------------------------------------------ assignment
    def _assignees(self, ctx: TenantContext, req: Any, step: Mapping[str, Any]) -> list[tuple[uuid.UUID | None, str]]:
        out: list[tuple[uuid.UUID | None, str]] = []
        for a in step["approvers"]:
            if a["kind"] == "user":
                out.append((uuid.UUID(a["ref"]), ""))
            elif a["kind"] == "role":
                out.append((None, a["ref"]))
            else:
                manager = directory().manager_of(ctx, req.company_id, req.requested_by)
                if manager is None:
                    raise ValidationFailed("the requester has no manager configured", code="approver_unresolved")
                out.append((manager, ""))
        return out

    def _assign_step(self, uow: UnitOfWork, req: Any, step_index: int) -> None:
        from workflow.models import ApprovalAssignment

        step = req.policy.steps[step_index]
        principals: set[uuid.UUID] = set()
        for principal_id, role in self._assignees(uow.ctx, req, step):
            ApprovalAssignment.objects.create(
                tenant_id=req.tenant_id, request=req, step=step_index, principal_id=principal_id, role=role
            )
            if principal_id:
                principals.add(principal_id)
            else:
                principals |= directory().members_of_role(uow.ctx, req.company_id, role)
        principals.discard(req.requested_by)
        hours = req.policy.escalation_hours
        req.due_at = clock.now() + dt.timedelta(hours=hours) if hours else None
        for p in sorted(principals):
            _notify(uow, p, req, "aprobacion_pendiente")

    # ------------------------------------------------------------------ requests
    def request(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        document: DocumentRef,
        action: str = "post",
        *,
        facts: Mapping[str, Any] | None = None,
        reason: str = "",
        policy_code: str | None = None,
    ) -> ApprovalRequestDTO | None:
        """``WorkflowService.request(document_ref, action)``: None when no policy matches (post freely)."""
        from workflow.models import ApprovalPolicy, ApprovalRequest

        ctx = uow.ctx
        ctx.require_company(company_id)
        facts = dict(facts or {})
        existing = (
            ApprovalRequest.objects.select_for_update()
            .filter(
                tenant_id=ctx.tenant_id, company_id=company_id, document_type=document.type, document_id=document.id,
                action=action, state__in=[ApprovalRequest.PENDING, ApprovalRequest.APPROVED],
            )  # fmt: skip
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return to_dto(existing)  # one live request per document + action (idempotent)
        if policy_code is not None:
            pol = ApprovalPolicy.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, code=policy_code, active=True
            ).first()
            if pol is None:
                raise NotFound(f"approval policy {policy_code} not found")
        else:
            pol = self.matching_policy(ctx, company_id, document.type, action, facts)
            if pol is None:
                return None
        amount = facts.get("amount")
        req = ApprovalRequest.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, policy=pol, document_type=document.type,
            document_id=document.id, document_number=document.number or "", action=action,
            amount=to_decimal(amount) if amount is not None else None,
            context={k: str(v) for k, v in facts.items()}, requested_by=ctx.principal_id, reason=reason,
            created_by=ctx.principal_id, updated_by=ctx.principal_id,
        )  # fmt: skip
        self._assign_step(uow, req, 0)
        req.save(update_fields=["due_at"])
        record_change(
            "workflow.approval.request", "workflow.approval", req.id, company_id=company_id,
            after={"document_type": document.type, "document_id": document.id, "policy": pol.code, "state": req.state},
            reason=reason, uow=uow,
        )  # fmt: skip
        return to_dto(req)

    def request_approval(
        self, uow: UnitOfWork, company_id: uuid.UUID, document: DocumentRef, policy_code: str, reason: str = ""
    ) -> ApprovalRequestDTO:
        dto = self.request(uow, company_id, document, "post", reason=reason, policy_code=policy_code)
        if dto is None:
            raise NotFound("approval policy not found")
        return dto

    def is_approved(self, ctx: TenantContext, document: DocumentRef, action: str = "post") -> bool:
        from workflow.models import ApprovalRequest

        return ApprovalRequest.objects.filter(
            tenant_id=ctx.tenant_id, document_type=document.type, document_id=document.id, action=action,
            state=ApprovalRequest.APPROVED,
        ).exists()  # fmt: skip

    def require_approved(
        self, uow: UnitOfWork, company_id: uuid.UUID, document: DocumentRef, action: str, facts: Mapping[str, Any]
    ) -> None:
        """Modules call this before ``:post``: raises ``approval_required`` while a matching policy is not approved."""
        if self.is_approved(uow.ctx, document, action):
            return
        dto = self.request(uow, company_id, document, action, facts=facts)
        if dto is not None and dto.state != "APPROVED":
            raise Conflict("the document needs approval before posting", code="approval_required", approval_id=str(dto.id))

    def state(self, request_id: uuid.UUID) -> ApprovalRequestDTO:
        from workflow.models import ApprovalRequest

        uow = require_uow()
        req = ApprovalRequest.objects.filter(tenant_id=uow.ctx.tenant_id, id=request_id).first()
        if req is None:
            raise NotFound("approval request not found")
        return to_dto(req)

    # ------------------------------------------------------------------ deciding
    def _load_pending(self, uow: UnitOfWork, request_id: uuid.UUID) -> Any:
        from workflow.models import ApprovalRequest

        req = (
            ApprovalRequest.objects.select_for_update()
            .select_related("policy")
            .filter(tenant_id=uow.ctx.tenant_id, company_id__in=list(uow.ctx.company_ids), id=request_id)
            .first()
        )
        if req is None:
            raise NotFound("approval request not found")
        if req.state != ApprovalRequest.PENDING:
            raise Conflict(f"the request is already {req.state}", code="approval_not_pending")
        return req

    def _acting_for(self, uow: UnitOfWork, req: Any) -> uuid.UUID | None:
        """The assignee the caller acts for (itself, a delegator, a role) or None."""
        from workflow.models import ApprovalAssignment, ApprovalDelegation

        me = uow.ctx.principal_id
        assignments = list(ApprovalAssignment.objects.filter(request=req, step=req.current_step))
        direct = {a.principal_id for a in assignments if a.principal_id}
        if me in direct:
            return me
        for a in assignments:
            if a.role and me in directory().members_of_role(uow.ctx, req.company_id, a.role):
                return me
        now = clock.now()
        delegators = set(
            ApprovalDelegation.objects.filter(
                tenant_id=req.tenant_id, company_id=req.company_id, delegate_id=me, valid_from__lte=now, valid_to__gte=now
            ).values_list("delegator_id", flat=True)
        )
        hit = sorted(direct & delegators)
        return hit[0] if hit else None

    def decide(self, uow: UnitOfWork, request_id: uuid.UUID, approve: bool, reason: str) -> ApprovalRequestDTO:
        from workflow.models import ApprovalDecision, ApprovalRequest

        ctx = uow.ctx
        req = self._load_pending(uow, request_id)
        if ctx.principal_id == req.requested_by or (ctx.on_behalf_of and ctx.on_behalf_of == req.requested_by):
            raise PermissionDenied("the requester cannot decide their own request (SoD)", code="sod_violation")
        decision = policy().authorize(ctx, "workflow.approval.approve", req)
        if not decision.allowed:
            code = "sod_violation" if "sod" in decision.reason.lower() else "permission_denied"
            raise PermissionDenied(decision.reason or "missing permission workflow.approval.approve", code=code)
        acting_for = self._acting_for(uow, req)
        if acting_for is None:
            raise PermissionDenied("you are not an approver of the current step", code="approver_not_assigned")
        if acting_for == req.requested_by:
            raise PermissionDenied("a delegation of the requester cannot approve (SoD)", code="sod_violation")
        if not approve and not reason.strip():
            raise ValidationFailed("a rejection needs a reason")
        if ApprovalDecision.objects.filter(request=req, step=req.current_step, decider_id=ctx.principal_id).exists():
            raise Conflict("you already decided this step", code="approval_already_decided")
        ApprovalDecision.objects.create(
            tenant_id=req.tenant_id, request=req, step=req.current_step, decider_id=ctx.principal_id,
            on_behalf_of=acting_for if acting_for != ctx.principal_id else None, approve=approve, reason=reason,
        )  # fmt: skip
        before = {"state": req.state, "current_step": req.current_step}
        if not approve:
            req.state = ApprovalRequest.REJECTED
        else:
            step = req.policy.steps[req.current_step]
            approvals = ApprovalDecision.objects.filter(request=req, step=req.current_step, approve=True).count()
            if approvals >= step["quorum"]:
                if req.current_step + 1 < len(req.policy.steps):
                    req.current_step += 1
                    req.escalated_at = None
                    self._assign_step(uow, req, req.current_step)
                else:
                    req.state = ApprovalRequest.APPROVED
        if req.state != ApprovalRequest.PENDING:
            req.decided_at = clock.now()
            uow.emit(
                "workflow.approval.decided.v1",
                {
                    "approval_id": str(req.id),
                    "resource": {"type": req.document_type, "id": str(req.document_id), "number": req.document_number or None},
                    "decision": req.state,
                },
                company_id=req.company_id,
            )
            _notify(uow, req.requested_by, req, "aprobacion_decidida")
        req.save_versioned(ctx.principal_id, ["state", "current_step", "decided_at", "due_at", "escalated_at"])
        record_change(
            "workflow.approval.approve" if approve else "workflow.approval.reject", "workflow.approval", req.id,
            company_id=req.company_id, before=before, after={"state": req.state, "current_step": req.current_step},
            reason=reason, uow=uow,
        )  # fmt: skip
        return to_dto(req)

    def delegate(self, uow: UnitOfWork, request_id: uuid.UUID, to_principal: uuid.UUID, reason: str) -> ApprovalRequestDTO:
        from workflow.models import ApprovalAssignment

        req = self._load_pending(uow, request_id)
        acting_for = self._acting_for(uow, req)
        if acting_for is None:
            raise PermissionDenied("only an approver of the current step can delegate", code="approver_not_assigned")
        if to_principal == req.requested_by:
            raise PermissionDenied("cannot delegate to the requester (SoD)", code="sod_violation")
        ApprovalAssignment.objects.create(
            tenant_id=req.tenant_id, request=req, step=req.current_step, principal_id=to_principal
        )
        uow.emit(
            "workflow.approval.decided.v1",
            {"approval_id": str(req.id), "resource": {"type": req.document_type, "id": str(req.document_id),
             "number": req.document_number or None}, "decision": "DELEGATED"},
            company_id=req.company_id,
        )  # fmt: skip
        _notify(uow, to_principal, req, "aprobacion_pendiente")
        record_change(
            "workflow.approval.delegate", "workflow.approval", req.id, company_id=req.company_id,
            after={"delegated_to": to_principal}, reason=reason, uow=uow,
        )  # fmt: skip
        return to_dto(req)

    def cancel(self, uow: UnitOfWork, request_id: uuid.UUID, reason: str) -> ApprovalRequestDTO:
        from workflow.models import ApprovalRequest

        req = self._load_pending(uow, request_id)
        if uow.ctx.principal_id != req.requested_by:
            raise PermissionDenied("only the requester can cancel")
        req.state = ApprovalRequest.CANCELLED
        req.decided_at = clock.now()
        req.save_versioned(uow.ctx.principal_id, ["state", "decided_at"])
        record_change("workflow.approval.cancel", "workflow.approval", req.id, company_id=req.company_id,
                      after={"state": req.state}, reason=reason, uow=uow)
        return to_dto(req)

    # ------------------------------------------------------------------ escalation
    def escalate_overdue(self, uow: UnitOfWork, now: dt.datetime | None = None) -> int:
        """Add the escalation assignee to every pending request past its due time (idempotent)."""
        from workflow.models import ApprovalAssignment, ApprovalRequest

        now = now or clock.now()
        overdue = (
            ApprovalRequest.objects.select_for_update(skip_locked=True)
            .select_related("policy")
            .filter(
                tenant_id=uow.ctx.tenant_id, company_id__in=list(uow.ctx.company_ids), state=ApprovalRequest.PENDING,
                due_at__lt=now, escalated_at__isnull=True,
            )  # fmt: skip
        )
        count = 0
        for req in overdue:
            target = req.policy.escalate_to or {}
            principal: uuid.UUID | None = None
            role = ""
            if target.get("kind") == "user":
                principal = uuid.UUID(str(target["ref"]))
            elif target.get("kind") == "role":
                role = str(target["ref"])
            else:
                principal = directory().manager_of(uow.ctx, req.company_id, req.requested_by)
            if principal is None and not role:
                continue
            ApprovalAssignment.objects.create(
                tenant_id=req.tenant_id, request=req, step=req.current_step, principal_id=principal, role=role,
                escalation=True,
            )  # fmt: skip
            req.escalated_at = now
            req.escalation_level += 1
            req.save(update_fields=["escalated_at", "escalation_level"])
            recipients = {principal} if principal else directory().members_of_role(uow.ctx, req.company_id, role)
            for p in sorted(r for r in recipients if r and r != req.requested_by):
                _notify(uow, p, req, "aprobacion_escalada")
            record_change("workflow.approval.escalate", "workflow.approval", req.id, company_id=req.company_id,
                          after={"escalated_to": str(principal or role)}, uow=uow)
            count += 1
        return count

    # ------------------------------------------------------------------ inbox / KPIs
    def inbox(self, ctx: TenantContext, company_id: uuid.UUID | None = None) -> list[Any]:
        from workflow.models import ApprovalAssignment, ApprovalDelegation, ApprovalRequest

        me = ctx.principal_id
        companies = [company_id] if company_id else list(ctx.company_ids)
        roles: set[str] = set()
        for c in companies:
            for role in ApprovalAssignment.objects.filter(
                tenant_id=ctx.tenant_id, request__company_id=c, request__state="PENDING"
            ).exclude(role="").values_list("role", flat=True):
                if me in directory().members_of_role(ctx, c, role):
                    roles.add(role)
        now = clock.now()
        delegators = list(
            ApprovalDelegation.objects.filter(
                tenant_id=ctx.tenant_id, delegate_id=me, valid_from__lte=now, valid_to__gte=now
            ).values_list("delegator_id", flat=True)
        )
        mine = ApprovalAssignment.objects.filter(
            Q(principal_id=me) | Q(role__in=roles) | Q(principal_id__in=delegators)
        ).values("request_id", "step")
        pending = ApprovalRequest.objects.filter(
            tenant_id=ctx.tenant_id, company_id__in=companies, state=ApprovalRequest.PENDING
        ).exclude(requested_by=me)
        wanted = {(r["request_id"], r["step"]) for r in mine}
        return [r for r in pending.order_by("created_at") if (r.id, r.current_step) in wanted]

    def kpis(self, ctx: TenantContext, company_id: uuid.UUID, start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
        from workflow.models import ApprovalRequest

        inbox = self.inbox(ctx, company_id)
        now = clock.now()
        resolved = ApprovalRequest.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, decided_at__gte=start, decided_at__lt=end,
            state__in=[ApprovalRequest.APPROVED, ApprovalRequest.REJECTED],
        ).values_list("created_at", "decided_at")  # fmt: skip
        seconds = [(d - c) // dt.timedelta(seconds=1) for c, d in resolved]
        avg = (Decimal(sum(seconds)) / Decimal(3600) / len(seconds)).quantize(Decimal("0.01")) if seconds else None
        return {
            "pendientes": len(inbox),
            "vencidas_sla": sum(1 for r in inbox if r.due_at and r.due_at < now),
            "tiempo_aprobacion": avg,
        }


def _notify(uow: UnitOfWork, recipient: uuid.UUID, req: Any, template: str) -> None:
    data = {
        "documento": f"{req.document_type} {req.document_number or ''}".strip(),
        "estado": req.state,
        "approval_id": str(req.id),
    }
    notify(uow, recipient, req.company_id, template, data)
