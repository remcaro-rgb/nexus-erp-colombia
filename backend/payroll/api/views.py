"""Payroll API views (contract ``contracts/openapi/modules/payroll.yaml``, frozen by P02).

Only documented operations are routed. Authorization happens in the service functions
(``payroll.<resource>.<action>`` through the PolicyService).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from kernel.errors import NotFound, ValidationFailed
from kernel.uow import UnitOfWork, run_in_unit_of_work
from payroll import payslips, runs
from payroll.api.base import PayrollAPIView, body, command, company_id, etag, page
from payroll.concepts import service as concepts
from payroll.models import ElectronicPayrollDocument, PayrollConcept, PayrollNovelty, PayrollPeriod, PayrollRun


def _action_result(resource_type: str, obj: Any, data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "resource": {"type": resource_type, "id": str(obj.id)}, "data": data}


# --------------------------------------------------------------------------- periods


class PeriodList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.period.read", {"company_id": cid})
        qs = PayrollPeriod.objects.filter(tenant_id=ctx.tenant_id, company_id=cid)
        return page(request, qs, runs.period_json)

    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            p = runs.create_period(uow, cid, body(request))
            return runs.period_json(p), 201, {**etag(p), "Location": f"/api/v1/payroll/periods/{p.id}"}

        return command(request, ctx, "payroll.period.create", fn)


class PeriodDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.period.read", {"company_id": cid})
        p = runs.get_period(ctx, cid, pk)
        return Response(runs.period_json(p), headers=etag(p))


# --------------------------------------------------------------------------- concepts


class ConceptList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.concept.read", {"company_id": cid})
        qs = PayrollConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=cid)
        return page(request, qs, concepts.to_json)

    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            c = concepts.create(uow, cid, body(request))
            return concepts.to_json(c), 201, {**etag(c), "Location": f"/api/v1/payroll/concepts/{c.id}"}

        return command(request, ctx, "payroll.concept.create", fn)


class ConceptDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.concept.read", {"company_id": cid})
        c = concepts.get(ctx, cid, pk)
        return Response(concepts.to_json(c), headers=etag(c))

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        if_match = request.headers.get("If-Match")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            c = concepts.update(uow, cid, pk, body(request), if_match)
            return concepts.to_json(c), 200, etag(c)

        return command(request, ctx, f"payroll.concept.update:{pk}", fn)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        if_match = request.headers.get("If-Match")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            concepts.delete(uow, cid, pk, if_match)
            return None, 204, {}

        return command(request, ctx, f"payroll.concept.delete:{pk}", fn)


class ConceptValidateGraph(PayrollAPIView):
    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        data = body(request)
        params = data.get("parameters") or {}
        on: dt.date | None = None
        raw = params.get("on") or data.get("date")
        if raw:
            try:
                on = dt.date.fromisoformat(str(raw))
            except ValueError as exc:
                raise ValidationFailed("date must be YYYY-MM-DD", field="date") from exc

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            return concepts.validate_graph(uow.ctx, cid, on), 200, {}

        return command(request, ctx, "payroll.concept.validate_graph", fn)


# --------------------------------------------------------------------------- novelties


class NoveltyList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.novelty.read", {"company_id": cid})
        qs = PayrollNovelty.objects.filter(tenant_id=ctx.tenant_id, company_id=cid)
        if request.query_params.get("period_id"):
            qs = qs.filter(period_id=request.query_params["period_id"])
        return page(request, qs, runs.novelty_json)

    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            n = runs.create_novelty(uow, cid, body(request))
            return runs.novelty_json(n), 201, {**etag(n), "Location": f"/api/v1/payroll/novelties/{n.id}"}

        return command(request, ctx, "payroll.novelty.create", fn)


class NoveltyDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.novelty.read", {"company_id": cid})
        n = PayrollNovelty.objects.filter(tenant_id=ctx.tenant_id, company_id=cid, id=pk).first()
        if n is None:
            raise NotFound("novelty not found")
        return Response(runs.novelty_json(n), headers=etag(n))


class NoveltyBulk(PayrollAPIView):
    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        items = body(request).get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValidationFailed("items must be a list of 1..1000 novelties", field="items")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            created = [runs.create_novelty(uow, cid, item) for item in items]
            return {"status": "ok", "data": {"created": len(created), "ids": [str(n.id) for n in created]}}, 200, {}

        return command(request, ctx, "payroll.novelty.bulk", fn)


# --------------------------------------------------------------------------- runs


class RunList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.run.read", {"company_id": cid})
        qs = PayrollRun.objects.filter(tenant_id=ctx.tenant_id, company_id=cid)
        return page(request, qs, runs.run_json)

    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            r = runs.create_run(uow, cid, body(request))
            return runs.run_json(r), 201, {**etag(r), "Location": f"/api/v1/payroll/runs/{r.id}"}

        return command(request, ctx, "payroll.run.create", fn)


class RunDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.run.read", {"company_id": cid})
        r = runs.get_run(ctx, cid, pk)
        return Response(runs.run_json(r), headers=etag(r))


def _run_action(name: str, fn_name: str) -> type[PayrollAPIView]:
    service_fn = getattr(runs, fn_name)

    class RunAction(PayrollAPIView):
        def post(self, request: Request, pk: uuid.UUID) -> Response:
            ctx, cid = self.ctx, company_id(request, self.ctx)
            data = body(request)

            def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
                if fn_name == "pay":
                    ref = str((data.get("parameters") or {}).get("payment_ref") or data.get("reason") or "")
                    r = service_fn(uow, cid, pk, ref)
                else:
                    r = service_fn(uow, cid, pk)
                return _action_result("payroll.run", r, runs.run_json(r)), 200, etag(r)

            return command(request, ctx, f"payroll.run.{name}:{pk}", fn)

    RunAction.__name__ = f"Run{name.title()}"
    return RunAction


RunCalculate = _run_action("calculate", "calculate")
RunApprove = _run_action("approve", "approve")
RunPost = _run_action("post", "post")
RunPay = _run_action("pay", "pay")


class RunSimulate(PayrollAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        # nothing is persisted: no idempotency record, the unit of work only binds the context (RLS)
        result = run_in_unit_of_work(lambda uow: runs.simulate(uow, cid, pk), ctx)
        return Response(result)


# --------------------------------------------------------------------------- payslips


class PayslipList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        qs = payslips.payslips_for(ctx, cid)
        if request.query_params.get("run_id"):
            qs = qs.filter(run_id=request.query_params["run_id"])
        fields = payslips.readable(ctx)
        return page(request, qs, lambda s: payslips.payslip_json(ctx, s, fields=fields))


class PayslipDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        slip = payslips.payslips_for(ctx, cid).filter(id=pk).first()
        if slip is None:
            raise NotFound("payslip not found")
        return Response(payslips.payslip_json(ctx, slip))


# --------------------------------------------------------------------------- electronic payroll


class ElectronicList(PayrollAPIView):
    def get(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.electronic_payroll.read", {"company_id": cid})
        qs = ElectronicPayrollDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=cid)
        return page(request, qs, payslips.electronic_json)

    def post(self, request: Request) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            d = payslips.create_electronic(uow, cid, body(request))
            return payslips.electronic_json(d), 201, {**etag(d), "Location": f"/api/v1/payroll/electronic-payroll/{d.id}"}

        return command(request, ctx, "payroll.electronic_payroll.create", fn)


class ElectronicDetail(PayrollAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        concepts.policy().require(ctx, "payroll.electronic_payroll.read", {"company_id": cid})
        d = ElectronicPayrollDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=cid, id=pk).first()
        if d is None:
            raise NotFound("electronic payroll document not found")
        return Response(payslips.electronic_json(d), headers=etag(d))


class ElectronicGenerate(PayrollAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            d = payslips.generate(uow, cid, pk)
            return _action_result("payroll.electronic_payroll", d, payslips.electronic_json(d)), 200, etag(d)

        return command(request, ctx, f"payroll.electronic_payroll.generate:{pk}", fn)


class ElectronicAdjust(PayrollAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, cid = self.ctx, company_id(request, self.ctx)
        kind = str((body(request).get("parameters") or {}).get("kind") or "Reemplazar")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            d = payslips.adjust(uow, cid, pk, kind)
            return _action_result("payroll.electronic_payroll", d, payslips.electronic_json(d)), 200, etag(d)

        return command(request, ctx, f"payroll.electronic_payroll.adjust:{pk}", fn)
