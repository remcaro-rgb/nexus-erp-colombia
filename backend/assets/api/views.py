"""HTTP endpoints of ``/api/v1/assets/…`` and ``/api/v1/kpis/activos`` (contract assets.yaml 1.0.0)."""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

from django.db.models import Model, QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from assets import kpis, reports, service
from assets.api import serializers as ser
from assets.api.base import (
    AssetsAPIView,
    body_of,
    check_if_match,
    command,
    paginate,
    parse_uuid,
    require,
    respond,
    scoped,
    visible,
    writable_check,
)
from assets.errors import FilterInvalid
from assets.models import (
    Asset,
    AssetBook,
    AssetBookValue,
    AssetCategory,
    DepreciationLine,
    DepreciationRun,
    Impairment,
    PhysicalCount,
    RegisterEntry,
    Revaluation,
)
from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

ACTION_FIELDS = ("reason", "comment", "effective_date", "parameters")
BULK_LIMIT = 1000


class Resource:
    """Declarative CRUD resource (list/create/get/patch/delete) bound to one model and its permission codes."""

    name: ClassVar[str]
    model: ClassVar[type[Model]]
    resource_type: ClassVar[str]
    perm: ClassVar[str]  # <module>.<resource>; actions read/create/update/delete
    input_fields: ClassVar[tuple[str, ...]]
    filters: ClassVar[dict[str, str]] = {}
    search: ClassVar[tuple[str, ...]] = ()
    sorts: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def queryset(cls, ctx: TenantContext, company: uuid.UUID) -> QuerySet[Any]:
        return cls.model._default_manager.filter(tenant_id=ctx.tenant_id, company_id=company)

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        raise NotImplementedError

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        raise NotImplementedError

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        raise NotImplementedError

    @classmethod
    def path(cls, obj: Any) -> str:
        return f"/api/v1/assets/{cls.name}/{obj.id}"


def _get(
    ctx: TenantContext, company: uuid.UUID, res: type[Resource], pk: Any, action: str = "read", lock: bool = False
) -> Any:
    qs = scoped(ctx, f"{res.perm}.{action}", res.queryset(ctx, company))
    if lock:
        qs = qs.select_for_update()
    obj = qs.filter(id=parse_uuid(pk)).first()
    if obj is None:
        raise NotFound(f"{res.name} not found")
    return obj


class CollectionView(AssetsAPIView):
    resource: ClassVar[type[Resource]]

    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        res = self.resource
        qs = scoped(ctx, f"{res.perm}.read", res.queryset(ctx, company))
        return paginate(
            request, qs, lambda o: res.to_json(ctx, o), filters=res.filters, search=res.search, sorts=res.sorts
        )

    def post(self, request: Request) -> Response:
        ctx, company = self.company(request)
        res = self.resource
        require(ctx, f"{res.perm}.create")
        data = body_of(request, res.input_fields)
        writable_check(ctx, res.resource_type, data)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            obj = res.create(uow, company, data)
            return res.to_json(ctx, obj), 201, obj.etag

        resp = command(request, ctx, f"{res.resource_type}.create", fn)
        body = resp.data if isinstance(resp.data, dict) else {}
        if body.get("id"):
            resp["Location"] = f"/api/v1/assets/{res.name}/{body['id']}"
        return resp


class ItemView(AssetsAPIView):
    resource: ClassVar[type[Resource]]

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        obj = _get(ctx, company, self.resource, pk)
        return respond(self.resource.to_json(ctx, obj), etag=obj.etag)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        res = self.resource
        require(ctx, f"{res.perm}.update")
        data = body_of(request, res.input_fields)
        writable_check(ctx, res.resource_type, data)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            obj = _get(ctx, company, res, pk, "update", lock=True)
            check_if_match(request, obj)
            obj = res.update(uow, obj, data)
            obj.refresh_from_db()
            return res.to_json(ctx, obj), 200, obj.etag

        return command(request, ctx, f"{res.resource_type}.update", fn)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        res = self.resource
        require(ctx, f"{res.perm}.delete")

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            obj = _get(ctx, company, res, pk, "delete", lock=True)
            check_if_match(request, obj)
            res.delete(uow, obj)
            return None, 204, None

        resp = command(request, ctx, f"{res.resource_type}.delete", fn)
        resp.data = None
        return resp


