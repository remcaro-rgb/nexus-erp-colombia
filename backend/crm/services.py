"""CRM services (P10a): leads (+convert), contacts, opportunities (+win/lose), activities, pipelines, forecast.

Every call authorizes through ``PolicyService`` (deny by default). Lead conversion creates (or reuses by NIT)
the customer party through ``sales.party.service.PartyService`` in the same unit of work and requires a
consent record reference (T-P10-10). Activity reminders go through P07's ``SchedulerService`` when registered.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet

from crm.models import Activity, Contact, Lead, Opportunity, Pipeline
from kernel import clock
from kernel.errors import Conflict, ValidationFailed
from kernel.money import q_amount, q_pct
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import InvalidStateTransition
from sales.party.service import PartyService
from sales.services.common import (
    audit,
    currency_in,
    date_in,
    datetime_in,
    decimal_in,
    lock,
    require,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    value = str(raw).strip().lower()
    if not EMAIL_RE.match(value) or len(value) > 254:
        raise ValidationFailed("email must be an e-mail address", field="email")
    return value


def _phone(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not 7 <= len(digits) <= 15:
        raise ValidationFailed("phone must have 7–15 digits", field="phone")
    return digits


def _code(ctx: TenantContext, company_id: uuid.UUID, model: Any, raw: Any, prefix: str) -> str:
    code = text_in(raw, "code", 40).strip()
    if not code:
        n = model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).count() + 1
        code = f"{prefix}-{n:06d}"
        while model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).exists():
            n += 1
            code = f"{prefix}-{n:06d}"
    elif model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).exists():
        raise Conflict(f"code {code} already exists")
    return code


def _stamp(uow: UnitOfWork) -> dict[str, Any]:
    return {"tenant_id": uow.ctx.tenant_id, "created_by": uow.ctx.principal_id, "updated_by": uow.ctx.principal_id}


def _touch(uow: UnitOfWork, obj: Any) -> None:
    obj.version += 1
    obj.updated_by = uow.ctx.principal_id
    obj.updated_at = clock.now()
    obj.save()


# --------------------------------------------------------------------------- pipelines

PIPELINE_FIELDS = ("code", "name", "stages", "active")


def _stages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw or len(raw) > 30:
        raise ValidationFailed("stages must be a list of 1–30 {code, name, probability}", field="stages")
    out, seen = [], set()
    for i, s in enumerate(raw):
        if not isinstance(s, Mapping):
            raise ValidationFailed(f"stages[{i}] must be an object", field="stages")
        unknown_keys(s, ("code", "name", "probability"), f" in stages[{i}]")
        code = text_in(s.get("code"), "code", 40).strip().upper()
        if not code or code in seen:
            raise ValidationFailed(f"stages[{i}].code must be unique and not empty", field="stages")
        seen.add(code)
        prob = decimal_in(s.get("probability", "0"), "probability", minimum=Decimal(0), maximum=Decimal(100))
        out.append(
            {"code": code, "name": text_in(s.get("name") or code, "name", 120), "probability": f"{q_pct(prob):f}"}
        )
    return out


def create_pipeline(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Pipeline:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "crm.pipeline.create", {"company_id": company_id})
    unknown_keys(data, PIPELINE_FIELDS)
    row = Pipeline.objects.create(
        company_id=company_id,
        code=_code(ctx, company_id, Pipeline, data.get("code"), "EMB"),
        name=text_in(data.get("name") or "Embudo", "name", 200),
        stages=_stages(data.get("stages")),
        active=bool(data.get("active", True)),
        **_stamp(uow),
    )
    audit(uow, "crm.pipeline.create", "crm.pipeline", row.id, company_id, {"code": row.code})
    return row


def update_pipeline(uow: UnitOfWork, row: Pipeline, data: Mapping[str, Any]) -> Pipeline:
    require(uow.ctx, "crm.pipeline.update", row)
    unknown_keys(data, PIPELINE_FIELDS)
    row = lock(Pipeline.objects.filter(tenant_id=uow.ctx.tenant_id), row.id)
    if "stages" in data:
        stages = _stages(data["stages"])
        used = set(
            Opportunity.objects.filter(pipeline=row, status=Opportunity.Status.OPEN).values_list("stage", flat=True)
        )
        missing = used - {s["code"] for s in stages}
        if missing:
            raise ValidationFailed(f"stages in use cannot be removed: {', '.join(sorted(missing))}", field="stages")
        row.stages = stages
    if "name" in data:
        row.name = text_in(data["name"], "name", 200)
    if "active" in data:
        row.active = bool(data["active"])
    _touch(uow, row)
    return row


# --------------------------------------------------------------------------- leads

LEAD_FIELDS = (
    "code",
    "name",
    "company_name",
    "nit",
    "email",
    "phone",
    "source",
    "campaign",
    "owner_id",
    "score",
    "consent_ref",
    "lead_status",
    "lost_reason",
)


def _lead_values(data: Mapping[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in data or not partial:
        name = text_in(data.get("name"), "name", 200).strip()
        if not name:
            raise ValidationFailed("name is required", field="name")
        out["name"] = name
    for key, size in (("company_name", 250), ("source", 60), ("campaign", 120), ("lost_reason", 2000)):
        if key in data:
            out[key] = text_in(data[key], key, size)
    if "nit" in data:
        out["nit"] = re.sub(r"\D", "", str(data["nit"] or ""))[:20]
    if "email" in data:
        out["email"] = _email(data["email"])
    if "phone" in data:
        out["phone"] = _phone(data["phone"])
    for key in ("owner_id", "consent_ref"):
        if key in data:
            out[key] = uuid_in(data[key], key, required=False)
    if "score" in data:
        score = data["score"]
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            raise ValidationFailed("score must be an integer 0–100", field="score")
        out["score"] = score
    if "lead_status" in data:
        if data["lead_status"] not in (Lead.Status.NEW, Lead.Status.QUALIFIED, Lead.Status.LOST):
            raise ValidationFailed(
                "lead_status may be NEW, QUALIFIED or LOST (CONVERTED only by :convert)", field="lead_status"
            )
        out["status"] = data["lead_status"]
    return out


def create_lead(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Lead:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "crm.lead.create", {"company_id": company_id})
    unknown_keys(data, LEAD_FIELDS)
    values = _lead_values(data, partial=False)
    values.setdefault("owner_id", ctx.on_behalf_of or ctx.principal_id)
    lead = Lead.objects.create(
        company_id=company_id, code=_code(ctx, company_id, Lead, data.get("code"), "PRO"), **values, **_stamp(uow)
    )
    audit(uow, "crm.lead.create", "crm.lead", lead.id, company_id, {"code": lead.code})
    return lead


def update_lead(uow: UnitOfWork, lead: Lead, data: Mapping[str, Any]) -> Lead:
    require(uow.ctx, "crm.lead.update", lead)
    unknown_keys(data, LEAD_FIELDS)
    lead = lock(Lead.objects.filter(tenant_id=uow.ctx.tenant_id), lead.id)
    if lead.status == Lead.Status.CONVERTED:
        raise InvalidStateTransition("a converted lead cannot be changed", status_now=lead.status)
    if "code" in data:
        raise ValidationFailed("code cannot be changed", field="code")
    for k, v in _lead_values(data, partial=True).items():
        setattr(lead, k, v)
    _touch(uow, lead)
    return lead


def convert_lead(uow: UnitOfWork, lead: Lead, params: Mapping[str, Any]) -> Lead:
    """Lead → customer party (with consent) [+ opportunity]; emits ``crm.lead.converted.v1``."""
    ctx = uow.ctx
    require(ctx, "crm.lead.update", lead)
    lead = lock(Lead.objects.filter(tenant_id=ctx.tenant_id), lead.id)
    if lead.status in (Lead.Status.CONVERTED, Lead.Status.LOST):
        raise InvalidStateTransition(f"a {lead.status} lead cannot be converted", status_now=lead.status)
    unknown_keys(params, ("nit", "dv", "person_type", "legal_name", "consent_ref", "opportunity", "party_id"))
    consent = uuid_in(params.get("consent_ref"), "consent_ref", required=False) or lead.consent_ref
    party_id = uuid_in(params.get("party_id"), "party_id", required=False)
    if party_id is None:
        if consent is None:
            raise ValidationFailed(
                "converting a lead into a party needs the data-processing consent record", field="consent_ref"
            )
        nit = params.get("nit") or lead.nit
        if not nit:
            raise ValidationFailed("nit is required to create the customer", field="nit")
        party = PartyService().ensure(
            uow,
            lead.company_id,
            {
                "legal_name": params.get("legal_name") or lead.company_name or lead.name,
                "nit": nit,
                "dv": params.get("dv"),
                "person_type": params.get("person_type") or ("JURIDICA" if lead.company_name else "NATURAL"),
                "email": lead.email,
                "phone": lead.phone,
                "consent_ref": str(consent),
                "salesperson_id": str(lead.owner_id) if lead.owner_id else None,
            },
            "customer",
        )
        party_id = party.id
    else:
        PartyService().get(ctx, lead.company_id, party_id)
    opp: Opportunity | None = None
    raw_opp = params.get("opportunity")
    if isinstance(raw_opp, Mapping):
        opp = create_opportunity(uow, lead.company_id, {**raw_opp, "party": str(party_id)}, lead=lead)
    lead.status, lead.party_id, lead.consent_ref = Lead.Status.CONVERTED, party_id, consent
    lead.opportunity_id = opp.id if opp else None
    _touch(uow, lead)
    uow.emit(
        "crm.lead.converted.v1",
        {"lead_id": str(lead.id), "party_id": str(party_id), **({"opportunity_id": str(opp.id)} if opp else {})},
        company_id=lead.company_id,
    )
    audit(uow, "crm.lead.convert", "crm.lead", lead.id, lead.company_id, {"party": str(party_id)})
    return lead


# --------------------------------------------------------------------------- contacts

CONTACT_FIELDS = ("code", "name", "email", "phone", "party", "position", "consent_ref")


def _party_ref(raw: Any) -> uuid.UUID | None:
    if isinstance(raw, Mapping):
        raw = raw.get("id")
    return uuid_in(raw, "party", required=False)


def create_contact(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Contact:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "crm.contact.create", {"company_id": company_id})
    unknown_keys(data, CONTACT_FIELDS)
    name = text_in(data.get("name"), "name", 200).strip()
    if not name:
        raise ValidationFailed("name is required", field="name")
    party_id = _party_ref(data.get("party"))
    if party_id is not None:
        PartyService().get(ctx, company_id, party_id)
    row = Contact.objects.create(
        company_id=company_id,
        code=_code(ctx, company_id, Contact, data.get("code"), "CON"),
        name=name,
        email=_email(data.get("email")),
        phone=_phone(data.get("phone")),
        party_id=party_id,
        position=text_in(data.get("position"), "position", 120) or None,
        consent_ref=uuid_in(data.get("consent_ref"), "consent_ref", required=False),
        **_stamp(uow),
    )
    audit(uow, "crm.contact.create", "crm.contact", row.id, company_id, {"code": row.code})
    return row


def update_contact(uow: UnitOfWork, row: Contact, data: Mapping[str, Any]) -> Contact:
    require(uow.ctx, "crm.contact.update", row)
    unknown_keys(data, CONTACT_FIELDS)
    row = lock(Contact.objects.filter(tenant_id=uow.ctx.tenant_id), row.id)
    if "name" in data:
        row.name = text_in(data["name"], "name", 200)
    if "email" in data:
        row.email = _email(data["email"])
    if "phone" in data:
        row.phone = _phone(data["phone"])
    if "position" in data:
        row.position = text_in(data["position"], "position", 120) or None
    if "party" in data:
        row.party_id = _party_ref(data["party"])
        if row.party_id is not None:
            PartyService().get(uow.ctx, row.company_id, row.party_id)
    _touch(uow, row)
    return row


# --------------------------------------------------------------------------- opportunities

OPP_FIELDS = (
    "code",
    "name",
    "party",
    "amount",
    "currency",
    "stage",
    "close_date",
    "probability",
    "pipeline_id",
    "owner_id",
    "products",
)


def _stage_probability(pipeline: Pipeline, stage: str) -> Decimal:
    for s in pipeline.stages:
        if s["code"] == stage:
            return Decimal(s["probability"])
    raise ValidationFailed(f"stage {stage} is not a stage of pipeline {pipeline.code}", field="stage")


def _money(raw: Any, currency: str) -> Decimal:
    if isinstance(raw, Mapping):
        if raw.get("currency") not in (None, currency):
            raise ValidationFailed(f"amount.currency must be {currency}", field="amount")
        raw = raw.get("amount")
    return q_amount(decimal_in(raw if raw not in (None, "") else "0", "amount", minimum=Decimal(0)))


def create_opportunity(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, lead: Lead | None = None
) -> Opportunity:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "crm.opportunity.create", {"company_id": company_id})
    unknown_keys(data, OPP_FIELDS)
    pipeline = Pipeline.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, id=uuid_req(data.get("pipeline_id"), "pipeline_id"), active=True
    ).first()
    if pipeline is None:
        raise ValidationFailed("pipeline_id is not an active pipeline of the company", field="pipeline_id")
    stage = str(data.get("stage") or pipeline.stages[0]["code"]).upper()
    default_prob = _stage_probability(pipeline, stage)
    currency = currency_in(data.get("currency"))
    party_id = _party_ref(data.get("party"))
    if party_id is not None:
        PartyService().get(ctx, company_id, party_id)
    name = text_in(data.get("name"), "name", 200).strip()
    if not name:
        raise ValidationFailed("name is required", field="name")
    row = Opportunity.objects.create(
        company_id=company_id,
        code=_code(ctx, company_id, Opportunity, data.get("code"), "OPO"),
        name=name,
        party_id=party_id,
        lead=lead,
        pipeline=pipeline,
        stage=stage,
        amount=_money(data.get("amount"), currency),
        currency=currency,
        probability=decimal_in(data["probability"], "probability", minimum=Decimal(0), maximum=Decimal(100))
        if data.get("probability") not in (None, "")
        else default_prob,
        close_date=date_in(data["close_date"], "close_date") if data.get("close_date") else None,
        owner_id=uuid_in(data.get("owner_id"), "owner_id", required=False)
        or (lead.owner_id if lead else None)
        or ctx.on_behalf_of
        or ctx.principal_id,
        products=list(data.get("products") or []),
        **_stamp(uow),
    )
    audit(uow, "crm.opportunity.create", "crm.opportunity", row.id, company_id, {"code": row.code})
    return row


def update_opportunity(uow: UnitOfWork, row: Opportunity, data: Mapping[str, Any]) -> Opportunity:
    require(uow.ctx, "crm.opportunity.update", row)
    unknown_keys(data, OPP_FIELDS)
    row = lock(Opportunity.objects.select_related("pipeline").filter(tenant_id=uow.ctx.tenant_id), row.id)
    if row.status != Opportunity.Status.OPEN:
        raise InvalidStateTransition(f"a {row.status} opportunity cannot be changed", status_now=row.status)
    if "stage" in data:
        row.stage = str(data["stage"]).upper()
        row.probability = _stage_probability(row.pipeline, row.stage)
    if data.get("probability") not in (None, ""):
        row.probability = decimal_in(data["probability"], "probability", minimum=Decimal(0), maximum=Decimal(100))
    if "amount" in data:
        row.amount = _money(data["amount"], row.currency)
    if "name" in data:
        row.name = text_in(data["name"], "name", 200)
    if "close_date" in data:
        row.close_date = date_in(data["close_date"], "close_date") if data["close_date"] else None
    if "owner_id" in data:
        row.owner_id = uuid_in(data["owner_id"], "owner_id", required=False)
    if "party" in data:
        row.party_id = _party_ref(data["party"])
    if "products" in data:
        row.products = list(data["products"] or [])
    _touch(uow, row)
    return row


def close_opportunity(uow: UnitOfWork, row: Opportunity, won: bool, reason: str) -> Opportunity:
    require(uow.ctx, "crm.opportunity.update", row)
    row = lock(Opportunity.objects.filter(tenant_id=uow.ctx.tenant_id), row.id)
    if row.status != Opportunity.Status.OPEN:
        raise InvalidStateTransition(f"a {row.status} opportunity cannot be closed again", status_now=row.status)
    if not won and not reason.strip():
        raise ValidationFailed("losing an opportunity needs a reason", field="reason", code="reason_required")
    row.status = Opportunity.Status.WON if won else Opportunity.Status.LOST
    row.probability = Decimal(100) if won else Decimal(0)
    row.lost_reason = "" if won else reason
    row.closed_on = clock.business_date()
    _touch(uow, row)
    if won:
        uow.emit(
            "crm.opportunity.won.v1",
            {"opportunity_id": str(row.id), "amount": {"amount": f"{row.amount:f}", "currency": row.currency}},
            company_id=row.company_id,
        )
    audit(
        uow,
        f"crm.opportunity.{'win' if won else 'lose'}",
        "crm.opportunity",
        row.id,
        row.company_id,
        {"status": row.status},
        reason,
    )
    return row


# --------------------------------------------------------------------------- activities

ACTIVITY_FIELDS = ("activity_type", "subject", "due_at", "related", "body", "owner_id", "done")


def create_activity(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Activity:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "crm.activity.create", {"company_id": company_id})
    unknown_keys(data, ACTIVITY_FIELDS)
    kind = data.get("activity_type")
    if kind not in Activity.Type.values:
        raise ValidationFailed("activity_type must be CALL, MEETING, EMAIL, TASK or NOTE", field="activity_type")
    subject = text_in(data.get("subject"), "subject", 250).strip()
    if not subject:
        raise ValidationFailed("subject is required", field="subject")
    related = data.get("related")
    if related is not None:
        if not isinstance(related, Mapping) or not related.get("type"):
            raise ValidationFailed("related must be a DocumentRef {type, id}", field="related")
        related = {
            "type": text_in(related["type"], "related.type", 80),
            "id": str(uuid_req(related.get("id"), "related.id")),
            "number": related.get("number"),
        }
    row = Activity.objects.create(
        company_id=company_id,
        activity_type=kind,
        subject=subject,
        body=text_in(data.get("body"), "body"),
        due_at=datetime_in(data["due_at"], "due_at") if data.get("due_at") else None,
        related=related,
        owner_id=uuid_in(data.get("owner_id"), "owner_id", required=False) or ctx.on_behalf_of or ctx.principal_id,
        done=bool(data.get("done", False)),
        **_stamp(uow),
    )
    audit(uow, "crm.activity.create", "crm.activity", row.id, company_id, {"type": kind})
    return row


# --------------------------------------------------------------------------- forecast


def forecast(
    ctx: TenantContext, qs: QuerySet[Opportunity], period_from: dt.date | None, period_to: dt.date | None
) -> dict[str, Any]:
    """Pipeline forecast by stage and owner: Σ amount and Σ amount × probability of OPEN opportunities whose
    expected close date falls in the period (COP and other currencies kept apart)."""
    rows = qs.filter(status=Opportunity.Status.OPEN)
    if period_from:
        rows = rows.filter(close_date__gte=period_from)
    if period_to:
        rows = rows.filter(close_date__lte=period_to)
    by_stage: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    by_owner: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for o in rows.order_by("id"):
        weighted = q_amount(o.amount * o.probability / Decimal(100))
        for bucket, key in ((by_stage, (o.stage, o.currency)), (by_owner, (str(o.owner_id or ""), o.currency))):
            acc = bucket[key]
            acc[0] += 1
            acc[1] += o.amount
            acc[2] += weighted
        totals[o.currency][0] += o.amount
        totals[o.currency][1] += weighted

    def rows_of(bucket: dict[tuple[str, str], list[Decimal]], name: str) -> list[dict[str, Any]]:
        return [
            {name: k[0] or None, "currency": k[1], "count": int(v[0]), "amount": f"{v[1]:f}", "weighted": f"{v[2]:f}"}
            for k, v in sorted(bucket.items())
        ]

    return {
        "period_from": period_from.isoformat() if period_from else None,
        "period_to": period_to.isoformat() if period_to else None,
        "by_stage": rows_of(by_stage, "stage"),
        "by_owner": rows_of(by_owner, "owner_id"),
        "totals": [{"currency": c, "amount": f"{v[0]:f}", "weighted": f"{v[1]:f}"} for c, v in sorted(totals.items())],
    }


# --------------------------------------------------------------------------- deletes


def delete_record(perm: str) -> Any:
    """Delete of an unreferenced CRM record (masters are archived instead once referenced)."""

    def run(uow: UnitOfWork, obj: Any) -> None:
        require(uow.ctx, perm, obj)
        if isinstance(obj, Lead) and obj.status == Lead.Status.CONVERTED:
            raise InvalidStateTransition("a converted lead cannot be deleted", status_now=obj.status)
        if isinstance(obj, Opportunity) and obj.status != Opportunity.Status.OPEN:
            raise InvalidStateTransition("a closed opportunity cannot be deleted", status_now=obj.status)
        if (
            isinstance(obj, Pipeline)
            and Opportunity.objects.filter(tenant_id=uow.ctx.tenant_id, pipeline_id=obj.id).exists()
        ):
            raise Conflict("the pipeline has opportunities; archive it instead")
        audit(uow, perm, perm.rsplit(".", 1)[0], obj.id, obj.company_id, {})
        obj.delete()

    return run
