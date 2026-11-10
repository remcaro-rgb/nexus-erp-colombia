"""Sales endpoints (contract ``contracts/openapi/modules/sales.yaml``, P10a core).

One generic resource table drives list/create/get/patch/delete/actions: each handler authorizes with the
operation's ``x-permission`` (deny by default, the services call ``PolicyService``), reads inside the
request's forced-RLS scope with the policy's row scope (salesperson "own customers", company), and writes
in one idempotent unit of work. Simulations, ``pricing:quote`` and reprice previews run inside a savepoint
that is rolled back: they never persist anything (C1-3).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.db import transaction
from django.db.models import QuerySet
from rest_framework.request import Request
from rest_framework.response import Response

from kernel import clock
from kernel.errors import NotFound, PermissionDenied, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.api import serializers as ser
from sales.api.base import SalesView, action_body, body, check_if_match, command, page
from sales.models import (
    ArOpenItem,
    Delivery,
    Invoice,
    Party,
    PriceFormula,
    PriceList,
    Quote,
    Receipt,
    SalesNote,
    SalesOrder,
    SalesReturn,
    WriteOff,
)
from sales.party import service as parties
from sales.pricing import service as pricing
from sales.services import ar, collections, documents, posting
from sales.services.common import get_scoped, require, scoped


def rolled_back[T](fn: Callable[[], T]) -> T:
    """Run ``fn`` in a savepoint that is always rolled back (read-only previews)."""
    with transaction.atomic():
        result = fn()
        transaction.set_rollback(True)
    return result


@dataclass(frozen=True)
class Resource:
    resource: str
    model: Any
    to_json: Callable[[TenantContext, Any], dict[str, Any]]
    read: str
    create: Callable[[UnitOfWork, uuid.UUID, Mapping[str, Any]], Any] | None = None
    update: Callable[[UnitOfWork, Any, Mapping[str, Any]], Any] | None = None
    delete: Callable[[UnitOfWork, Any], None] | None = None
    actions: Mapping[str, Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]] = field(default_factory=dict)
    filters: Mapping[str, str] = field(default_factory=dict)
    base: Callable[[TenantContext], QuerySet[Any]] | None = None
    previews: frozenset[str] = frozenset()

    def queryset(self, ctx: TenantContext) -> QuerySet[Any]:
        qs = self.base(ctx) if self.base else self.model.objects.all()
        return scoped(ctx, self.read, qs)


def _doc(resource: str) -> Callable[[TenantContext, Any], dict[str, Any]]:
    return lambda ctx, obj: ser.document_json(ctx, resource, obj)


def serialize(ctx: TenantContext, obj: Any) -> Any:
    """JSON of any action result (documents created by conversions included)."""
    if isinstance(obj, dict):
        if "document" in obj and "diff" in obj:
            doc = obj["document"]
            return ser.document_json(ctx, RESOURCE_OF[type(doc)], doc, obj["diff"])
        if "journal_entries" in obj:
            return {"persisted": False, **obj}
        return obj
    for model, fn in SERIALIZERS.items():
        if isinstance(obj, model):
            return fn(ctx, obj)
    raise TypeError(f"no serializer for {type(obj).__name__}")


def _merge(uow: UnitOfWork, party: Party, reason: str, params: Mapping[str, Any]) -> Party:
    return parties.merge(uow, party, params.get("duplicate_id"), reason)


def _note(kind: str) -> Callable[[UnitOfWork, uuid.UUID, Mapping[str, Any]], SalesNote]:
    return lambda uow, company, data: posting.create_note(uow, company, kind, data)


def _note_action(uow: UnitOfWork, note: SalesNote, action: str) -> Any:
    if action == "post":
        return posting.post_note(uow, note)
    return posting.simulate_note(uow, note)


def _delete_note(uow: UnitOfWork, note: SalesNote) -> None:
    require(uow.ctx, "sales.credit_note.delete" if note.kind == "CREDIT" else "sales.debit_note.delete", note)
    if note.status != SalesNote.Status.DRAFT:
        raise ValidationFailed("only a DRAFT note can be deleted")
    note.lines.all().delete()
    note.delete()


def _quote_act(a: str) -> Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]:
    return lambda uow, q, r, p: documents.quote_action(uow, q, a, r, p)


def _invoice_act(a: str) -> Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]:
    return lambda uow, i, r, p: posting.invoice_action(uow, i, a, r, p)


def _note_act(a: str) -> Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]:
    return lambda uow, n, r, p: _note_action(uow, n, a)


def _write_off_act(a: str) -> Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]:
    return lambda uow, x, r, p: collections.write_off_action(uow, x, a)


def _return_act(a: str) -> Callable[[UnitOfWork, Any, str, Mapping[str, Any]], Any]:
    return lambda uow, x, r, p: collections.return_action(uow, x, a, r)


def _delete_simple(perm: str) -> Callable[[UnitOfWork, Any], None]:
    def run(uow: UnitOfWork, obj: Any) -> None:
        require(uow.ctx, perm, obj)
        if obj.status != "DRAFT":
            raise ValidationFailed("only a DRAFT document can be deleted")
        if hasattr(obj, "lines") and hasattr(obj.lines, "all"):
            obj.lines.all().delete()
        obj.delete()

    return run


RESOURCES: dict[str, Resource] = {
    "parties": Resource(
        "sales.party",
        Party,
        ser.party_json,
        "sales.party.read",
        create=parties.create_party,
        update=parties.update_party,
        actions={"merge": _merge},
        filters={
            "code": "code",
            "nit": "nit",
            "status": "status",
            "customer_group": "customer_group",
            "salesperson_id": "salesperson_id",
        },
    ),
    "price-lists": Resource(
        "sales.price_list",
        PriceList,
        ser.price_list_json,
        "sales.price_list.read",
        create=pricing.create_price_list,
        update=pricing.update_price_list,
        filters={"code": "code", "currency": "currency", "customer_group": "customer_group", "status": "status"},
    ),
    "price-formulas": Resource(
        "sales.price_formula",
        PriceFormula,
        ser.price_formula_json,
        "sales.price_formula.read",
        create=pricing.create_price_formula,
        update=pricing.update_price_formula,
        filters={"code": "code", "status": "status", "formula_code": "formula_code"},
    ),
    "quotes": Resource(
        "sales.quote",
        Quote,
        _doc("sales.quote"),
        "sales.quote.read",
        create=documents.create_quote,
        update=documents.update_quote,
        delete=lambda uow, q: documents.delete_draft(uow, q, "sales.quote.delete"),
        actions={a: _quote_act(a) for a in ("send", "accept", "to-order", "cancel", "reprice")},
        filters={"status": "status", "party": "party_id", "salesperson_id": "salesperson_id"},
        base=lambda ctx: Quote.objects.select_related("party"),
        previews=frozenset({"reprice"}),
    ),
    "orders": Resource(
        "sales.order",
        SalesOrder,
        _doc("sales.order"),
        "sales.order.read",
        create=documents.create_order,
        update=documents.update_order,
        delete=lambda uow, o: documents.delete_draft(uow, o, "sales.order.delete"),
        actions={
            "confirm": lambda uow, o, r, p: documents.confirm_order(uow, o, r),
            "cancel": lambda uow, o, r, p: documents.cancel_order(uow, o, r),
            "to-invoice": lambda uow, o, r, p: posting.invoice_from_order(uow, o, p),
        },
        filters={"status": "status", "party": "party_id", "salesperson_id": "salesperson_id"},
        base=lambda ctx: SalesOrder.objects.select_related("party"),
    ),
    "deliveries": Resource(
        "sales.delivery",
        Delivery,
        ser.delivery_json,
        "sales.delivery.read",
        create=posting.create_delivery,
        delete=_delete_simple("sales.delivery.delete"),
        actions={"validate": lambda uow, x, r, p: posting.validate_delivery(uow, x)},
        filters={"status": "status", "order": "order_id"},
        base=lambda ctx: Delivery.objects.select_related("party"),
    ),
    "invoices": Resource(
        "sales.invoice",
        Invoice,
        _doc("sales.invoice"),
        "sales.invoice.read",
        create=posting.create_invoice,
        update=posting.update_invoice,
        delete=lambda uow, i: documents.delete_draft(uow, i, "sales.invoice.delete"),
        actions={a: _invoice_act(a) for a in ("post", "simulate", "send", "reprice")},
        filters={"status": "status", "party": "party_id", "salesperson_id": "salesperson_id", "number": "number"},
        base=lambda ctx: Invoice.objects.select_related("party"),
        previews=frozenset({"simulate", "reprice"}),
    ),
    "credit-notes": Resource(
        "sales.credit_note",
        SalesNote,
        ser.note_json,
        "sales.credit_note.read",
        create=_note(SalesNote.Kind.CREDIT),
        delete=_delete_note,
        actions={a: _note_act(a) for a in ("post", "simulate")},
        filters={"status": "status", "party": "party_id"},
        base=lambda ctx: SalesNote.objects.select_related("party", "invoice").filter(kind=SalesNote.Kind.CREDIT),
        previews=frozenset({"simulate"}),
    ),
    "debit-notes": Resource(
        "sales.debit_note",
        SalesNote,
        ser.note_json,
        "sales.debit_note.read",
        create=_note(SalesNote.Kind.DEBIT),
        delete=_delete_note,
        actions={a: _note_act(a) for a in ("post", "simulate")},
        filters={"status": "status", "party": "party_id"},
        base=lambda ctx: SalesNote.objects.select_related("party", "invoice").filter(kind=SalesNote.Kind.DEBIT),
        previews=frozenset({"simulate"}),
    ),
    "receipts": Resource(
        "sales.receipt",
        Receipt,
        ser.receipt_json,
        "sales.receipt.read",
        create=collections.create_receipt,
        delete=_delete_simple("sales.receipt.delete"),
        actions={"post": lambda uow, x, r, p: collections.post_receipt(uow, x)},
        filters={"status": "status", "party": "party_id"},
        base=lambda ctx: Receipt.objects.select_related("party"),
    ),
    "ar/open-items": Resource(
        "sales.ar_open_item",
        ArOpenItem,
        ser.open_item_json,
        "sales.ar_open_item.read",
        filters={"party": "party_id", "status": "status", "currency": "currency"},
        base=lambda ctx: ArOpenItem.objects.select_related("party"),
    ),
    "write-offs": Resource(
        "sales.write_off",
        WriteOff,
        ser.write_off_json,
        "sales.write_off.read",
        create=collections.create_write_off,
        delete=_delete_simple("sales.write_off.delete"),
        actions={a: _write_off_act(a) for a in ("post", "simulate")},
        filters={"status": "status", "party": "party_id"},
        base=lambda ctx: WriteOff.objects.select_related("party", "open_item"),
        previews=frozenset({"simulate"}),
    ),
    "returns": Resource(
        "sales.return",
        SalesReturn,
        ser.return_json,
        "sales.return.read",
        create=collections.create_return,
        actions={a: _return_act(a) for a in ("submit", "approve", "post")},
        filters={"status": "status"},
        base=lambda ctx: SalesReturn.objects.select_related("invoice"),
    ),
}

RESOURCE_OF: dict[type, str] = {Quote: "sales.quote", SalesOrder: "sales.order", Invoice: "sales.invoice"}
SERIALIZERS: dict[type, Callable[[TenantContext, Any], dict[str, Any]]] = {
    Quote: _doc("sales.quote"),
    SalesOrder: _doc("sales.order"),
    Invoice: _doc("sales.invoice"),
    Party: ser.party_json,
    PriceList: ser.price_list_json,
    PriceFormula: ser.price_formula_json,
    Delivery: ser.delivery_json,
    SalesNote: ser.note_json,
    Receipt: ser.receipt_json,
    ArOpenItem: ser.open_item_json,
    WriteOff: ser.write_off_json,
    SalesReturn: ser.return_json,
}


class _ResourceView(SalesView):
    """Generic resource handler; CRM reuses it with its own ``registry`` and ``serialize_result``."""

    key: str = ""
    registry: ClassVar[Mapping[str, Resource]] = RESOURCES
    created_actions: ClassVar[frozenset[str]] = frozenset({"to-order", "to-invoice"})

    def resource_spec(self) -> Resource:
        return self.registry[self.key]

    def serialize_result(self, ctx: TenantContext, obj: Any) -> Any:
        return serialize(ctx, obj)


class CollectionView(_ResourceView):
    def get(self, request: Request) -> Response:
        spec = self.resource_spec()
        ctx, _company = self.ctx_company(request)
        return page(request, spec.queryset(ctx), lambda o: spec.to_json(ctx, o), spec.filters)

    def post(self, request: Request) -> Response:
        spec = self.resource_spec()
        if spec.create is None:
            raise NotFound("not found")
        ctx, company = self.ctx_company(request)
        data = body(request)
        create = spec.create

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            return self.serialize_result(ctx, create(uow, company, data)), 201

        return command(request, ctx, f"{spec.resource}.create", run)


class ItemView(_ResourceView):
    def _get(self, ctx: TenantContext, pk: Any) -> Any:
        spec = self.resource_spec()
        return get_scoped(ctx, spec.read, spec.base(ctx) if spec.base else spec.model.objects.all(), pk)

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = self.context()
        obj = self._get(ctx, pk)
        response = Response(self.resource_spec().to_json(ctx, obj))
        if hasattr(obj, "etag"):
            response["ETag"] = obj.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        spec = self.resource_spec()
        if spec.update is None:
            raise NotFound("not found")
        ctx = self.context()
        obj = self._get(ctx, pk)
        check_if_match(request, obj, required=True)
        data = body(request)
        if (
            "status" in data
            and spec.resource != "sales.party"
            and spec.resource not in ("sales.price_list", "sales.price_formula")
        ):
            raise ValidationFailed("status changes only through actions", field="status")
        update = spec.update

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            return self.serialize_result(ctx, update(uow, obj, data)), 200

        return command(request, ctx, f"{spec.resource}.update", run)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        spec = self.resource_spec()
        if spec.delete is None:
            raise NotFound("not found")
        ctx = self.context()
        obj = self._get(ctx, pk)
        check_if_match(request, obj, required=False)
        delete = spec.delete

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            delete(uow, obj)
            return None, 204

        return command(request, ctx, f"{spec.resource}.delete", run)


class ActionView(_ResourceView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        spec = self.resource_spec()
        fn = spec.actions.get(action)
        if fn is None:
            raise NotFound("not found")
        ctx = self.context()
        obj = get_scoped(ctx, spec.read, spec.base(ctx) if spec.base else spec.model.objects.all(), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)
        preview = action in spec.previews and not (action == "reprice" and params.get("apply"))

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            if preview:
                return rolled_back(lambda: self.serialize_result(ctx, fn(uow, obj, reason, params))), 200
            status = 201 if action in self.created_actions else 200
            return self.serialize_result(ctx, fn(uow, obj, reason, params)), status

        return command(request, ctx, f"{spec.resource}.{action}", run)


class BulkPartiesView(SalesView):
    """``POST /parties:bulk`` (≤ 1,000 items): all-or-nothing creation; duplicates fail the whole batch."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        data = body(request)
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValidationFailed("items must be a list of 1–1000 parties", field="items")

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            created = [parties.create_party(uow, company, x) for x in items]
            return {"status": "OK", "data": {"created": [str(p.id) for p in created], "count": len(created)}}, 200

        return command(request, ctx, "sales.party.bulk", run)


