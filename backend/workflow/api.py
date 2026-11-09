"""REST API of the workflow module (``contracts/openapi/modules/workflow.yaml``).

Every view needs a bound tenant context (P04 authentication sets it) and asks
the P04 policy through the services; mutating calls require an
``Idempotency-Key`` and run in one unit of work. Errors are RFC 9457
problem+json through ``kernel.errors``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import ModuleSecuredView
from kernel import clock, tenancy
from kernel.errors import DomainError, NotAuthenticated, NotFound, ValidationFailed
from kernel.events import to_jsonable
from kernel.idempotency import StoredResponse
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from workflow.formula.engine import EvaluationResult, ValidationReport
from workflow.policy import policy

STATUS_OUT = {"DRAFT": "DRAFT", "PENDING_APPROVAL": "SUBMITTED", "ACTIVE": "APPROVED", "RETIRED": "RETIRED"}
MAX_LIMIT = 500


# --------------------------------------------------------------------------- plumbing
def context() -> TenantContext:
    ctx = tenancy.current()
    if ctx is None:
        raise NotAuthenticated("authentication required", code="unauthenticated")
    return ctx


def company_of(request: Request, ctx: TenantContext) -> uuid.UUID:
    raw = request.headers.get("X-Company-Id")
    if raw:
        try:
            return ctx.require_company(uuid.UUID(raw))
        except ValueError:
            raise ValidationFailed("X-Company-Id must be a UUID") from None
    if len(ctx.company_ids) == 1:
        return next(iter(ctx.company_ids))
    raise ValidationFailed("X-Company-Id header required (several companies)")


class IdempotencyKeyRequired(DomainError):
    code, status, title = "idempotency_key_required", 400, "Idempotency-Key header required"


def idem_key(request: Request) -> str:
    key = str(request.headers.get("Idempotency-Key", "") or "")
    if not 8 <= len(key) <= 128:
        raise IdempotencyKeyRequired("send an Idempotency-Key header (8-128 characters)")
    return key


def limit_of(request: Request) -> int:
    try:
        return max(1, min(int(request.query_params.get("limit", 50)), MAX_LIMIT))
    except ValueError:
        raise ValidationFailed("limit must be an integer", code="filter_invalid") from None


def mutate(request: Request, scope: str, fn: Callable[[UnitOfWork], tuple[Any, int]]) -> Response:
    """Run ``fn`` in one idempotent UoW; the stored response is replayed for a repeated key."""
    ctx = context()

    def work(uow: UnitOfWork) -> Any:
        body, status = fn(uow)
        body = to_jsonable(body)
        uow.set_response(body, status)
        return body, status

    result = run_in_unit_of_work(work, ctx, idempotency_key=idem_key(request), request=request.data, scope=scope)
    if isinstance(result, StoredResponse):
        return Response(result.body, status=result.status_code)
    body, status = result
    return Response(body, status=status)


def action_result(
    status: str, data: dict[str, Any] | None = None, resource: dict[str, Any] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "data": to_jsonable(data or {})}
    if resource:
        out["resource"] = resource
    return out


def page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": to_jsonable(items), "next_cursor": None}


def parse_uuid(value: Any, name: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed(f"{name} must be a UUID") from None


# --------------------------------------------------------------------------- serializers
def formula_out(ctx: TenantContext, row: Any) -> dict[str, Any]:
    data = {
        "id": row.id, "tenant_id": row.tenant_id, "company_id": row.company_id, "code": row.code,
        "status": "ARCHIVED" if row.status == "RETIRED" else "ACTIVE", "description": row.description,
        "expression": row.expression, "result_type": row.result_type, "inputs": row.inputs,
        "tests": {"cases": row.test_cases, "last_run": row.last_test_run}, "formula_status": STATUS_OUT[row.status],
        "version_no": row.formula_version, "consumers": row.consumers, "version": row.version,
        "domain": row.domain, "depends_on": row.depends_on, "hash": row.hash,
        "effective_from": row.effective_from, "effective_to": row.effective_to,
        "author_id": row.author_id, "approved_by": row.approved_by,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }  # fmt: skip
    return data


def report_out(report: ValidationReport) -> dict[str, Any]:
    return {
        "ok": report.ok, "errors": report.errors, "order": report.order, "hash": report.hash,
        "tests": [{"name": t.name, "passed": t.passed, "got": t.got, "expected": t.expected, "error": t.error}
                  for t in report.tests],
    }  # fmt: skip


def evaluation_out(result: EvaluationResult) -> dict[str, Any]:
    return result.snapshot()


def approval_out(ctx: TenantContext, req: Any) -> dict[str, Any]:
    data = {
        "id": req.id, "company_id": req.company_id, "state": req.state, "policy": req.policy.code,
        "document": {"type": req.document_type, "id": req.document_id, "number": req.document_number or None},
        "action": req.action, "amount": req.amount, "requested_by": req.requested_by, "reason": req.reason,
        "current_step": req.current_step, "due_at": req.due_at, "escalated": req.escalated_at is not None,
        "created_at": req.created_at, "decided_at": req.decided_at, "version": req.version,
    }  # fmt: skip
    # Amounts follow the field policy (deny by default): unreadable → masked.
    if "amount" not in policy().field_mask(ctx, "workflow.approval").readable:
        data["amount"] = None
    return data


def audit_out(ctx: TenantContext, ev: Any) -> dict[str, Any]:
    data = {
        "id": ev.id, "seq": ev.seq, "company_id": ev.company_id, "at": ev.at, "principal_id": ev.principal_id,
        "principal_type": ev.principal_type, "actor_id": ev.actor_id, "channel": ev.channel, "action": ev.action,
        "resource_type": ev.resource_type, "resource_id": ev.resource_id, "changes": ev.changes,
        "request_id": ev.request_id, "trace_id": ev.trace_id, "reason": ev.reason, "hash": ev.hash,
        "prev_hash": ev.prev_hash, "ip": ev.ip, "user_agent": ev.user_agent,
    }  # fmt: skip
    mask = policy().field_mask(ctx, "workflow.audit_event")
    for f in ("ip", "user_agent"):
        if f not in mask.readable:
            data[f] = None
    # Field-level masking of the recorded changes: only readable fields of the audited resource keep values.
    res_mask = policy().field_mask(ctx, ev.resource_type)
    data["changes"] = {k: (v if k in res_mask.readable else {"changed": True}) for k, v in ev.changes.items()}
    return data


def job_out(job: Any) -> dict[str, Any]:
    return {
        "id": job.id, "code": job.code, "job_type": job.job_type, "owner_id": job.owner_id, "cron": job.cron,
        "run_at": job.run_at, "timezone": job.timezone, "params": job.params, "state": job.state,
        "next_run_at": job.next_run_at, "last_run_at": job.last_run_at, "last_result": job.last_result,
        "grant_ref": job.grant_ref, "created_by_agent": job.created_by_agent, "version": job.version,
    }  # fmt: skip


def notification_out(n: Any) -> dict[str, Any]:
    return {"id": n.id, "template_code": n.template_code, "title": n.title, "body": n.body, "link": n.link,
            "created_at": n.created_at, "read_at": n.read_at}  # fmt: skip


# --------------------------------------------------------------------------- base view
class WorkflowView(ModuleSecuredView):
    """P04's secured base: IAM authentication, context required (401); every view asks the policy (deny by default)."""


