"""Generic secured resource endpoints for the HR and time keeping APIs (ADR-004 via :class:`SecuredView`).

A :class:`Spec` declares one resource of the frozen contract (``hr.yaml`` / ``timekeeping.yaml``); :func:`routes`
builds its documented routes only: collection (GET list, POST create), detail (GET, PATCH with ``If-Match``,
DELETE), and explicit POST actions (``/{id}:action`` or collection ``:action``). Every write needs an
``Idempotency-Key`` and runs in one unit of work; reads are policy-scoped (company row security) and field-masked;
a foreign id is 404.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.urls import URLPattern, path
from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import SecuredView, check_if_match, etag_for
from iam.policy.engine import get_engine
from kernel import tenancy
from kernel.uow import UnitOfWork

Creator = Callable[[UnitOfWork, uuid.UUID, Mapping[str, Any]], Any]
Updater = Callable[[UnitOfWork, Any, Mapping[str, Any]], None]
Deleter = Callable[[UnitOfWork, Any], None]
ActionFn = Callable[[UnitOfWork, Any, Mapping[str, Any]], None]
CollectionActionFn = Callable[[UnitOfWork, uuid.UUID, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class Action:
    permission: str
    fn: ActionFn
    status: int = 200


@dataclass(frozen=True)
class CollectionAction:
    permission: str
    fn: CollectionActionFn
    status: int = 200


@dataclass(frozen=True)
class Spec:
    resource: str  # "<module>.<resource>" (field policy + permission prefix)
    url: str  # route without leading slash, e.g. "api/v1/hr/employees"
    model: Any
    to_dict: Callable[[Any], dict[str, Any]]
    create: Creator | None = None
    update: Updater | None = None
    delete: Deleter | None = None
    input_fields: tuple[str, ...] = ()
    filters: Mapping[str, str] = field(default_factory=dict)
    sorts: tuple[str, ...] = ()
    search: tuple[str, ...] = ()
    actions: Mapping[str, Action] = field(default_factory=dict)
    collection_actions: Mapping[str, CollectionAction] = field(default_factory=dict)
    select_related: tuple[str, ...] = ()

    def perm(self, verb: str) -> str:
        return f"{self.resource}.{verb}"


def company_of(view: SecuredView) -> uuid.UUID:
    """The request's single active company (``X-Company-Id`` validated by the authentication)."""
    return sorted(view.ctx.company_ids)[0]


class _Base(SecuredView):
    spec: Spec

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return self.spec.to_dict(obj)

    def _qs(self) -> Any:
        qs = self.spec.model.objects.all()
        if self.spec.select_related:
            qs = qs.select_related(*self.spec.select_related)
        return qs

    def _load(self, pk: str, action: str, lock: bool = False) -> Any:
        qs = self._qs()
        if lock:
            qs = qs.select_for_update(of=("self",))
        return self.get_scoped(qs, pk, action)

    def _respond(self, response: Response, location: bool = False) -> Response:
        if location and response.status_code == 201 and isinstance(response.data, dict) and "id" in response.data:
            response["Location"] = f"/{self.spec.url}/{response.data['id']}"
        return response


class CollectionView(_Base):
    def get(self, request: Request) -> Response:
        qs = get_engine().scope_queryset(self.ctx, self.spec.perm("read"), self._qs())
        return self.paginate(
            request, qs, search_fields=self.spec.search, filter_fields=self.spec.filters, sort_fields=self.spec.sorts
        )

    def post(self, request: Request) -> Response:
        create = self.spec.create
        assert create is not None  # noqa: S101 - POST is only routed when a creator exists
        self.check_writable(request.data, self.spec.input_fields)
        company = company_of(self)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            get_engine().require(self.ctx, self.spec.perm("create"), {"company_id": company})
            obj = create(uow, company, request.data)
            return self.serialize(obj), 201

        return self._respond(self.mutate(request, run, scope=self.spec.perm("create")), location=True)