# =========================================================================== resources


class Categories(Resource):
    name, model, resource_type, perm = "categories", AssetCategory, "assets.category", "assets.category"
    input_fields = ("code", "name", "useful_life_months", "method", "account_role")
    filters: ClassVar[dict[str, str]] = {"code": "code", "status": "status", "method": "method", "name": "name"}
    search = ("code", "name")
    sorts = ("code", "name", "useful_life_months")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.category(obj)

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        for key in ("code", "name", "useful_life_months", "method"):
            if data.get(key) in (None, ""):
                raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        service.validate_category(data)
        if AssetCategory.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company, code=data["code"]).exists():
            raise ValidationFailed("a category with this code exists", pointer="/code", reason="duplicate")
        obj = AssetCategory.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company,
            code=str(data["code"])[:40],
            name=str(data["name"])[:200],
            useful_life_months=data["useful_life_months"],
            method=data["method"],
            account_role=str(data.get("account_role") or "")[:255],
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
        service._audit(uow, "assets.category.create", "assets.category", obj.id, company, {"code": obj.code})
        return obj

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        service.validate_category(data)
        fields = [k for k in cls.input_fields if k in data]
        for k in fields:
            setattr(obj, k, data[k] if k != "account_role" else str(data[k] or ""))
        if fields:
            obj.save_versioned(uow.ctx.principal_id, fields)
        service._audit(uow, "assets.category.update", "assets.category", obj.id, obj.company_id, {"fields": fields})
        return obj

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        if Asset.objects.filter(category=obj).exists():
            raise Conflict("the category is in use", code="invalid_state_transition")
        obj.book_defaults.all().delete()
        service._audit(uow, "assets.category.delete", "assets.category", obj.id, obj.company_id, {"code": obj.code})
        obj.delete()


class Books(Resource):
    name, model, resource_type, perm = "books", AssetBook, "assets.book", "assets.book"
    input_fields = ("code", "name", "framework")
    filters: ClassVar[dict[str, str]] = {"code": "code", "framework": "framework", "status": "status"}
    search = ("code", "name")
    sorts = ("code", "name", "framework")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.book(obj)

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        if data.get("framework") not in ("NIIF", "FISCAL"):
            raise ValidationFailed("framework must be NIIF or FISCAL", pointer="/framework", reason="enum")
        for key in ("code", "name"):
            if not data.get(key):
                raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        if AssetBook.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company, code=data["code"]).exists():
            raise ValidationFailed("a book with this code exists", pointer="/code", reason="duplicate")
        obj = AssetBook.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company,
            code=str(data["code"])[:40],
            name=str(data["name"])[:200],
            framework=data["framework"],
            ledger_book_code=str(data["code"])[:20],
            created_by=uow.ctx.principal_id,
        )
        service._audit(uow, "assets.book.create", "assets.book", obj.id, company, {"code": obj.code})
        return obj

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        if AssetBookValue.objects.filter(book=obj).exclude(status="PENDING").exists() and (
            "framework" in data or "code" in data
        ):
            raise Conflict("a book in use keeps its code and framework", code="invalid_state_transition")
        fields = [k for k in cls.input_fields if k in data]
        for k in fields:
            setattr(obj, k, data[k])
        if fields:
            obj.save_versioned(uow.ctx.principal_id, fields)
        return obj

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        if AssetBookValue.objects.filter(book=obj).exists() or DepreciationRun.objects.filter(book=obj).exists():
            raise Conflict("the book is in use", code="invalid_state_transition")
        obj.delete()


