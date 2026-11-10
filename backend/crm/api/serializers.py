"""JSON views of the CRM records with field security (fail closed).

Classifications follow the contract: lead/contact personal data is ``restricted-pii``, the lead name and the
opportunity party/amount are ``confidential``, activity bodies are ``confidential``. Fields the principal's
field policy cannot read are dropped; the same projection serves UI, API, exports, AI and MCP.
"""

from __future__ import annotations

from typing import Any

from crm.models import Activity, Contact, Lead, Opportunity, Pipeline
from iam.policy import fields as field_registry
from kernel.tenancy import TenantContext
from sales.api.serializers import audit_stamp, money
from sales.api.serializers import masked as _masked

_BASE = {
    "id": "public",
    "tenant_id": "internal",
    "company_id": "internal",
    "status": "internal",
    "version": "internal",
    "audit": "internal",
    "code": "internal",
}
FIELDS: dict[str, dict[str, str]] = {
    "crm.lead": {
        **_BASE,
        "name": "confidential",
        "company_name": "confidential",
        "nit": "confidential",
        "email": "restricted-pii",
        "phone": "restricted-pii",
        "source": "internal",
        "campaign": "internal",
        "owner_id": "internal",
        "score": "internal",
        "lead_status": "internal",
        "consent_ref": "internal",
        "party_id": "internal",
        "opportunity_id": "internal",
        "lost_reason": "internal",
    },
    "crm.contact": {
        **_BASE,
        "name": "restricted-pii",
        "email": "restricted-pii",
        "phone": "restricted-pii",
        "party": "confidential",
        "position": "internal",
        "consent_ref": "internal",
    },
    "crm.opportunity": {
        **_BASE,
        "name": "internal",
        "party": "confidential",
        "amount": "confidential",
        "stage": "internal",
        "close_date": "internal",
        "probability": "internal",
        "pipeline_id": "internal",
        "owner_id": "internal",
        "opportunity_status": "internal",
        "lost_reason": "internal",
        "lead_id": "internal",
        "weighted_amount": "confidential",
    },
    "crm.activity": {
        **_BASE,
        "activity_type": "internal",
        "subject": "internal",
        "due_at": "internal",
        "related": "internal",
        "body": "confidential",
        "owner_id": "internal",
        "done": "internal",
    },
    "crm.pipeline": {**_BASE, "name": "internal", "stages": "internal"},
    "crm.forecast": {"amount": "confidential", "weighted": "confidential"},
}


def masked(ctx: TenantContext, resource: str, data: dict[str, Any]) -> dict[str, Any]:
    """Field security over the CRM classifications (fail closed)."""
    return _masked(ctx, resource, data, FIELDS)


def register_fields() -> None:
    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)


def _common(obj: Any) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "tenant_id": str(obj.tenant_id),
        "company_id": str(obj.company_id),
        "version": getattr(obj, "version", 1),
        "audit": audit_stamp(obj),
    }


def _uuid(value: Any) -> str | None:
    return str(value) if value else None


def _party(party_id: Any) -> dict[str, Any] | None:
    return {"id": str(party_id), "kind": "customer"} if party_id else None


def lead_json(ctx: TenantContext, x: Lead) -> dict[str, Any]:
    return masked(
        ctx,
        "crm.lead",
        {
            **_common(x),
            "code": x.code,
            "name": x.name,
            "company_name": x.company_name,
            "nit": x.nit,
            "email": x.email,
            "phone": x.phone,
            "source": x.source,
            "campaign": x.campaign,
            "owner_id": _uuid(x.owner_id),
            "score": x.score,
            "status": "ARCHIVED" if x.status in (Lead.Status.CONVERTED, Lead.Status.LOST) else "ACTIVE",
            "lead_status": x.status,
            "consent_ref": _uuid(x.consent_ref),
            "party_id": _uuid(x.party_id),
            "opportunity_id": _uuid(x.opportunity_id),
            "lost_reason": x.lost_reason,
        },
    )


def contact_json(ctx: TenantContext, x: Contact) -> dict[str, Any]:
    return masked(
        ctx,
        "crm.contact",
        {
            **_common(x),
            "code": x.code,
            "name": x.name,
            "status": "ACTIVE",
            "email": x.email,
            "phone": x.phone,
            "party": _party(x.party_id),
            "position": x.position,
            "consent_ref": _uuid(x.consent_ref),
        },
    )


def opportunity_json(ctx: TenantContext, x: Opportunity) -> dict[str, Any]:
    from kernel.money import q_amount

    return masked(
        ctx,
        "crm.opportunity",
        {
            **_common(x),
            "code": x.code,
            "name": x.name,
            "status": "ACTIVE" if x.status == Opportunity.Status.OPEN else "ARCHIVED",
            "opportunity_status": x.status,
            "party": _party(x.party_id),
            "amount": money(x.amount, x.currency),
            "weighted_amount": money(q_amount(x.amount * x.probability / 100), x.currency),
            "stage": x.stage,
            "close_date": x.close_date.isoformat() if x.close_date else None,
            "probability": f"{x.probability:f}",
            "pipeline_id": str(x.pipeline_id),
            "owner_id": _uuid(x.owner_id),
            "lead_id": _uuid(x.lead_id),
            "lost_reason": x.lost_reason,
        },
    )


def activity_json(ctx: TenantContext, x: Activity) -> dict[str, Any]:
    return masked(
        ctx,
        "crm.activity",
        {
            **_common(x),
            "status": "DONE" if x.done else "OPEN",
            "activity_type": x.activity_type,
            "subject": x.subject,
            "due_at": x.due_at.isoformat() if x.due_at else None,
            "related": x.related,
            "body": x.body,
            "owner_id": _uuid(x.owner_id),
            "done": x.done,
        },
    )


def pipeline_json(ctx: TenantContext, x: Pipeline) -> dict[str, Any]:
    return masked(
        ctx,
        "crm.pipeline",
        {
            **_common(x),
            "code": x.code,
            "name": x.name,
            "status": "ACTIVE" if x.active else "ARCHIVED",
            "stages": x.stages,
        },
    )


SERIALIZERS: dict[type, Any] = {
    Lead: lead_json,
    Contact: contact_json,
    Opportunity: opportunity_json,
    Activity: activity_json,
    Pipeline: pipeline_json,
}


def serialize(ctx: TenantContext, obj: Any) -> Any:
    for model, fn in SERIALIZERS.items():
        if isinstance(obj, model):
            return fn(ctx, obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"no serializer for {type(obj).__name__}")