# --------------------------------------------------------------------------- formulas
class FormulaListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.models import Formula

        ctx = context()
        company = company_of(request, ctx)
        qs = Formula.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
        qs = policy().scope_queryset(ctx, "workflow.formula.read", qs)
        for f in ("domain", "code"):
            if request.query_params.get(f):
                qs = qs.filter(**{f: request.query_params[f]})
        rows = list(qs.order_by("code", "-formula_version")[: limit_of(request)])
        return Response(page([formula_out(ctx, r) for r in rows]))

    def post(self, request: Request) -> Response:
        from workflow.formula.lifecycle import FormulaService

        company = company_of(request, context())
        data = dict(request.data)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            row = FormulaService().create_in(uow, company, data)
            return formula_out(uow.ctx, row), 201

        resp = mutate(request, "workflow.formula.create", fn)
        if resp.status_code == 201:
            resp["ETag"] = FormulaService()._get(context(), parse_uuid(resp.data["id"])).etag
        return resp


class FormulaDetailView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.formula.lifecycle import FormulaService

        ctx = context()
        policy().require(ctx, "workflow.formula.read")
        row = FormulaService()._get(ctx, id)
        resp = Response(to_jsonable(formula_out(ctx, row)))
        resp["ETag"] = row.etag
        return resp

    def patch(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.formula.lifecycle import FormulaService

        ctx = context()
        idem_key(request)
        row = FormulaService().update(ctx, id, dict(request.data), request.headers.get("If-Match"))
        resp = Response(to_jsonable(formula_out(ctx, row)))
        resp["ETag"] = row.etag
        return resp


def _formula_action(name: str) -> type[WorkflowView]:
    class View(WorkflowView):
        def post(self, request: Request, id: uuid.UUID) -> Response:
            from workflow.formula.lifecycle import FormulaService

            ctx = context()
            idem_key(request)
            svc = FormulaService()
            reason = str((request.data or {}).get("reason") or "")
            row = {"submit": lambda: svc.submit(ctx, id), "approve": lambda: svc.approve(ctx, id, reason),
                   "retire": lambda: svc.retire(ctx, id, reason)}[name]()  # fmt: skip
            return Response(action_result(STATUS_OUT[row.status], formula_out(ctx, row),
                                          {"type": "workflow.formula", "id": str(row.id)}))  # fmt: skip

    View.__name__ = f"Formula{name.title()}View"
    return View


class FormulaValidateView(WorkflowView):
    def post(self, request: Request) -> Response:
        from workflow.formula.lifecycle import FormulaService

        ctx = context()
        company = company_of(request, ctx)
        idem_key(request)
        params = dict((request.data or {}).get("parameters") or {})
        report = FormulaService().validate(ctx, company, params, domain=params.get("domain", "approval"))
        return Response(action_result("VALID" if report.ok else "INVALID", report_out(report)))


class FormulaSimulateView(WorkflowView):
    def post(self, request: Request) -> Response:
        from workflow.formula.lifecycle import FormulaService

        ctx = context()
        company = company_of(request, ctx)
        idem_key(request)
        params = dict((request.data or {}).get("parameters") or {})
        inputs = params.pop("values", None) or {}
        result = FormulaService().simulate(ctx, company, params, inputs, domain=params.get("domain", "approval"))
        return Response(action_result("SIMULATED", evaluation_out(result)))


class FormulaVersionsView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.formula.lifecycle import FormulaService

        ctx = context()
        policy().require(ctx, "workflow.formula.read")
        return Response(page([formula_out(ctx, r) for r in FormulaService().versions(ctx, id)]))


# --------------------------------------------------------------------------- approvals
class ApprovalListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.approvals.service import DbWorkflowService

        ctx = context()
        policy().require(ctx, "workflow.approval.read")
        company = company_of(request, ctx)
        items = DbWorkflowService().inbox(ctx, company)
        if request.query_params.get("vencidas") == "1":
            items = [r for r in items if r.due_at and r.due_at < clock.now()]
        return Response(page([approval_out(ctx, r) for r in items[: limit_of(request)]]))


class ApprovalDetailView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.models import ApprovalRequest

        ctx = context()
        policy().require(ctx, "workflow.approval.read")
        req = (
            ApprovalRequest.objects.select_related("policy")
            .filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=id)
            .first()
        )
        if req is None:
            raise NotFound("approval request not found")
        return Response(to_jsonable(approval_out(ctx, req)))