class DetailView(_Base):
    def get(self, request: Request, pk: str) -> Response:
        with tenancy.scope(self.ctx):
            obj = self._load(pk, self.spec.perm("read"))
            response = Response(self.serialize(obj))
        response["ETag"] = etag_for(self.spec.resource, obj.pk, obj.version)
        return response

    def patch(self, request: Request, pk: str) -> Response:
        update = self.spec.update
        assert update is not None  # noqa: S101 - PATCH is only routed when an updater exists
        self.check_writable(request.data, self.spec.input_fields)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            obj = self._load(pk, self.spec.perm("update"), lock=True)
            check_if_match(request, self.spec.resource, obj, required=True)
            update(uow, obj, request.data)
            return self.serialize(obj), 200

        return self.mutate(request, run, scope=self.spec.perm("update"))

    def delete(self, request: Request, pk: str) -> Response:
        delete = self.spec.delete
        assert delete is not None  # noqa: S101 - DELETE is only routed when a deleter exists

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            obj = self._load(pk, self.spec.perm("delete"), lock=True)
            check_if_match(request, self.spec.resource, obj, required=False)
            delete(uow, obj)
            return None, 204

        return self.mutate(request, run, scope=self.spec.perm("delete"))


class ActionView(_Base):
    action_name: str = ""

    def post(self, request: Request, pk: str) -> Response:
        action = self.spec.actions[self.action_name]
        payload = request.data if isinstance(request.data, Mapping) else {}

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            obj = self._load(pk, action.permission, lock=True)
            get_engine().require(self.ctx, action.permission, {"company_id": obj.company_id})
            action.fn(uow, obj, payload)
            return self.serialize(obj), action.status

        return self.mutate(request, run, scope=f"{self.spec.resource}:{self.action_name}")


class CollectionActionView(_Base):
    action_name: str = ""

    def post(self, request: Request) -> Response:
        action = self.spec.collection_actions[self.action_name]
        payload = request.data if isinstance(request.data, Mapping) else {}
        company = company_of(self)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            get_engine().require(self.ctx, action.permission, {"company_id": company})
            obj = action.fn(uow, company, payload)
            return self.serialize(obj), action.status

        return self.mutate(request, run, scope=f"{self.spec.resource}:{self.action_name}")


def _view(base: type[_Base], spec: Spec, permissions: dict[str, str], **attrs: Any) -> Any:
    name = base.__name__ + "_" + spec.resource.replace(".", "_") + "_" + attrs.get("action_name", "").replace("-", "_")
    cls = type(name, (base,), {"spec": spec, "resource_type": spec.resource, "permissions": permissions, **attrs})
    return cls.as_view()


def routes(spec: Spec) -> list[URLPattern]:
    """URL patterns of the documented operations of ``spec`` (actions before the detail route)."""
    out: list[URLPattern] = []
    for name, ca in spec.collection_actions.items():
        out.append(path(f"{spec.url}:{name}", _view(CollectionActionView, spec, {"POST": ca.permission}, action_name=name)))
    collection = {"GET": spec.perm("read")}
    if spec.create is not None:
        collection["POST"] = spec.perm("create")
    out.append(path(spec.url, _view(CollectionView, spec, collection)))
    for name, act in spec.actions.items():
        out.append(path(f"{spec.url}/<str:pk>:{name}", _view(ActionView, spec, {"POST": act.permission}, action_name=name)))
    detail = {"GET": spec.perm("read")}
    if spec.update is not None:
        detail["PATCH"] = spec.perm("update")
    if spec.delete is not None:
        detail["DELETE"] = spec.perm("delete")
    out.append(path(f"{spec.url}/<str:pk>", _view(DetailView, spec, detail)))
    return out


def audit_dict(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat(),
        "created_by": str(obj.created_by) if obj.created_by else None,
        "updated_at": obj.updated_at.isoformat(),
        "updated_by": str(obj.updated_by) if obj.updated_by else None,
    }


def money(value: Any, currency: str = "COP") -> dict[str, str] | None:
    from kernel.money import q_amount

    return None if value is None else {"amount": str(q_amount(value)), "currency": currency}
