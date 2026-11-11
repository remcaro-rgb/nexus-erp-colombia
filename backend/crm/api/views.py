"""CRM endpoints (contract ``contracts/openapi/modules/crm.yaml``).

Reuses the sales resource plumbing (deny-by-default authorization, forced-RLS read scope with the policy's
row scope — "own leads/opportunities" through ``owner_ids`` — idempotent units of work, If-Match on PATCH).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response

from crm import services as crm
from crm.api import serializers as ser
from crm.models import Activity, Contact, Lead, Opportunity, Pipeline
from kernel.errors import PermissionDenied, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.api.base import SalesView, body, command
from sales.api.serializers import _readable
from sales.api.views import Resource, _date_param
from sales.services.common import require, scoped

RESOURCES: dict[str, Resource] = {
    "leads": Resource(
        "crm.lead",
        Lead,
        ser.lead_json,
        "crm.lead.read",
        create=crm.create_lead,
        update=crm.update_lead,
        delete=crm.delete_record("crm.lead.delete"),
        actions={"convert": lambda uow, x, r, p: crm.convert_lead(uow, x, p)},
        filters={"code": "code", "lead_status": "status", "owner_id": "owner_id", "source": "source"},
    ),
    "contacts": Resource(
        "crm.contact",
        Contact,
        ser.contact_json,
        "crm.contact.read",
        create=crm.create_contact,
        update=crm.update_contact,
        delete=crm.delete_record("crm.contact.delete"),
        filters={"code": "code", "party": "party_id"},
    ),
    "opportunities": Resource(
        "crm.opportunity",
        Opportunity,
        ser.opportunity_json,
        "crm.opportunity.read",
        create=crm.create_opportunity,
        update=crm.update_opportunity,
        delete=crm.delete_record("crm.opportunity.delete"),
        actions={
            "win": lambda uow, x, r, p: crm.close_opportunity(uow, x, True, r),
            "lose": lambda uow, x, r, p: crm.close_opportunity(uow, x, False, r),
        },
        filters={
            "stage": "stage",
            "pipeline_id": "pipeline_id",
            "owner_id": "owner_id",
            "opportunity_status": "status",
            "party": "party_id",
        },
    ),
    "activities": Resource(
        "crm.activity",
        Activity,
        ser.activity_json,
        "crm.activity.read",
        create=crm.create_activity,
        filters={"activity_type": "activity_type", "owner_id": "owner_id", "done": "done"},
    ),
    "pipelines": Resource(
        "crm.pipeline",
        Pipeline,
        ser.pipeline_json,
        "crm.pipeline.read",
        create=crm.create_pipeline,
        update=crm.update_pipeline,
        delete=crm.delete_record("crm.pipeline.delete"),
        filters={"code": "code"},
    ),
}

ACTIONS: dict[str, tuple[str, ...]] = {"leads": ("convert",), "opportunities": ("win", "lose")}


class CrmResources:
    """Mixin for ``sales.api.views.views_for``: CRM registry and serializer."""

    registry: ClassVar[Mapping[str, Resource]] = RESOURCES
    created_actions: ClassVar[frozenset[str]] = frozenset()

    def serialize_result(self, ctx: TenantContext, obj: Any) -> Any:
        return ser.serialize(ctx, obj)


class BulkLeadsView(SalesView):
    """``POST /leads:bulk`` (≤ 1,000 items): all-or-nothing creation."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        data = body(request)
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValidationFailed("items must be a list of 1–1000 leads", field="items")

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            created = [crm.create_lead(uow, company, x) for x in items]
            return {"status": "OK", "data": {"created": [str(x.id) for x in created], "count": len(created)}}, 200

        return command(request, ctx, "crm.lead.bulk", run)


class ForecastView(SalesView):
    """``GET /forecast`` → ``ReportResult``; amounts are confidential (field grant required, fail closed)."""

    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "crm.forecast.read")
        if not {"amount", "weighted"} <= _readable(ctx, "crm.forecast"):
            raise PermissionDenied("forecast amounts are confidential for this principal", code="field_forbidden")
        qs = scoped(ctx, "crm.forecast.read", Opportunity.objects.filter(company_id=company))
        params: dict[str, Any] = {}
        for name in ("owner_id", "pipeline_id"):
            raw = request.query_params.get(name)
            if raw:
                try:
                    qs = qs.filter(**{name: uuid.UUID(raw)})
                except ValueError as exc:
                    raise ValidationFailed(f"{name} must be a UUID", field=name) from exc
                params[name] = raw
        period_from = _date_param(request, "period_from") if request.query_params.get("period_from") else None
        period_to = _date_param(request, "period_to") if request.query_params.get("period_to") else None
        data = crm.forecast(ctx, qs, period_from, period_to)
        params.update({"period_from": data["period_from"], "period_to": data["period_to"]})
        return Response(
            {
                "report": "crm.forecast",
                "parameters": params,
                "columns": [
                    {"key": "stage", "label": "Etapa", "type": "string"},
                    {"key": "currency", "label": "Moneda", "type": "string"},
                    {"key": "count", "label": "Oportunidades", "type": "integer"},
                    {"key": "amount", "label": "Valor", "type": "decimal"},
                    {"key": "weighted", "label": "Valor ponderado", "type": "decimal"},
                ],
                "rows": data["by_stage"],
                "totals": {"by_currency": data["totals"], "by_owner": data["by_owner"]},
                "warnings": [],
                "synthetic": False,
            }
        )
