"""Purchasing endpoints (contract ``contracts/openapi/modules/purchasing.yaml``, P11a core).

Each handler authorizes with the operation's ``x-permission`` (the services call ``PolicyService``),
reads inside the request's forced-RLS scope and writes in one idempotent unit of work.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from kernel import clock
from kernel.errors import NotFound, ValidationFailed
from kernel.uow import UnitOfWork
from purchasing.api import serializers as ser
from purchasing.api.base import PurchasingView, action_body, body, check_if_match, command, idempotency_key, page
from purchasing.models import ApOpenItem, PurchaseOrder, Receipt, Rfq, VendorInvoice, VendorQuote
from purchasing.services import ap, attachments, invoices, orders, receipts, rfq
from purchasing.services.common import get_scoped, scoped


def _etag(model: Any) -> Callable[[Any], str | None]:
    def tag(data: Any) -> str | None:
        if isinstance(data, dict) and "id" in data and "version" in data:
            obj = model(pk=uuid.UUID(data["id"]), version=data["version"])
            return str(obj.etag)
        return None

    return tag


def _date_param(request: Request, name: str) -> dt.date:
    raw = request.query_params.get(name)
    if not raw:
        return clock.business_date()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationFailed(f"{name} must be a date (YYYY-MM-DD)", field=name) from exc


# --------------------------------------------------------------------------- RFQs


class RfqList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.rfq.read", Rfq.objects.filter(company_id=company))
        return page(request, qs, ser.rfq_json, {"status": "status", "number": "number"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            created, warnings = rfq.create_rfq(uow, company, body(request))
            return ser.rfq_json(created, warnings), 201

        return command(request, ctx, "purchasing.rfq.create", fn, etag=_etag(Rfq))


class RfqDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.rfq.read", Rfq.objects.filter(company_id=company), pk)
        response = Response(ser.rfq_json(obj))
        response["ETag"] = obj.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.rfq.update", Rfq.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            "purchasing.rfq.update",
            lambda uow: (ser.rfq_json(rfq.update_rfq(uow, obj, body(request))), 200),
            etag=_etag(Rfq),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.rfq.delete", Rfq.objects.filter(company_id=company), pk)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            rfq.delete_rfq(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.rfq.delete", fn)


class RfqAction(PurchasingView):
    PERMISSIONS = {
        "send": "purchasing.rfq.update",
        "close": "purchasing.rfq.update",
        "reopen": "purchasing.rfq.update",
        "evaluate": "purchasing.rfq.update",
        "award": "purchasing.rfq.approve",
        "approve-award": "purchasing.rfq.approve",
    }

    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        permission = self.PERMISSIONS.get(action)
        if permission is None:
            raise NotFound("not found")
        obj = get_scoped(ctx, permission, Rfq.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            target = Rfq.objects.select_for_update().get(pk=obj.pk)
            if action == "send":
                rfq.send_rfq(uow, target)
            elif action == "close":
                rfq.close_rfq(uow, target)
            elif action == "reopen":
                rfq.reopen_rfq(uow, target, reason, params.get("deadline"))
            elif action == "evaluate":
                rfq.evaluate_rfq(uow, target)
            elif action == "award":
                rfq.award_rfq(uow, target, {**params, "reason": reason})
            else:
                approve = params.get("approve", True)
                if not isinstance(approve, bool):
                    raise ValidationFailed("parameters.approve must be true or false", field="parameters.approve")
                rfq.approve_award(uow, target, approve, reason)
            target.refresh_from_db()
            return ser.rfq_json(target), 200

        return command(request, ctx, f"purchasing.rfq.{action}", fn, etag=_etag(Rfq))


class RfqComparison(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.rfq.read", Rfq.objects.filter(company_id=company), pk)
        snapshot = rfq.latest_comparison(obj)
        if snapshot is None:
            raise NotFound("the RFQ has not been evaluated yet")
        return Response(ser.comparison_json(snapshot))


# --------------------------------------------------------------------------- quotes


def _rfq_for_quotes(ctx: Any, company: uuid.UUID, rfq_id: uuid.UUID, action: str) -> Rfq:
    return get_scoped(ctx, action, Rfq.objects.filter(company_id=company), rfq_id)  # type: ignore[no-any-return]


class QuoteList(PurchasingView):
    def get(self, request: Request, rfq_id: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        parent = _rfq_for_quotes(ctx, company, rfq_id, "purchasing.quote.read")
        qs = scoped(ctx, "purchasing.quote.read", VendorQuote.objects.filter(rfq=parent)).select_related("supplier")
        return page(request, qs, lambda q: ser.quote_json(ctx, q), {"quote_status": "quote_status"})

    def post(self, request: Request, rfq_id: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        parent = _rfq_for_quotes(ctx, company, rfq_id, "purchasing.quote.create")

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            target = Rfq.objects.select_for_update().get(pk=parent.pk)
            quote, warnings = rfq.create_quote(uow, target, body(request))
            return ser.quote_json(ctx, quote, warnings), 201

        return command(request, ctx, "purchasing.quote.create", fn, etag=_etag(VendorQuote))


class QuoteDetail(PurchasingView):
    def _get(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID, action: str) -> VendorQuote:
        ctx, company = self.ctx_company(request)
        parent = _rfq_for_quotes(ctx, company, rfq_id, action)
        return get_scoped(ctx, action, VendorQuote.objects.filter(rfq=parent).select_related("supplier"), pk)  # type: ignore[no-any-return]

    def get(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID) -> Response:
        quote = self._get(request, rfq_id, pk, "purchasing.quote.read")
        response = Response(ser.quote_json(self.context(), quote))
        response["ETag"] = quote.etag
        return response

    def patch(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID) -> Response:
        quote = self._get(request, rfq_id, pk, "purchasing.quote.update")
        check_if_match(request, quote, required=True)
        ctx = self.context()
        return command(
            request,
            ctx,
            "purchasing.quote.update",
            lambda uow: (ser.quote_json(ctx, rfq.update_quote(uow, quote, body(request))), 200),
            etag=_etag(VendorQuote),
        )

    def delete(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID) -> Response:
        quote = self._get(request, rfq_id, pk, "purchasing.quote.delete")
        ctx = self.context()

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            rfq.withdraw_quote(uow, quote)
            return None, 204

        return command(request, ctx, "purchasing.quote.delete", fn)


class QuoteAttachments(PurchasingView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        items = attachments.list_attachments(ctx, company, rfq_id, pk)
        return Response({"items": [attachments.to_json(a) for a in items], "next_cursor": None})

    def post(self, request: Request, rfq_id: uuid.UUID, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed("multipart field 'file' is required", field="file")
        content = upload.read()
        att, replayed = attachments.add_attachment(
            ctx,
            company,
            rfq_id,
            pk,
            upload.name or "adjunto",
            content,
            upload.content_type or "application/octet-stream",
            idempotency_key=idempotency_key(request),
        )
        data = att if isinstance(att, dict) else attachments.to_json(att)
        response = Response(data, status=201)
        response["Idempotent-Replayed"] = "true" if replayed else "false"
        return response


# --------------------------------------------------------------------------- orders


class OrderList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.order.read", PurchaseOrder.objects.filter(company_id=company))
        qs = qs.select_related("supplier", "rfq")
        return page(
            request,
            qs,
            lambda o: ser.order_json(ctx, o),
            {"status": "status", "number": "number", "party_id": "supplier_id"},
        )

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            "purchasing.order.create",
            lambda uow: (ser.order_json(ctx, orders.create_order(uow, company, body(request))), 201),
            etag=_etag(PurchaseOrder),
        )


class OrderDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.order.read", PurchaseOrder.objects.filter(company_id=company), pk)
        response = Response(ser.order_json(ctx, obj))
        response["ETag"] = obj.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.order.update", PurchaseOrder.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            "purchasing.order.update",
            lambda uow: (ser.order_json(ctx, orders.update_order(uow, obj, body(request))), 200),
            etag=_etag(PurchaseOrder),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.order.delete", PurchaseOrder.objects.filter(company_id=company), pk)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            orders.delete_order(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.order.delete", fn)


class OrderAction(PurchasingView):
    PERMISSIONS = {
        "submit": "purchasing.order.update",
        "approve": "purchasing.order.approve",
        "send": "purchasing.order.update",
        "close": "purchasing.order.update",
        "cancel": "purchasing.order.void",
    }

    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        permission = self.PERMISSIONS.get(action)
        if permission is None:
            raise NotFound("not found")
        obj = get_scoped(ctx, permission, PurchaseOrder.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            target = PurchaseOrder.objects.select_for_update().get(pk=obj.pk)
            if action == "submit":
                orders.submit_order(uow, target)
            elif action == "approve":
                approve = params.get("approve", True)
                if not isinstance(approve, bool):
                    raise ValidationFailed("parameters.approve must be true or false", field="parameters.approve")
                orders.approve_order(uow, target, approve, reason)
            elif action == "send":
                orders.send_order(uow, target)
            elif action == "close":
                orders.close_order(uow, target, reason)
            else:
                orders.cancel_order(uow, target, reason)
            target.refresh_from_db()
            return ser.order_json(ctx, target), 200

        return command(request, ctx, f"purchasing.order.{action}", fn, etag=_etag(PurchaseOrder))


# --------------------------------------------------------------------------- receipts


class ReceiptList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.receipt.read", Receipt.objects.filter(company_id=company))
        qs = qs.select_related("order__supplier")
        return page(request, qs, lambda r: ser.receipt_json(ctx, r), {"status": "status", "order_id": "order_id"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            "purchasing.receipt.create",
            lambda uow: (ser.receipt_json(ctx, receipts.create_receipt(uow, company, body(request))), 201),
            etag=_etag(Receipt),
        )


class ReceiptDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.receipt.read", Receipt.objects.filter(company_id=company), pk)
        response = Response(ser.receipt_json(ctx, obj))
        response["ETag"] = obj.etag
        return response

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.receipt.delete", Receipt.objects.filter(company_id=company), pk)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            receipts.delete_receipt(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.receipt.delete", fn)


class ReceiptAction(PurchasingView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        if action == "simulate":
            obj = get_scoped(ctx, "purchasing.receipt.read", Receipt.objects.filter(company_id=company), pk)
            return command(
                request, ctx, "purchasing.receipt.simulate", lambda uow: (receipts.simulate_receipt(uow, obj), 200)
            )
        if action != "validate":
            raise NotFound("not found")
        obj = get_scoped(ctx, "purchasing.receipt.update", Receipt.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            done = receipts.validate_receipt(uow, obj)
            return ser.receipt_json(ctx, done), 200

        return command(request, ctx, "purchasing.receipt.validate", fn, etag=_etag(Receipt))


# --------------------------------------------------------------------------- vendor invoices


class InvoiceList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.vendor_invoice.read", VendorInvoice.objects.filter(company_id=company))
        qs = qs.select_related("supplier", "order")
        return page(
            request,
            qs,
            lambda v: ser.invoice_json(ctx, v),
            {"status": "status", "match_status": "match_status", "party_id": "supplier_id"},
        )

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            "purchasing.vendor_invoice.create",
            lambda uow: (ser.invoice_json(ctx, invoices.create_invoice(uow, company, body(request))), 201),
            etag=_etag(VendorInvoice),
        )


class InvoiceDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.vendor_invoice.read", VendorInvoice.objects.filter(company_id=company), pk)
        response = Response(ser.invoice_json(ctx, obj))
        response["ETag"] = obj.etag
        return response

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.vendor_invoice.delete", VendorInvoice.objects.filter(company_id=company), pk)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            invoices.delete_invoice(uow, obj)
            return None, 204

        return command(request, ctx, "purchasing.vendor_invoice.delete", fn)


class InvoiceAction(PurchasingView):
    PERMISSIONS = {
        "match": "purchasing.vendor_invoice.update",
        "post": "purchasing.vendor_invoice.post",
        "simulate": "purchasing.vendor_invoice.read",
    }

    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        permission = self.PERMISSIONS.get(action)
        if permission is None:
            raise NotFound("not found")
        obj = get_scoped(ctx, permission, VendorInvoice.objects.filter(company_id=company), pk)
        if action == "simulate":
            return command(
                request,
                ctx,
                "purchasing.vendor_invoice.simulate",
                lambda uow: (invoices.simulate_invoice(uow, obj), 200),
            )
        check_if_match(request, obj, required=False)
        action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            done = invoices.match_invoice(uow, obj) if action == "match" else invoices.post_invoice(uow, obj)
            done.refresh_from_db()
            return ser.invoice_json(ctx, done), 200

        return command(request, ctx, f"purchasing.vendor_invoice.{action}", fn, etag=_etag(VendorInvoice))


# --------------------------------------------------------------------------- AP / reports


class OpenItemList(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "purchasing.ap_open_item.read", ApOpenItem.objects.filter(company_id=company))
        qs = qs.select_related("supplier")
        return page(request, qs, lambda i: ser.open_item_json(ctx, i), {"status": "status", "party_id": "supplier_id"})


class OpenItemDetail(PurchasingView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "purchasing.ap_open_item.read", ApOpenItem.objects.filter(company_id=company), pk)
        return Response(ser.open_item_json(ctx, obj))


class Aging(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        party = request.query_params.get("party_id")
        try:
            supplier = uuid.UUID(party) if party else None
        except ValueError as exc:
            raise ValidationFailed("party_id must be a UUID", field="party_id") from exc
        return Response(ap.aging(ctx, company, _date_param(request, "as_of"), supplier))


class GrniReport(PurchasingView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return Response(ap.grni(ctx, company, _date_param(request, "as_of")))