def _approval_action(name: str) -> type[WorkflowView]:
    class View(WorkflowView):
        def post(self, request: Request, id: uuid.UUID) -> Response:
            from workflow.approvals.service import DbWorkflowService
            from workflow.models import ApprovalRequest

            data = request.data or {}
            reason = str(data.get("reason") or "")
            svc = DbWorkflowService()

            def fn(uow: UnitOfWork) -> tuple[Any, int]:
                if name == "delegate":
                    to = parse_uuid((data.get("parameters") or {}).get("to_principal_id"), "to_principal_id")
                    dto = svc.delegate(uow, id, to, reason)
                else:
                    dto = svc.decide(uow, id, name == "approve", reason)
                req = ApprovalRequest.objects.select_related("policy").get(id=dto.id)
                return action_result(dto.state, approval_out(uow.ctx, req),
                                     {"type": dto.document.type, "id": str(dto.document.id)}), 200  # fmt: skip

            return mutate(request, f"workflow.approval.{name}", fn)

    View.__name__ = f"Approval{name.title()}View"
    return View


# --------------------------------------------------------------------------- audit
class AuditEventListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.models import AuditEvent

        ctx = context()
        policy().require(ctx, "workflow.audit_event.read")
        qs = AuditEvent.objects.filter(tenant_id=ctx.tenant_id).filter(company_id__in=[*ctx.company_ids, None])
        q = request.query_params
        if q.get("resource_type"):
            qs = qs.filter(resource_type=q["resource_type"])
        if q.get("resource_id"):
            qs = qs.filter(resource_id=parse_uuid(q["resource_id"], "resource_id"))
        if q.get("principal_id"):
            qs = qs.filter(principal_id=parse_uuid(q["principal_id"], "principal_id"))
        if q.get("channel"):
            qs = qs.filter(channel=q["channel"])
        for param, lookup in (("from", "at__gte"), ("to", "at__lt")):
            if q.get(param):
                try:
                    day = dt.date.fromisoformat(q[param])
                except ValueError:
                    raise ValidationFailed(f"{param} must be YYYY-MM-DD", code="filter_invalid") from None
                qs = qs.filter(**{lookup: clock.local_midnight_utc(day)})
        rows = list(qs.order_by("-seq")[: limit_of(request)])
        return Response(page([audit_out(ctx, e) for e in rows]))