class PricingQuoteView(SalesView):
    """``POST /pricing:quote`` → price + explanation per item (D-024); read-only (rolled back)."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        raw = body(request)
        params = raw.get("parameters")
        source: Mapping[str, Any] = params if isinstance(params, Mapping) else raw
        data = {k: v for k, v in source.items() if k not in ("reason", "comment", "effective_date")}

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            result = rolled_back(lambda: pricing.price_quote(uow, company, data))
            return {"status": "OK", "data": result, "warnings": []}, 200

        return command(request, ctx, "sales.pricing.quote", run)


class ItemPricesView(SalesView):
    """Item base prices (lowest D-024 level): ``GET``/``POST /item-prices`` (CCR P10a-1, additive)."""

    def get(self, request: Request) -> Response:
        from sales.models import ItemSalesPrice

        ctx, _company = self.ctx_company(request)
        qs = scoped(ctx, "sales.price_list.read", ItemSalesPrice.objects.all())
        return page(request, qs, ser.item_price_json, {"item_id": "item_id", "currency": "currency"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        data = body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            return ser.item_price_json(pricing.set_item_price(uow, company, data)), 200

        return command(request, ctx, "sales.item_price.set", run)


def _date_param(request: Request, name: str, default: dt.date | None = None) -> dt.date:
    raw = request.query_params.get(name)
    if not raw:
        if default is None:
            raise ValidationFailed(f"{name} is required", field=name)
        return default
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationFailed(f"{name} must be a date", field=name) from exc


def _require_balances(ctx: TenantContext) -> None:
    """AR balances are ``confidential``: reports that only show balances need the field grant (fail closed)."""
    if "balance" not in ser._readable(ctx, "sales.ar_open_item"):
        raise PermissionDenied("AR balances are confidential for this principal", code="field_forbidden")


class AgingView(SalesView):
    def get(self, request: Request) -> Response:
        ctx, _company = self.ctx_company(request)
        require(ctx, "sales.ar_aging.read")
        qs = scoped(ctx, "sales.ar_aging.read", ArOpenItem.objects.all())
        if request.query_params.get("party_id"):
            qs = qs.filter(party_id=request.query_params["party_id"])
        if request.query_params.get("salesperson_id"):
            qs = qs.filter(party__salesperson_id=request.query_params["salesperson_id"])
        _require_balances(ctx)
        return Response(ar.aging(ctx, qs, _date_param(request, "as_of", clock.business_date())))


class StatementView(SalesView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "sales.ar_statement.read")
        _require_balances(ctx)
        party = get_scoped(
            ctx, "sales.party.read", Party.objects.filter(company_id=company), request.query_params.get("party_id")
        )
        qs = scoped(ctx, "sales.ar_statement.read", ArOpenItem.objects.all())
        today = clock.business_date()
        data = ar.statement(
            ctx,
            party,
            qs,
            _date_param(request, "date_from", today.replace(day=1)),
            _date_param(request, "date_to", today),
        )
        return Response(data)


class DuplicatesView(SalesView):
    """``GET /parties/duplicates?nit=&email=&phone=`` (duplicate detection, T-P10-14)."""

    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "sales.party.read")
        found = parties.duplicates(
            ctx,
            company,
            nit=request.query_params.get("nit"),
            email=request.query_params.get("email"),
            phone=request.query_params.get("phone"),
        )
        return Response({"items": [ser.party_json(ctx, p) for p in found]})


def views_for(key: str, mixin: type | None = None) -> tuple[Any, Any, Any]:
    """Concrete views of one resource; ``mixin`` (first base) swaps the registry/serializer (CRM)."""
    attrs: dict[str, Any] = {"key": key}
    name = key.replace("/", "_").replace("-", "_").title()

    def make(kind: str, base: type) -> Any:
        bases = (mixin, base) if mixin else (base,)
        view: Any = type(f"{name}{kind}", bases, attrs)
        return view.as_view()

    return make("Collection", CollectionView), make("Item", ItemView), make("Action", ActionView)
