"""Generic company-scoped master-data endpoints (tax codes, withholding concepts,
ICA tariffs, party tax profiles, calendars): list / create / get / patch / archive.

DELETE never deletes (C1-5): it archives (``status = ARCHIVED`` / ``active = false``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Model, QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from kernel import services
from kernel.errors import Conflict, ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from tax.api import base, serializers
from tax.models import IcaTariff, PartyTaxProfile, TaxCalendar, TaxCode, WithholdingConcept


def _choice(model_choices: Any, field_name: str) -> Callable[[Any], Any]:
    allowed = {c for c, _ in model_choices.choices}

    def check(value: Any) -> Any:
        if value not in allowed:
            raise ValidationFailed(f"{field_name} must be one of {sorted(allowed)}", field=field_name)
        return value

    return check


def _str(max_len: int, field_name: str, min_len: int = 0) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        if not isinstance(value, str) or not min_len <= len(value) <= max_len:
            raise ValidationFailed(
                f"{field_name} must be a string of {min_len}..{max_len} characters", field=field_name
            )
        return value

    return check


def _bool(field_name: str) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValidationFailed(f"{field_name} must be a boolean", field=field_name)
        return value

    return check


def _str_list(field_name: str) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        if not isinstance(value, list) or not all(isinstance(x, str) and len(x) <= 255 for x in value):
            raise ValidationFailed(f"{field_name} must be a list of strings", field=field_name)
        return value

    return check


def _year(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int) or not 2000 <= value <= 2100:
        raise ValidationFailed("year must be an integer 2000..2100", field="year")
    return value


def _obj(field_name: str) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValidationFailed(f"{field_name} must be an object", field=field_name)
        return value

    return check


def _dane(value: Any) -> Any:
    if not isinstance(value, str) or len(value) != 5 or not value.isdigit():
        raise ValidationFailed("municipality_code must be a 5-digit DANE code", field="municipality_code")
    return value


def _party(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "id" not in value or set(value) - {"id", "kind", "name", "tax_id"}:
        raise ValidationFailed("party must be {id, kind}", field="party")
    kind = value.get("kind", "other")
    if kind not in ("customer", "vendor", "employee", "other"):
        raise ValidationFailed("party.kind must be customer|vendor|employee|other", field="party.kind")
    return {"party_id": base.parse_uuid(value["id"], "party.id"), "party_kind": kind}


@dataclass(frozen=True)
class Resource:
    name: str
    model: type[Model]
    permission: str  # e.g. tax.tax_code
    to_json: Callable[[Any], dict[str, Any]]
    fields: dict[str, Callable[[Any], Any]]
    required: tuple[str, ...]
    archive: Callable[[Any], list[str]]
    expand: dict[str, Callable[[Any], dict[str, Any]]] = field(default_factory=dict)
    extra_fields: tuple[str, ...] = ()  # accepted beyond the contract (CCR P06-1)


def _archive_status(obj: Any) -> list[str]:
    obj.status = "ARCHIVED"
    return ["status"]


def _archive_active(obj: Any) -> list[str]:
    obj.active = False
    return ["active"]


RESOURCES: dict[str, Resource] = {
    "tax-codes": Resource(
        "tax-codes", TaxCode, "tax.tax_code", serializers.tax_code,
        {"code": _str(40, "code", 1), "name": _str(200, "name", 1), "tax_type": _choice(TaxCode.TaxType, "tax_type"),
         "rate_parameter_code": _str(40, "rate_parameter_code", 1), "account_role": _str(255, "account_role"),
         "base_rule": _choice(TaxCode.BaseRule, "base_rule"), "base_tax_type": _str(16, "base_tax_type"),
         "min_base_uvt_parameter_code": _str(40, "min_base_uvt_parameter_code"),
         "applicability": lambda v: v if isinstance(v, list) else _obj("applicability")(v)},
        ("code", "name", "tax_type"), _archive_status,
        extra_fields=("base_rule", "base_tax_type", "min_base_uvt_parameter_code", "applicability"),
    ),
    "withholding-concepts": Resource(
        "withholding-concepts", WithholdingConcept, "tax.withholding_concept", serializers.withholding_concept,
        {"code": _str(40, "code", 1), "name": _str(200, "name", 1),
         "tax_type": _choice(WithholdingConcept.TaxType, "tax_type"),
         "base_parameter_code": _str(40, "base_parameter_code", 1),
         "rate_parameter_code": _str(40, "rate_parameter_code", 1)},
        ("code", "name", "tax_type"), _archive_status,
    ),
    "ica-tariffs": Resource(
        "ica-tariffs", IcaTariff, "tax.ica_tariff", serializers.ica_tariff,
        {"code": _str(40, "code", 1), "name": _str(200, "name", 1), "municipality_code": _dane,
         "activity_code": _str(10, "activity_code", 1), "rate_parameter_code": _str(40, "rate_parameter_code", 1)},
        ("code", "name", "municipality_code", "activity_code"), _archive_status,
    ),
    "party-tax-profiles": Resource(
        "party-tax-profiles", PartyTaxProfile, "tax.party_tax_profile", serializers.party_tax_profile,
        {"active": _bool("active"), "regime": _choice(PartyTaxProfile.Regime, "regime"),
         "responsibilities": _str_list("responsibilities"), "withholding_agent": _bool("withholding_agent"),
         "person_type": _choice(PartyTaxProfile.PersonType, "person_type"),
         "gran_contribuyente": _bool("gran_contribuyente"), "autorretenedor": _bool("autorretenedor"),
         "vat_withholding_agent": _bool("vat_withholding_agent"),
         "ica_municipalities": _str_list("ica_municipalities"), "ciiu_codes": _str_list("ciiu_codes")},
        ("party",), _archive_active, expand={"party": _party},
        extra_fields=("person_type", "gran_contribuyente", "autorretenedor", "vat_withholding_agent",
                      "ica_municipalities", "ciiu_codes"),
    ),
    "calendars": Resource(
        "calendars", TaxCalendar, "tax.calendar", serializers.calendar,
        {"code": _str(40, "code", 1), "name": _str(200, "name", 1), "obligation": _str(255, "obligation", 1),
         "year": _year, "due_dates": _obj("due_dates")},
        ("code", "name", "obligation", "year"), _archive_status,
    ),
}  # fmt: skip


def _clean(resource: Resource, data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    allowed = set(resource.fields) | set(resource.expand)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    if partial and not data:
        raise ValidationFailed("empty patch")
    if not partial:
        missing = [k for k in resource.required if data.get(k) in (None, "")]
        if missing:
            raise ValidationFailed(f"missing properties: {', '.join(missing)}", fields=missing)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in resource.expand:
            out.update(resource.expand[key](value))
        else:
            out[key] = resource.fields[key](value)
    if resource.model is TaxCalendar and "due_dates" in out:
        from tax.calendar import validate_due_dates

        validate_due_dates(out["due_dates"], out.get("year"))
    return out


def scoped(resource: Resource, ctx: TenantContext, company: uuid.UUID, action: str) -> QuerySet[Any]:
    qs = resource.model._default_manager.filter(tenant_id=ctx.tenant_id, company_id=company)
    return services.get(PolicyService).scope_queryset(ctx, f"{resource.permission}.{action}", qs)


def _save(fn: Callable[[], Any]) -> Any:
    try:
        with transaction.atomic():
            return fn()
    except IntegrityError as exc:
        raise Conflict("a record with the same code already exists", code="duplicate") from exc


class ListCreate(base.TaxAPIView):
    resource: Resource

    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, f"{self.resource.permission}.read")
        qs = scoped(self.resource, ctx, company, "read")
        code = request.query_params.get("code")
        if code:
            qs = qs.filter(code=code)
        return base.page(request, qs, self.resource.to_json)

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, f"{self.resource.permission}.create")
        values = _clean(self.resource, base.body_object(request, set(self.resource.fields) | set(self.resource.expand)),
                        partial=False)  # fmt: skip

        def create(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = _save(lambda: self.resource.model._default_manager.create(
                tenant_id=ctx.tenant_id, company_id=company, created_by=ctx.principal_id,
                updated_by=ctx.principal_id, **values))  # fmt: skip
            location = f"/api/v1/tax/{self.resource.name}/{obj.id}"
            return self.resource.to_json(obj), 201, {"ETag": obj.etag, "Location": location}

        return base.command(request, ctx, f"tax.{self.resource.name}.create", create)


class Detail(base.TaxAPIView):
    resource: Resource

    def _obj(self, request: Request, pk: uuid.UUID, action: str, *, lock: bool = False) -> tuple[Any, TenantContext]:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        base.authorize(ctx, f"{self.resource.permission}.{action}")
        qs = scoped(self.resource, ctx, company, action)
        if lock:
            qs = qs.select_for_update()
        return base.get_or_404(qs, pk), ctx

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        obj, _ = self._obj(request, pk, "read")
        return base.with_etag(Response(self.resource.to_json(obj)), obj)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        values = _clean(self.resource, base.body_object(request, set(self.resource.fields) | set(self.resource.expand)),
                        partial=True)  # fmt: skip

        def update(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj, _ = self._obj(request, pk, "update", lock=True)
            obj.check_if_match(request.headers.get("If-Match"))
            for key, value in values.items():
                setattr(obj, key, value)
            _save(lambda: obj.save_versioned(ctx.principal_id, list(values)))
            return self.resource.to_json(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, f"tax.{self.resource.name}.update", update)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)

        def archive(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj, _ = self._obj(request, pk, "delete", lock=True)
            if request.headers.get("If-Match"):
                obj.check_if_match(request.headers.get("If-Match"))
            obj.save_versioned(ctx.principal_id, self.resource.archive(obj))
            return None, 204, {}

        return base.command(request, ctx, f"tax.{self.resource.name}.delete", archive)


def views_for(name: str) -> tuple[Any, Any]:
    resource = RESOURCES[name]
    list_view = type(f"{resource.model.__name__}ListCreate", (ListCreate,), {"resource": resource})
    detail_view = type(f"{resource.model.__name__}Detail", (Detail,), {"resource": resource})
    return list_view.as_view(), detail_view.as_view()