class AuditEventDetailView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.models import AuditEvent

        ctx = context()
        policy().require(ctx, "workflow.audit_event.read")
        ev = (
            AuditEvent.objects.filter(tenant_id=ctx.tenant_id, id=id)
            .filter(company_id__in=[*ctx.company_ids, None])
            .first()
        )
        if ev is None:
            raise NotFound("audit event not found")
        return Response(to_jsonable(audit_out(ctx, ev)))


class AuditVerifyView(WorkflowView):
    def post(self, request: Request) -> Response:
        from workflow.audit.service import DbAuditService

        ctx = context()
        idem_key(request)
        policy().require(ctx, "workflow.audit_event.read")
        r = DbAuditService().verify(ctx.tenant_id)
        return Response(action_result("VERIFIED" if r.ok else "TAMPERED", {
            "ok": r.ok, "checked": r.checked, "first_bad_seq": r.first_bad_seq, "reason": r.reason,
            "head_hash": r.head_hash,
        }))  # fmt: skip


# --------------------------------------------------------------------------- scheduler
class JobListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.scheduler.service import DbSchedulerService

        ctx = context()
        policy().require(ctx, "workflow.scheduled_job.read")
        agent = request.query_params.get("created_by_agent")
        jobs = DbSchedulerService().list_jobs(ctx, created_by_agent=None if agent is None else agent == "true")
        return Response(page([job_out(j) for j in jobs[: limit_of(request)]]))

    def post(self, request: Request) -> Response:
        from workflow.scheduler.service import DbSchedulerService

        company = company_of(request, context())
        d = request.data or {}
        try:
            run_at = dt.datetime.fromisoformat(d["run_at"]) if d.get("run_at") else None
        except ValueError:
            raise ValidationFailed("run_at must be an ISO 8601 timestamp") from None

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            job = DbSchedulerService().create_in(
                uow, company, code=str(d.get("code", "")), job_type=str(d.get("job_type", "")),
                cron=str(d.get("cron") or ""), run_at=run_at, timezone=str(d.get("timezone") or "America/Bogota"),
                params=dict(d.get("params") or {}), grant_ref=str(d.get("grant_ref") or ""),
            )  # fmt: skip
            return job_out(job), 201

        return mutate(request, "workflow.scheduled_job.create", fn)


