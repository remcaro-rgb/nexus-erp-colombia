"""Supplier portal API (``/api/portal/v1/supplier/*``, F-PORTAL-SUP; contract ``portal.yaml``; owner P11, part P11b).

Security (D-018, D-033, F-PORTAL-CORE) through :class:`iam.external.portal.PortalView`: ``erp-portal`` tokens of
ACTIVE supplier accounts only; the supplier is the purchasing supplier linked to the account's party in the
account's company — never a value of the request (scope ids in the query/body are refused,
``external_account_scope_violation``). Reads filter by that supplier in the repository layer
(:mod:`purchasing.portal.service`); a foreign id is a 404 (no existence oracle). Responses are field allowlists
(``portal_fields``): never other vendors' quotes or prices, comparison scores, landed costs, internal notes or
costs (T-PORTAL-SUP-04). Every write is idempotent (``Idempotency-Key``) and audited.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import parse_uuid, to_json
from iam.external.portal import PortalView, own_account_id
from kernel import clock, tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.money import q_amount
from kernel.uow import UnitOfWork
from purchasing.models import ApOpenItem, PurchaseOrder, Supplier, VendorQuote
from purchasing.portal import crypto, service
from purchasing.portal.models import SupplierDocument, SupplierInvoiceUpload
from purchasing.requisitions.common import reject_unknown

ACTION_KEYS = ("reason", "comment", "effective_date", "parameters")


def _money(value: Any, currency: str) -> dict[str, str]:
    return {"amount": str(q_amount(Decimal(value))), "currency": currency}


def _audit_stamp(obj: Any) -> dict[str, Any]:
    created = getattr(obj, "created_at", None)
    return {"created_at": created.isoformat() if created else None, "channel": "portal"}


class SupplierPortalView(PortalView):
    portal_kind = "supplier"
    permissions: ClassVar[dict[str, str]] = {}

    def supplier(self) -> Supplier:
        portal = self.effective.portal
        assert portal is not None  # noqa: S101 - PortalView.check_access guarantees it
        return service.supplier_of(portal.company_id, portal.party_id)

    def action(self, request: Request) -> tuple[str, dict[str, Any], str]:
        data = request.data if isinstance(request.data, Mapping) else {}
        reject_unknown(data, ACTION_KEYS)
        params = data.get("parameters") or {}
        if not isinstance(params, Mapping):
            raise ValidationFailed("parameters must be an object", field="parameters")
        reason = data.get("reason") or ""
        comment = data.get("comment") or ""
        if not isinstance(reason, str) or not isinstance(comment, str) or len(reason) > 2000 or len(comment) > 2000:
            raise ValidationFailed("reason/comment must be texts of at most 2000 characters", field="reason")
        return reason, dict(params), comment

    def body(self, request: Request) -> Mapping[str, Any]:
        data = request.data if isinstance(request.data, Mapping) else {}
        return {k: v for k, v in data.items() if k not in self.ignored_params}

    def detail(self, queryset: Any, pk: str) -> Response:
        with tenancy.scope(self.ctx):
            obj = queryset.filter(pk=parse_uuid(pk)).first()
            if obj is None:
                raise NotFound("not found", code="portal_document_not_owned")
            return Response(self.serialize(obj))

    def own_list(self, request: Request, build: Any) -> Response:
        with tenancy.scope(self.ctx):
            qs = build(self.supplier())
        return self.paginate(request, qs)


# --------------------------------------------------------------------------- profile


class ProfileView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.supplier_profile.read"}
    portal_fields = (
        "id",
        "tenant_id",
        "legal_name",
        "nit",
        "bank_account",
        "contacts",
        "pending_change",
        "version",
        "audit",
    )

    def to_dict(self, s: Any) -> dict[str, Any]:
        row = service.master_data(s)
        return {
            "id": s.id,
            "tenant_id": s.tenant_id,
            "legal_name": s.name,
            "nit": s.nit,
            "bank_account": crypto.masked(row.bank_account_last4) if row else None,
            "contacts": dict(row.contacts) if row else {},
            "pending_change": service.pending_change(s) is not None,
            "version": row.version if row else 1,
            "audit": _audit_stamp(s),
        }

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            return Response(self.serialize(self.supplier()))


class ProfileChangeView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {"POST": "portal.supplier_profile.create"}
    portal_fields = ("status", "resource", "data", "warnings")

    def post(self, request: Request) -> Response:
        reason, params, _comment = self.action(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            change = service.submit_profile_change(uow, self.supplier(), own_account_id(self), params, reason)
            return {
                "status": change.status,
                "resource": {"type": "purchasing.supplier_profile_change", "id": str(change.id), "number": None},
                "data": {"required_approvals": change.required_approvals, "fields": sorted(change.requested)},
                "warnings": [],
            }, 200

        return self.mutate(request, run, scope="portal.supplier_profile.create")


# --------------------------------------------------------------------------- documents


class DocumentsView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": "portal.supplier_document.read",
        "POST": "portal.supplier_document.create",
    }
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "document_type",
        "file_name",
        "expires_on",
        "review_status",
        "version",
        "audit",
    )

    def to_dict(self, d: Any) -> dict[str, Any]:
        return {
            "id": d.id,
            "tenant_id": d.tenant_id,
            "status": d.review_status,
            "document_type": d.document_type,
            "file_name": d.file_name,
            "expires_on": d.expires_on,
            "review_status": d.review_status,
            "version": d.version,
            "audit": _audit_stamp(d),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            with tenancy.scope(self.ctx):
                qs = SupplierDocument.objects.filter(supplier_id=self.supplier().id)
            return self.detail(qs, pk)
        return self.own_list(request, lambda s: SupplierDocument.objects.filter(supplier_id=s.id))

    def post(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request)
        return self.mutate(
            request,
            lambda uow: (self.serialize(service.create_document(uow, self.supplier(), data)), 201),
            scope="portal.supplier_document.create",
        )


# --------------------------------------------------------------------------- RFQs and quotes


class RfqsView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.supplier_rfq.read"}
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "number",
        "deadline",
        "rfq_status",
        "currency",
        "lines",
        "version",
        "audit",
    )

    def to_dict(self, r: Any) -> dict[str, Any]:
        return {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "status": r.status,
            "number": r.number,
            "deadline": r.deadline,
            "rfq_status": "OPEN" if service.rfq_open(r) else "CLOSED",
            "currency": r.currency,
            # what to quote (public to the invited vendor): item, description, requested quantity
            "lines": [
                {
                    "line_no": ln.line_no,
                    "item_id": str(ln.item_id) if ln.item_id else None,
                    "description": ln.description,
                    "quantity": {"value": str(ln.quantity), "uom": ln.uom},
                }
                for ln in r.lines.order_by("line_no")
            ],
            "version": r.version,
            "audit": _audit_stamp(r),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            with tenancy.scope(self.ctx):
                qs = service.own_rfqs(self.supplier())
            return self.detail(qs, pk)
        return self.own_list(request, service.own_rfqs)


def quote_dict(q: VendorQuote) -> dict[str, Any]:
    return {
        "id": q.id,
        "tenant_id": q.tenant_id,
        "status": q.quote_status,
        "number": q.number,
        "currency": q.currency,
        "valid_until": q.valid_until,
        "lead_time_days": q.lead_time_days,
        # the supplier's own offer only (no landed total, score, rank or other vendors)
        "lines": [
            {
                "line_no": ln.line_no,
                "quantity": {"value": str(ln.quantity), "uom": ln.uom},
                "unit_price": _money(ln.unit_price, q.currency),
                "discount_pct": str(ln.discount_pct),
            }
            for ln in q.lines.order_by("line_no")
        ],
        "version": q.version,
        "audit": _audit_stamp(q),
    }


class QuotesView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": "portal.supplier_quote.read",
        "POST": "portal.supplier_quote.create",
    }
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "number",
        "lines",
        "currency",
        "valid_until",
        "lead_time_days",
        "version",
        "audit",
    )

    def to_dict(self, q: Any) -> dict[str, Any]:
        return quote_dict(q)

    def _quotes(self, rfq_id: str) -> Any:
        s = self.supplier()
        return service.own_quotes(s, service.own_rfq(s, parse_uuid(rfq_id)))

    def get(self, request: Request, rfq_id: str, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            qs = self._quotes(rfq_id)
        if pk is not None:
            return self.detail(qs, pk)
        return self.paginate(request, qs)

    def post(self, request: Request, rfq_id: str, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request)

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            quote = service.submit_quote(uow, self.supplier(), parse_uuid(rfq_id), data)
            return self.serialize(quote), 201

        return self.mutate(request, run, scope="portal.supplier_quote.create")


# --------------------------------------------------------------------------- purchase orders


class OrdersView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.supplier_purchase_order.read"}
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "number",
        "total",
        "acknowledged_at",
        "document_date",
        "expected_date",
        "lines",
        "answer",
        "version",
        "audit",
    )

    def to_dict(self, o: Any) -> dict[str, Any]:
        answer = service.last_answer(o)
        status = o.status
        if status == PurchaseOrder.Status.SENT and answer is not None and not answer.accepted:
            status = "CHANGE_REQUESTED"
        return {
            "id": o.id,
            "tenant_id": o.tenant_id,
            "status": status,
            "number": o.number,
            "total": _money(o.total, o.currency),
            "acknowledged_at": answer.answered_at if answer is not None and answer.accepted else None,
            "document_date": o.document_date,
            "expected_date": o.expected_date,
            # the agreed order lines of this supplier (its own prices)
            "lines": [
                {
                    "line_no": ln.line_no,
                    "item_id": str(ln.item_id) if ln.item_id else None,
                    "description": ln.description,
                    "quantity": {"value": str(ln.quantity), "uom": ln.uom},
                    "unit_price": _money(ln.unit_price, o.currency),
                    "received_qty": str(ln.received_qty),
                }
                for ln in o.lines.order_by("line_no")
            ],
            "answer": None if answer is None else {"accepted": answer.accepted, "comment": answer.comment},
            "version": o.version,
            "audit": _audit_stamp(o),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            with tenancy.scope(self.ctx):
                qs = service.own_orders(self.supplier())
            return self.detail(qs, pk)
        return self.own_list(request, service.own_orders)


class OrderAcknowledgeView(OrdersView):
    permissions: ClassVar[dict[str, str]] = {"POST": "portal.supplier_purchase_order.update"}

    def post(self, request: Request, pk: str) -> Response:
        _reason, params, comment = self.action(request)
        reject_unknown(params, ("accepted",), " in parameters")
        accepted = params.get("accepted", True)
        if not isinstance(accepted, bool):
            raise ValidationFailed("parameters.accepted must be true or false", field="parameters.accepted")

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            order = service.acknowledge(uow, self.supplier(), own_account_id(self), parse_uuid(pk), accepted, comment)
            order.refresh_from_db()
            return self.serialize(order), 200

        return self.mutate(request, run, scope="portal.supplier_purchase_order.update")


# --------------------------------------------------------------------------- shipments


class ShipmentsView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": "portal.supplier_shipment.read",
        "POST": "portal.supplier_shipment.create",
    }
    portal_fields = (
        "id",
        "tenant_id",
        "status",
        "number",
        "purchase_order_id",
        "expected_date",
        "tracking",
        "lines",
        "version",
        "audit",
    )

    def to_dict(self, n: Any) -> dict[str, Any]:
        return {
            "id": n.id,
            "tenant_id": n.tenant_id,
            "status": n.status,
            "number": n.number,
            "purchase_order_id": n.purchase_order_id,
            "expected_date": n.expected_date,
            "tracking": n.tracking,
            "lines": [
                {"line_no": ln.order_line_no, "quantity": str(ln.quantity)} for ln in n.lines.order_by("order_line_no")
            ],
            "version": n.version,
            "audit": _audit_stamp(n),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            with tenancy.scope(self.ctx):
                qs = service.own_shipments(self.supplier())
            return self.detail(qs, pk)
        return self.own_list(request, service.own_shipments)

    def post(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request)
        return self.mutate(
            request,
            lambda uow: (self.serialize(service.create_shipment(uow, self.supplier(), data)), 201),
            scope="portal.supplier_shipment.create",
        )


# --------------------------------------------------------------------------- invoices and payments


class InvoicesView(SupplierPortalView):
    permissions: ClassVar[dict[str, str]] = {
        "GET": "portal.supplier_invoice.read",
        "POST": "portal.supplier_invoice.create",
    }
    portal_fields = ("id", "tenant_id", "status", "number", "processing_status", "version", "audit")

    def to_dict(self, i: Any) -> dict[str, Any]:
        return {
            "id": i.id,
            "tenant_id": i.tenant_id,
            "status": i.processing_status,
            "number": i.number,
            "processing_status": i.processing_status,
            "version": i.version,
            "audit": _audit_stamp(i),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            with tenancy.scope(self.ctx):
                qs = SupplierInvoiceUpload.objects.filter(supplier_id=self.supplier().id)
            return self.detail(qs, pk)
        return self.own_list(request, lambda s: SupplierInvoiceUpload.objects.filter(supplier_id=s.id))

    def post(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request)
        return self.mutate(
            request,
            lambda uow: (self.serialize(service.upload_invoice(uow, self.supplier(), data)), 201),
            scope="portal.supplier_invoice.create",
        )


class PaymentsView(SupplierPortalView):
    """Own payables (P11a AP open items) with the vouchers applied to them (P16 ``PaymentStatus``)."""

    permissions: ClassVar[dict[str, str]] = {"GET": "portal.supplier_payment.read"}
    portal_fields = ("id", "tenant_id", "status", "document", "amount", "paid_on", "vouchers", "version", "audit")

    def to_dict(self, item: Any) -> dict[str, Any]:
        vouchers = [info for it, info in self._payments if it.id == item.id]
        posted = [v.posted_on for v in vouchers if v.state == "POSTED" and v.posted_on]
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "status": item.status,
            "document": {"type": item.document_type, "id": str(item.document_id), "number": item.document_number},
            "amount": _money(item.amount, item.currency),
            "paid_on": max(posted) if posted and item.status == ApOpenItem.Status.PAID else None,
            "vouchers": [
                {
                    "number": v.voucher.number,
                    "state": v.state,
                    "amount": _money(v.amount, v.currency),
                    "posted_on": v.posted_on,
                }
                for v in vouchers
            ],
            "version": item.version,
            "audit": _audit_stamp(item),
        }

    def get(self, request: Request, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            supplier = self.supplier()
            self._payments = service.own_payments(supplier)
            qs = service.own_open_items(supplier)
        if pk is not None:
            return self.detail(qs, pk)
        return self.paginate(request, qs)

    _payments: list[Any] = []


# --------------------------------------------------------------------------- dashboard KPIs


class KpisView(SupplierPortalView):
    """``GET /api/portal/v1/supplier/kpis?period=YYYY-MM`` — dashboard KPIs (CCR P11b-2: route not yet in the
    contract; same pattern as the employee portal, CCR P13a-2)."""

    permissions: ClassVar[dict[str, str]] = {"GET": "portal.supplier_profile.read"}
    portal_fields = ("as_of", "period", "kpis", "generated_at", "portal")
    ignored_params = (*PortalView.ignored_params, "period")

    def get(self, request: Request) -> Response:
        start, end = _period(request.query_params.get("period"))
        prev_end = start - dt.timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        with tenancy.scope(self.ctx):
            supplier = self.supplier()
            values = service.kpis(supplier, start, end)
            previous = service.kpis(supplier, prev_start, prev_end)["sp_pagos_periodo"]
        counts = ("sp_rfq_abiertas", "sp_oc_por_confirmar")
        items = []
        for key, value in values.items():
            flow = key == "sp_pagos_periodo"
            items.append(
                {
                    "id": key,
                    "unit": "count" if key in counts else "COP",
                    "value": str(value) if key in counts else str(q_amount(value)),
                    "previous": str(q_amount(previous)) if flow else None,
                    "delta": str(q_amount(value - previous)) if flow else None,
                    "state": "ok",
                    "semantics": "flow" if flow else "snapshot",
                    "formula_ref": "module-registry.json › portals › proveedores",
                }
            )
        body = {
            "as_of": clock.business_date(),
            "period": {"start": start, "end": end},
            "kpis": items,
            "generated_at": clock.now(),
            "portal": "proveedores",
        }
        return Response({k: to_json(v) for k, v in body.items()})


def _period(raw: str | None) -> tuple[dt.date, dt.date]:
    today = clock.business_date()
    if raw:
        try:
            year, month = (int(x) for x in raw.split("-"))
            start = dt.date(year, month, 1)
        except ValueError as exc:
            raise ValidationFailed("period must be YYYY-MM", field="period") from exc
    else:
        start = today.replace(day=1)
    nxt = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, nxt - dt.timedelta(days=1)
