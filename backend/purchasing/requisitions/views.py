"""F-INV-REQ endpoints (contract ``contracts/openapi/modules/purchasing.yaml``; part P11b).

Each handler authorizes with the operation's ``x-permission`` (the services call ``PolicyService``), reads in the
request's forced-RLS scope and writes in one idempotent unit of work (P11a's ``purchasing.api.base`` plumbing:
``Idempotency-Key``, ``If-Match``, ETags, keyset paging, 404 for foreign ids).

Row scope: a principal holding ``purchasing.internal_requisition.read`` sees the company's requisitions as P04's
row policies allow (an ``owner`` scope restricts requesters to their own: ``IAM_SCOPE_FIELDS``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from kernel import clock, ids
from kernel.errors import NotFound, ValidationFailed
from kernel.uow import UnitOfWork
from purchasing.api.base import PurchasingView, action_body, body, check_if_match, command, page
from purchasing.requisitions import purchase, service
from purchasing.requisitions import serializers as ser
from purchasing.requisitions.common import get_scoped, scoped
from purchasing.requisitions.models import ConsolidationBatch, InternalRequisition, PurchaseRequisition


def _etag(model: Any) -> Callable[[Any], str | None]:
    def tag(data: Any) -> str | None:
        if isinstance(data, dict) and "id" in data and "version" in data:
            return str(model(pk=uuid.UUID(data["id"]), version=data["version"]).etag)
        return None

    return tag


def _flag(request: Request, name: str, default: bool) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    if raw.lower() in ("true", "1"):
        return True
    if raw.lower() in ("false", "0"):
        return False
    raise ValidationFailed(f"{name} must be true or false", field=name)


def _warnings(codes: list[str]) -> list[dict[str, str]]:
    return [{"code": code, "message": "D-011: se creó una tarea de comprador en lugar del borrador"} for code in codes]


# --------------------------------------------------------------------------- internal requisitions

IR_FILTERS = {"status": "status", "number": "number", "priority": "priority", "requester_id": "requester_id"}


class InternalRequisitionList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.internal_requisition.read", InternalRequisition.objects.filter(company_id=company))
        return page(request, qs, ser.requisition_json, IR_FILTERS)

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return ser.requisition_json(service.create(uow, company, body(request))), 201

        return command(request, ctx, "purchasing.internal_requisition.create", fn, etag=_etag(InternalRequisition))


class InternalRequisitionDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.internal_requisition.read", InternalRequisition.objects.filter(company_id=company), pk
        )
        response = Response(ser.requisition_json(obj))
        response["ETag"] = obj.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.internal_requisition.update", InternalRequisition.objects.filter(company_id=company), pk
        )
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            "purchasing.internal_requisition.update",
            lambda uow: (ser.requisition_json(service.update(uow, obj, body(request))), 200),
            etag=_etag(InternalRequisition),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.internal_requisition.delete", InternalRequisition.objects.filter(company_id=company), pk
        )
        check_if_match(request, obj, required=True)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            service.delete(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.internal_requisition.delete", fn)


class InternalRequisitionAction(PurchasingView):
    PERMISSIONS = {
        "submit": "purchasing.internal_requisition.update",
        "approve": "purchasing.internal_requisition.approve",
        "reject": "purchasing.internal_requisition.approve",
        "cancel": "purchasing.internal_requisition.void",
        "source": "purchasing.internal_requisition.post",
    }

    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        permission = self.PERMISSIONS.get(action)
        if permission is None:
            raise NotFound("not found")
        obj = get_scoped(ctx, permission, InternalRequisition.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)
        mode = request.query_params.get("mode") or str(params.get("mode") or "simulate")

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            target = InternalRequisition.objects.select_for_update().get(pk=obj.pk)
            if action == "source":
                result = service.source(uow, target, mode)
                target.refresh_from_db()
                return {
                    "status": result["status"],
                    "resource": {"type": service.DOC_TYPE, "id": str(target.id), "number": target.number},
                    "data": result,
                    "warnings": [],
                }, 200
            extra: dict[str, Any] | None = None
            if action == "submit":
                service.submit(uow, target)
            elif action == "approve":
                extra = {"sourcing": service.approve(uow, target, reason)}
            elif action == "reject":
                service.reject(uow, target, reason)
            else:
                service.cancel(uow, target, reason)
            target.refresh_from_db()
            return ser.requisition_json(target, extra), 200

        etag = None if action == "source" else _etag(InternalRequisition)
        return command(request, ctx, f"purchasing.internal_requisition.{action}", fn, etag=etag)


# --------------------------------------------------------------------------- consolidations


class ConsolidationList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.consolidation.read", ConsolidationBatch.objects.filter(company_id=company))
        return page(request, qs, ser.consolidation_json, {"status": "status", "number": "number"})


class ConsolidationDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.consolidation.read", ConsolidationBatch.objects.filter(company_id=company), pk
        )
        response = Response(ser.consolidation_json(obj))
        response["ETag"] = obj.etag
        return response


class ConsolidationRun(PurchasingView):
    """``POST consolidations:run?dry_run=true|false`` → ``Operation`` (runs synchronously, SUCCEEDED)."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        action_body(request)
        dry_run = _flag(request, "dry_run", True)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            started = clock.now()
            result = service.run_consolidation(uow, company, dry_run=dry_run)
            batch = result["batch"]
            links = [{"rel": "consolidation", "href": f"/api/v1/purchasing/consolidations/{batch.id}"}] if batch else []
            return {
                "id": str(batch.id if batch else ids.new_id()),
                "kind": "purchasing_consolidations_run",
                "status": "SUCCEEDED",
                "progress": "100",
                "started_at": started.isoformat(),
                "finished_at": clock.now().isoformat(),
                "result_links": links,
                "error": None,
                # CCR P11b-2: the preview (dry run) / result with lineage and the D-011 action per line
                "dry_run": dry_run,
                "lines": result["lines"],
                "warnings": _warnings(result["warnings"]),
            }, 202

        return command(request, ctx, "purchasing.consolidation.run", fn)