class JobDetailView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.scheduler.service import DbSchedulerService

        ctx = context()
        policy().require(ctx, "workflow.scheduled_job.read")
        return Response(to_jsonable(job_out(DbSchedulerService()._owned(ctx, id))))

    def delete(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.scheduler.service import DbSchedulerService

        ctx = context()
        idem_key(request)
        policy().require(ctx, "workflow.scheduled_job.delete")
        DbSchedulerService().cancel(ctx, id)
        return Response(status=204)


def _job_action(name: str) -> type[WorkflowView]:
    class View(WorkflowView):
        def post(self, request: Request, id: uuid.UUID) -> Response:
            from workflow.scheduler.service import DbSchedulerService

            ctx = context()
            idem_key(request)
            svc = DbSchedulerService()
            if name == "run-now":
                run = svc.run_now(ctx, id)
                return Response(action_result("ENQUEUED", {"run_id": str(run.id), "key": run.idempotency_key}))
            job = svc.pause(ctx, id) if name == "pause" else svc.resume(ctx, id)
            return Response(action_result(job.state, job_out(job)))

    View.__name__ = f"Job{name.replace('-', '').title()}View"
    return View


# --------------------------------------------------------------------------- notifications
class NotificationListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.notifications.service import DbNotificationService

        ctx = context()
        policy().require(ctx, "workflow.notification.read")
        company = company_of(request, ctx)
        svc = DbNotificationService()
        items = svc.list(ctx, company, unread_only=request.query_params.get("unread") == "1", limit=limit_of(request))
        body = page([notification_out(n) for n in items])
        body["unread_count"] = svc.badge_count(ctx, company)
        return Response(body)


class NotificationReadView(WorkflowView):
    def post(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.notifications.service import DbNotificationService

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            policy().require(uow.ctx, "workflow.notification.read")
            n = DbNotificationService().mark_read(uow, id)
            return action_result("READ", notification_out(n)), 200

        return mutate(request, "workflow.notification.mark_read", fn)


# --------------------------------------------------------------------------- webhooks
class WebhookListView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.models import WebhookSubscription

        ctx = context()
        qs = policy().scope_queryset(
            ctx, "workflow.webhook.read", WebhookSubscription.objects.filter(tenant_id=ctx.tenant_id)
        )
        items = [{"id": s.id, "url": s.url, "event_types": s.event_types, "active": s.active,
                  "service_account_id": s.service_account_id, "description": s.description} for s in qs]  # fmt: skip
        return Response(page(items))  # the signing secret is never serialized

    def post(self, request: Request) -> Response:
        from workflow.webhooks.service import WebhookService

        company = company_of(request, context())
        d = request.data or {}
        shown: dict[str, str] = {}

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            sub, signing = WebhookService().subscribe_in(
                uow, company, str(d.get("url", "")), list(d.get("event_types") or []),
                parse_uuid(d.get("service_account_id"), "service_account_id"), str(d.get("description") or ""),
            )  # fmt: skip
            shown["signing"] = signing
            # The stored (replayable) response never contains the secret.
            return {"id": sub.id, "url": sub.url, "event_types": sub.event_types}, 201

        resp = mutate(request, "workflow.webhook.create", fn)
        if "signing" in shown:
            resp.data = {**resp.data, "signing_secret_once": shown["signing"]}
        return resp


class WebhookTestView(WorkflowView):
    def post(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.webhooks.service import WebhookService

        ctx = context()
        idem_key(request)
        d = WebhookService().test(ctx, id)
        return Response(action_result(d.state, {"delivery_id": str(d.id), "status": d.last_status}))


class WebhookDeliveriesView(WorkflowView):
    def get(self, request: Request, id: uuid.UUID) -> Response:
        from workflow.models import WebhookDelivery

        ctx = context()
        policy().require(ctx, "workflow.webhook.read")
        rows = WebhookDelivery.objects.filter(
            tenant_id=ctx.tenant_id, subscription_id=id, subscription__company_id__in=list(ctx.company_ids)
        ).order_by("-created_at")[: limit_of(request)]
        return Response(page([{"id": d.id, "event_id": d.event_id, "event_type": d.event_type, "state": d.state,
                               "attempts": d.attempts, "last_status": d.last_status, "last_error": d.last_error,
                               "next_attempt_at": d.next_attempt_at} for d in rows]))  # fmt: skip


class WebhookReplayView(WorkflowView):
    def post(self, request: Request, id: uuid.UUID, delivery_id: uuid.UUID) -> Response:
        from workflow.webhooks.service import WebhookService

        ctx = context()
        idem_key(request)
        d = WebhookService().replay(ctx, delivery_id)
        return Response(action_result(d.state, {"delivery_id": str(d.id)}))


# --------------------------------------------------------------------------- KPIs (GET /api/v1/kpis/aprobaciones)
class KpiView(WorkflowView):
    def get(self, request: Request) -> Response:
        from workflow.kpis import aprobaciones_kpis

        ctx = context()
        company = company_of(request, ctx)
        return Response(to_jsonable(aprobaciones_kpis(ctx, company, request.query_params.get("period"))))


FormulaSubmitView = _formula_action("submit")
FormulaApproveView = _formula_action("approve")
FormulaRetireView = _formula_action("retire")
ApprovalApproveView = _approval_action("approve")
ApprovalRejectView = _approval_action("reject")
ApprovalDelegateView = _approval_action("delegate")
JobPauseView = _job_action("pause")
JobResumeView = _job_action("resume")
JobRunNowView = _job_action("run-now")