class Assets(Resource):
    name, model, resource_type, perm = "assets", Asset, "assets.asset", "assets.asset"
    input_fields = (
        "code",
        "name",
        "category_id",
        "acquisition_cost",
        "acquisition_date",
        "location",
        "custodian_employee_id",
        "dimensions",
    )
    filters: ClassVar[dict[str, str]] = {
        "code": "code",
        "name": "name",
        "category_id": "category_id",
        "location": "location",
        "acquisition_date": "acquisition_date",
        "lifecycle": "lifecycle",
        "cost_center_id": "cost_center_id",
    }
    search = ("code", "name", "serial")
    sorts = ("code", "name", "acquisition_date", "acquisition_cost")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return visible(ctx, cls.resource_type, ser.asset(obj))

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        for key in ("code", "name", "category_id", "acquisition_cost", "acquisition_date"):
            if data.get(key) in (None, ""):
                raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        return service.create_asset(uow, company, data)

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return service.update_asset(uow, obj, data)

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        service.delete_asset(uow, obj)


class Runs(Resource):
    name, model, resource_type, perm = (
        "depreciation-runs",
        DepreciationRun,
        "assets.depreciation_run",
        "assets.depreciation_run",
    )
    input_fields = ("document_date", "posting_date", "currency", "notes", "period_id", "book_id", "lines")
    filters: ClassVar[dict[str, str]] = {
        "status": "status",
        "book_id": "book_id",
        "period_id": "period_id",
        "document_date": "document_date",
        "number": "number",
    }
    search = ("number", "notes")
    sorts = ("document_date", "number", "period_start")

    @classmethod
    def queryset(cls, ctx: TenantContext, company: uuid.UUID) -> QuerySet[Any]:
        return DepreciationRun.objects.filter(tenant_id=ctx.tenant_id, company_id=company).select_related("book")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        detail = list(DepreciationLine.objects.filter(run=obj).select_related("asset").order_by("line_no"))
        return ser.run(obj, detail)

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        _currency(data)
        return service.create_run(uow, company, data)

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        _currency(data)
        return service.update_run(uow, obj, data)

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        service.delete_run(uow, obj)


def _currency(data: dict[str, Any]) -> None:
    if data.get("currency") not in (None, "COP"):
        raise ValidationFailed("asset documents are in COP", pointer="/currency", reason="currency")


class _Valuations(Resource):
    input_fields = ("document_date", "posting_date", "currency", "notes", "asset_id", "lines")
    filters: ClassVar[dict[str, str]] = {
        "status": "status",
        "asset_id": "asset_id",
        "document_date": "document_date",
        "number": "number",
    }
    search = ("number", "notes")
    sorts = ("document_date", "number")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.valuation(obj)

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        _currency(data)
        if not data.get("document_date") or not data.get("asset_id"):
            raise ValidationFailed("document_date and asset_id are required", pointer="/asset_id", reason="required")
        return service.create_valuation(uow, cls.model, company, data)  # type: ignore[arg-type]  # Revaluation | Impairment

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        _currency(data)
        return service.update_valuation(uow, obj, data)

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        service.delete_valuation(uow, obj)


class Revaluations(_Valuations):
    name, model, resource_type, perm = "revaluations", Revaluation, "assets.revaluation", "assets.revaluation"


class Impairments(_Valuations):
    name, model, resource_type, perm = "impairments", Impairment, "assets.impairment", "assets.impairment"