# --------------------------------------------------------------------------- purchase requisitions

PR_FILTERS = {"status": "status", "number": "number"}


class PurchaseRequisitionList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.requisition.read", PurchaseRequisition.objects.filter(company_id=company))
        return page(request, qs, ser.purchase_requisition_json, PR_FILTERS)

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return ser.purchase_requisition_json(purchase.create(uow, company, body(request))), 201

        return command(request, ctx, "purchasing.requisition.create", fn, etag=_etag(PurchaseRequisition))


class PurchaseRequisitionDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.requisition.read", PurchaseRequisition.objects.filter(company_id=company), pk)
        response = Response(ser.purchase_requisition_json(obj))
        response["ETag"] = obj.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.requisition.update", PurchaseRequisition.objects.filter(company_id=company), pk
        )
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            "purchasing.requisition.update",
            lambda uow: (ser.purchase_requisition_json(purchase.update(uow, obj, body(request))), 200),
            etag=_etag(PurchaseRequisition),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, "purchasing.requisition.delete", PurchaseRequisition.objects.filter(company_id=company), pk
        )
        check_if_match(request, obj, required=True)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            purchase.delete(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.requisition.delete", fn)


class PurchaseRequisitionAction(PurchasingView):
    PERMISSIONS = {
        "submit": "purchasing.requisition.update",
        "approve": "purchasing.requisition.approve",
        "reject": "purchasing.requisition.approve",
        "to-rfq": "purchasing.requisition.update",
        "to-po": "purchasing.requisition.update",
    }

    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        permission = self.PERMISSIONS.get(action)
        if permission is None:
            raise NotFound("not found")
        obj = get_scoped(ctx, permission, PurchaseRequisition.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            target = PurchaseRequisition.objects.select_for_update().get(pk=obj.pk)
            if action == "submit":
                purchase.submit(uow, target)
            elif action in ("approve", "reject"):
                purchase.decide(uow, target, action == "approve", reason)
            elif action == "to-rfq":
                purchase.to_rfq(uow, target, params)
            else:
                purchase.to_po(uow, target, params)
            target.refresh_from_db()
            return ser.purchase_requisition_json(target), 200

        return command(request, ctx, f"purchasing.requisition.{action}", fn, etag=_etag(PurchaseRequisition))