class Counts(Resource):
    name, model, resource_type, perm = (
        "physical-counts",
        PhysicalCount,
        "assets.physical_count",
        "assets.physical_count",
    )
    input_fields = ("document_date", "posting_date", "currency", "notes", "lines")
    filters: ClassVar[dict[str, str]] = {"status": "status", "document_date": "document_date", "number": "number"}
    search = ("number", "notes")
    sorts = ("document_date", "number")

    @classmethod
    def to_json(cls, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.count(obj)

    @classmethod
    def create(cls, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        if not data.get("document_date"):
            raise ValidationFailed("document_date is required", pointer="/document_date", reason="required")
        return service.create_count(uow, company, data)

    @classmethod
    def update(cls, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return service.update_count(uow, obj, data)

    @classmethod
    def delete(cls, uow: UnitOfWork, obj: Any) -> None:
        service.delete_count(uow, obj)


def _views(res: type[Resource]) -> tuple[type[CollectionView], type[ItemView]]:
    coll = type(f"{res.__name__}CollectionView", (CollectionView,), {"resource": res})
    item = type(f"{res.__name__}ItemView", (ItemView,), {"resource": res})
    return coll, item


CategoryCollection, CategoryItem = _views(Categories)
BookCollection, BookItem = _views(Books)
AssetCollection, AssetItem = _views(Assets)
RunCollection, RunItem = _views(Runs)
RevaluationCollection, RevaluationItem = _views(Revaluations)
ImpairmentCollection, ImpairmentItem = _views(Impairments)
CountCollection, CountItem = _views(Counts)


# =========================================================================== actions


def _action_body(request: Request) -> dict[str, Any]:
    data = body_of(request, ACTION_FIELDS)
    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValidationFailed("parameters must be an object", pointer="/parameters", reason="type")
    data["parameters"] = params
    if data.get("effective_date"):
        data["effective_date"] = service._date(data["effective_date"], "/effective_date")
    return data


class ActionView(AssetsAPIView):
    resource: ClassVar[type[Resource]]
    permission: ClassVar[str]
    lookup_action: ClassVar[str] = "read"
    handler: ClassVar[Callable[..., Any]]
    simulate: ClassVar[bool] = False

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.company(request)
        require(ctx, self.permission)
        data = _action_body(request)
        res = self.resource
        handler = type(self).handler
        if self.simulate:
            # read-only action: same validations as the real one, inside a UoW that is rolled back
            holder: dict[str, Any] = {}

            def sim(uow: UnitOfWork) -> None:
                obj = _get(ctx, company, res, pk, self.lookup_action)
                holder["body"] = handler(uow, obj, data, company)
                raise _Rollback()

            with contextlib.suppress(_Rollback):
                run_in_unit_of_work(sim, ctx)
            return respond(holder["body"])

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            obj = _get(ctx, company, res, pk, self.lookup_action, lock=True)
            result = handler(uow, obj, data, company)
            result.refresh_from_db()
            return res.to_json(ctx, result), 200, result.etag

        return command(request, ctx, f"{self.permission}:{type(self).__name__}", fn)


class _Rollback(Exception):
    """Aborts a simulation unit of work (nothing is persisted, C1-3)."""


def _activate(uow: UnitOfWork, obj: Asset, data: dict[str, Any], company: uuid.UUID) -> Asset:
    return service.activate(uow, obj, data["parameters"], data.get("effective_date"))


def _transfer(uow: UnitOfWork, obj: Asset, data: dict[str, Any], company: uuid.UUID) -> Asset:
    writable_check(uow.ctx, "assets.asset", data["parameters"])
    return service.transfer(uow, obj, data["parameters"], data.get("effective_date"), str(data.get("reason") or ""))


def _dispose(uow: UnitOfWork, obj: Asset, data: dict[str, Any], company: uuid.UUID) -> Asset:
    service.dispose(uow, obj, data["parameters"], data.get("effective_date"), str(data.get("reason") or ""))
    return obj


def _run_post(uow: UnitOfWork, obj: DepreciationRun, data: dict[str, Any], company: uuid.UUID) -> DepreciationRun:
    run, _replayed = service.post_run(uow, obj.id, company)
    return run


def _run_preview(uow: UnitOfWork, obj: DepreciationRun, data: dict[str, Any], company: uuid.UUID) -> dict[str, Any]:
    return service.preview_run(uow, obj)


def _valuation_post(model: type[Revaluation] | type[Impairment]) -> Callable[..., Any]:
    def handler(uow: UnitOfWork, obj: Any, data: dict[str, Any], company: uuid.UUID) -> Any:
        return service.post_valuation(uow, model, company, obj.id)

    return handler


def _valuation_simulate(uow: UnitOfWork, obj: Any, data: dict[str, Any], company: uuid.UUID) -> dict[str, Any]:
    return service.simulate_valuation(uow, obj)


def _count_submit(uow: UnitOfWork, obj: PhysicalCount, data: dict[str, Any], company: uuid.UUID) -> PhysicalCount:
    return service.submit_count(uow, obj)


def _count_approve(uow: UnitOfWork, obj: PhysicalCount, data: dict[str, Any], company: uuid.UUID) -> PhysicalCount:
    return service.approve_count(uow, obj, str(data.get("reason") or data.get("comment") or ""))


def _action(
    name: str,
    res: type[Resource],
    permission: str,
    handler: Callable[..., Any],
    *,
    simulate: bool = False,
    lookup: str = "read",
) -> type[ActionView]:
    return type(
        name,
        (ActionView,),
        {
            "resource": res,
            "permission": permission,
            "handler": staticmethod(handler),
            "simulate": simulate,
            "lookup_action": lookup,
        },
    )


AssetActivate = _action("AssetActivate", Assets, "assets.asset.update", _activate)
AssetTransfer = _action("AssetTransfer", Assets, "assets.asset.update", _transfer)
AssetDispose = _action("AssetDispose", Assets, "assets.asset.void", _dispose)
RunPreview = _action("RunPreview", Runs, "assets.depreciation_run.read", _run_preview, simulate=True)
RunPost = _action("RunPost", Runs, "assets.depreciation_run.post", _run_post)
RevaluationPost = _action("RevaluationPost", Revaluations, "assets.revaluation.post", _valuation_post(Revaluation))
RevaluationSimulate = _action(
    "RevaluationSimulate", Revaluations, "assets.revaluation.read", _valuation_simulate, simulate=True
)
ImpairmentPost = _action("ImpairmentPost", Impairments, "assets.impairment.post", _valuation_post(Impairment))
ImpairmentSimulate = _action(
    "ImpairmentSimulate", Impairments, "assets.impairment.read", _valuation_simulate, simulate=True
)
CountSubmit = _action("CountSubmit", Counts, "assets.physical_count.update", _count_submit)
CountApprove = _action("CountApprove", Counts, "assets.physical_count.approve", _count_approve)


class AssetBulk(AssetsAPIView):
    """``POST /assets:bulk`` — up to 1,000 assets; all-or-nothing (default) or per item."""

    def post(self, request: Request) -> Response:
        ctx, company = self.company(request)
        require(ctx, "assets.asset.create")
        data = body_of(request, ("all_or_nothing", "items"))
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValidationFailed("items is required", pointer="/items", reason="required")
        if len(items) > BULK_LIMIT:
            raise ValidationFailed("at most 1,000 items", pointer="/items", reason="max", code="bulk_limit_exceeded")
        all_or_nothing = data.get("all_or_nothing", True) is not False

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            results: list[dict[str, Any]] = []
            failed = False
            for i, item in enumerate(items):
                try:
                    if not isinstance(item, dict) or set(item) - set(Assets.input_fields):
                        raise ValidationFailed("invalid item", pointer=f"/items/{i}", reason="unknown")
                    writable_check(ctx, "assets.asset", item)
                    from django.db import transaction

                    with transaction.atomic(using=uow.using):
                        obj = Assets.create(uow, company, item)
                    results.append({"index": i, "status": "created", "id": obj.id, "error": None})
                except (ValidationFailed, Conflict, NotFound) as exc:
                    failed = True
                    results.append({"index": i, "status": "failed", "id": None, "error": exc.to_problem()})
            if failed and all_or_nothing:
                for r in results:
                    if r["status"] == "created":
                        r.update(status="skipped", id=None)
                raise _BulkFailed({"all_or_nothing": True, "committed": False, "results": results})
            return {"all_or_nothing": all_or_nothing, "committed": True, "results": results}, 200, None

        try:
            return command(request, ctx, "assets.asset.bulk", fn)
        except _BulkFailed as exc:
            return respond(exc.body, 200)


class _BulkFailed(Exception):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__("bulk failed")
        self.body = body


# =========================================================================== reports and KPIs


def _opt_uuid(request: Request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise FilterInvalid(f"{name} must be a UUID") from None


def _period_param(
    ctx: TenantContext, company: uuid.UUID, raw: str | None, pointer: str, *, end: bool, default: dt.date
) -> dt.date:
    if raw and len(raw) == 36:
        start, finish = kpis.period_bounds(ctx, company, raw)
        return finish if end else start
    return reports.parse_month(raw, pointer, end=end, default=default)


class RegisterReport(AssetsAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        perm = "assets.asset_register.read"
        raw = request.query_params.get("as_of")
        try:
            as_of = dt.date.fromisoformat(raw) if raw else dt.date.max
        except ValueError:
            raise FilterInvalid("as_of must be an ISO date") from None
        base = {"tenant_id": ctx.tenant_id, "company_id": company}
        body = reports.register_report(
            scoped(ctx, perm, Asset.objects.filter(**base)),
            scoped(ctx, perm, RegisterEntry.objects.filter(**base)),
            scoped(ctx, perm, AssetBook.objects.filter(**base)),
            as_of,
            _opt_uuid(request, "book_id"),
        )
        body["rows"] = [_mask_row(ctx, r) for r in body["rows"]]
        return respond(body)


def _mask_row(ctx: TenantContext, row: dict[str, Any]) -> dict[str, Any]:
    return row  # register rows carry no restricted field (custodian is not exported)


class ScheduleReport(AssetsAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        perm = "assets.depreciation_schedule.read"
        asset_id, book_id = _opt_uuid(request, "asset_id"), _opt_uuid(request, "book_id")
        if asset_id is None:
            raise FilterInvalid("asset_id is required")
        base = {"tenant_id": ctx.tenant_id, "company_id": company}
        bvs = scoped(ctx, perm, AssetBookValue.objects.filter(**base, asset_id=asset_id)).select_related("book")
        bvs = bvs.filter(book_id=book_id) if book_id else bvs.order_by("book__framework", "book__code")
        bv = bvs.first()
        if bv is None:
            raise NotFound("asset or book not found")
        return respond(reports.schedule_report(bv, scoped(ctx, perm, DepreciationLine.objects.filter(**base))))


class MovementReport(AssetsAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.company(request)
        perm = "assets.asset_movement.read"
        require(ctx, perm)
        today = clock.business_date()
        start = _period_param(
            ctx,
            company,
            request.query_params.get("period_from"),
            "/period_from",
            end=False,
            default=today.replace(month=1, day=1),
        )
        end = _period_param(ctx, company, request.query_params.get("period_to"), "/period_to", end=True, default=today)
        base = {"tenant_id": ctx.tenant_id, "company_id": company}
        cats = {c.id: c.code for c in scoped(ctx, perm, AssetCategory.objects.filter(**base))}
        entries = scoped(ctx, perm, RegisterEntry.objects.filter(**base))
        return respond(reports.movement_report(entries, cats, start, end, _opt_uuid(request, "book_id")))


class KpiView(AssetsAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self._kpi_company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        compare = request.query_params.get("compare", "previous") == "previous"
        return respond(kpis.activos_kpis(ctx, company, period, compare))

    def _kpi_company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        raw = request.query_params.get("company_id")
        if raw:
            ctx = self.ctx
            return ctx, ctx.require_company(raw)
        return self.company(request)


class KpiPanelView(KpiView):
    def get(self, request: Request, panel: str = "") -> Response:
        if panel != "depreciacion_mensual":
            raise NotFound("panel not found")
        ctx, company = self._kpi_company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        return respond(kpis.depreciation_panel(ctx, company, period))
