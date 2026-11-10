"""F-PORTAL-SUP services: the supplier of a portal principal, profile changes with internal approval, quotes,
PO acknowledgements, shipment notices, invoice uploads, payments and the dashboard KPIs.

Security (D-018, D-033): the supplier comes **only** from the portal account (party + company of the token), never
from the request body; every read filters by that supplier in the repository layer. The portal never posts: uploads
create items for internal review. Internal decisions (profile changes) need ``purchasing.supplier_profile_change.*``
and segregation of duties: a bank-account change needs two different internal approvers before the vendor master
changes (T-PORTAL-SUP-03).
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction

from kernel import clock, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.treasury import PaymentStatus, PaymentStatusInfo
from kernel.uow import UnitOfWork
from purchasing import interfaces as purchasing_contract
from purchasing.models import ApOpenItem, PurchaseOrder, PurchaseOrderLine, Rfq, Supplier, VendorQuote
from purchasing.portal import crypto
from purchasing.portal.bridge import portal_resources
from purchasing.portal.models import (
    PoAcknowledgement,
    ProfileChangeApproval,
    ShipmentNotice,
    ShipmentNoticeLine,
    SupplierDocument,
    SupplierInvoiceUpload,
    SupplierMasterData,
    SupplierProfileChange,
)
from purchasing.requisitions import common as c
from purchasing.requisitions.errors import (
    InvalidStateTransition,
    PortalProfileChangePending,
    PortalRfqClosed,
    ReasonRequired,
    SodViolation,
)

ZERO = Decimal(0)
VISIBLE_RFQ = (
    Rfq.Status.SENT,
    Rfq.Status.CLOSED,
    Rfq.Status.EVALUATED,
    Rfq.Status.AWARD_PENDING_APPROVAL,
    Rfq.Status.AWARDED,
    Rfq.Status.PO_CREATED,
    Rfq.Status.CANCELLED,
)
VISIBLE_PO = (
    PurchaseOrder.Status.SENT,
    PurchaseOrder.Status.ACKNOWLEDGED,
    PurchaseOrder.Status.PARTIALLY_RECEIVED,
    PurchaseOrder.Status.RECEIVED,
    PurchaseOrder.Status.CLOSED,
    PurchaseOrder.Status.CANCELLED,
)
SHIPPABLE_PO = (PurchaseOrder.Status.SENT, PurchaseOrder.Status.ACKNOWLEDGED, PurchaseOrder.Status.PARTIALLY_RECEIVED)
PROFILE_FIELDS = ("bank_account", "bank_name", "contacts", "tax_data")
DOCUMENT_TYPES = ("RUT", "CERTIFICATION", "BANK_CERTIFICATE", "CHAMBER_OF_COMMERCE", "OTHER")
MAX_XML_BYTES = 2 * 1024 * 1024
BANK_ACCOUNT_RE = re.compile(r"^[0-9]{6,20}$")


# --------------------------------------------------------------------------- the portal principal's supplier


def supplier_of(company_id: uuid.UUID, party_id: uuid.UUID | None) -> Supplier:
    """The purchasing supplier linked to the portal party in the portal company (404 if none)."""
    if party_id is None:
        raise NotFound("not found", code="portal_document_not_owned")
    supplier = Supplier.objects.filter(company_id=company_id, party_id=party_id).first()
    if supplier is None:
        raise NotFound("not found", code="portal_document_not_owned")
    return supplier


def master_data(supplier: Supplier) -> SupplierMasterData | None:
    return SupplierMasterData.objects.filter(company_id=supplier.company_id, supplier_id=supplier.id).first()


def pending_change(supplier: Supplier) -> SupplierProfileChange | None:
    return SupplierProfileChange.objects.filter(
        company_id=supplier.company_id, supplier_id=supplier.id, status=SupplierProfileChange.Status.SUBMITTED
    ).first()


# --------------------------------------------------------------------------- profile changes (T-PORTAL-SUP-03)


def submit_profile_change(
    uow: UnitOfWork, supplier: Supplier, account_id: uuid.UUID, params: Mapping[str, Any], reason: str
) -> SupplierProfileChange:
    c.reject_unknown(params, PROFILE_FIELDS, " in parameters")
    if not params:
        raise ValidationFailed("name at least one of bank_account, bank_name, contacts, tax_data", field="parameters")
    if pending_change(supplier) is not None:
        raise PortalProfileChangePending("a profile change is already waiting for approval")
    requested: dict[str, Any] = {}
    bank_enc, last4 = None, ""
    if "bank_account" in params:
        number = str(params["bank_account"] or "").replace(" ", "").replace("-", "")
        if not BANK_ACCOUNT_RE.match(number):
            raise ValidationFailed("bank_account must have 6 to 20 digits", field="parameters.bank_account")
        bank_enc, last4 = crypto.encrypt(number), number[-4:]
        requested["bank_account"] = crypto.masked(last4)
    if "bank_name" in params:
        requested["bank_name"] = c.text_in(params["bank_name"], "parameters.bank_name", 120)
    for key in ("contacts", "tax_data"):
        if key in params:
            if not isinstance(params[key], Mapping):
                raise ValidationFailed(f"{key} must be an object", field=f"parameters.{key}")
            requested[key] = dict(params[key])
    try:
        with transaction.atomic():
            change = SupplierProfileChange.objects.create(
                tenant_id=uow.ctx.tenant_id,
                company_id=supplier.company_id,
                supplier_id=supplier.id,
                party_id=supplier.party_id,
                external_account_id=account_id,
                requested=requested,
                bank_account_enc=bank_enc,
                bank_account_last4=last4,
                required_approvals=2 if bank_enc else 1,
                decision_reason=c.text_in(reason, "reason", 2000),
                created_by=uow.ctx.principal_id,
                updated_by=uow.ctx.principal_id,
            )
    except IntegrityError as exc:  # the partial unique index lost a race with another submission
        raise PortalProfileChangePending("a profile change is already waiting for approval") from exc
    c.audit(
        uow,
        "purchasing.supplier_profile_change.submit",
        "purchasing.supplier_profile_change",
        change.id,
        supplier.company_id,
        {"fields": sorted(requested), "channel": "portal"},
    )
    return change


def decide_profile_change(
    uow: UnitOfWork, change: SupplierProfileChange, approve: bool, reason: str
) -> SupplierProfileChange:
    """Internal decision. Rejection ends it; approvals accumulate (bank: two different approvers, SoD)."""
    ctx = uow.ctx
    c.require(ctx, "purchasing.supplier_profile_change.approve", change)
    if change.status != SupplierProfileChange.Status.SUBMITTED:
        raise InvalidStateTransition(f"the profile change is {change.status}")
    if not approve and not reason.strip():
        raise ReasonRequired("a reason is required to reject", field="reason")
    if ctx.principal_id in (change.created_by, change.external_account_id):
        raise SodViolation("the requester cannot decide the own change")
    if change.approvals.filter(approver_id=ctx.principal_id).exists():
        raise SodViolation("a second approval must come from a different person")
    now = clock.now()
    ProfileChangeApproval.objects.create(
        tenant_id=change.tenant_id,
        company_id=change.company_id,
        change=change,
        approver_id=ctx.principal_id,
        approve=approve,
        reason=reason,
        decided_at=now,
    )
    if not approve:
        change.status = SupplierProfileChange.Status.REJECTED
    elif change.approvals.filter(approve=True).count() >= change.required_approvals:
        change.status = SupplierProfileChange.Status.APPROVED
        _apply(uow, change)
    if change.status != SupplierProfileChange.Status.SUBMITTED:
        change.decided_at = now
        change.decision_reason = reason
    change.save_versioned(ctx.principal_id, ["status", "decided_at", "decision_reason"])
    c.audit(
        uow,
        f"purchasing.supplier_profile_change.{'approve' if approve else 'reject'}",
        "purchasing.supplier_profile_change",
        change.id,
        change.company_id,
        {"status": change.status, "approvals": change.approvals.count()},
    )
    return change


def _apply(uow: UnitOfWork, change: SupplierProfileChange) -> None:
    row = (
        SupplierMasterData.objects.select_for_update()
        .filter(company_id=change.company_id, supplier_id=change.supplier_id)
        .first()
    )
    if row is None:
        row = SupplierMasterData(
            tenant_id=change.tenant_id,
            company_id=change.company_id,
            supplier_id=change.supplier_id,
            party_id=change.party_id,
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
    if change.bank_account_enc:
        row.bank_account_enc = change.bank_account_enc
        row.bank_account_last4 = change.bank_account_last4
    if "bank_name" in change.requested:
        row.tax_data = {**row.tax_data, "bank_name": change.requested["bank_name"]}
    if "contacts" in change.requested:
        row.contacts = dict(change.requested["contacts"])
    if "tax_data" in change.requested:
        row.tax_data = {**row.tax_data, **change.requested["tax_data"]}
    if row._state.adding:
        row.save()
    else:
        row.save_versioned(uow.ctx.principal_id, ["bank_account_enc", "bank_account_last4", "contacts", "tax_data"])


def pending_profile_changes(uow: UnitOfWork, company_id: uuid.UUID) -> list[SupplierProfileChange]:
    """Internal inbox of SUBMITTED changes (``purchasing.supplier_profile_change.read``)."""
    c.require(uow.ctx, "purchasing.supplier_profile_change.read", {"company_id": company_id})
    return list(
        SupplierProfileChange.objects.filter(
            company_id=company_id, status=SupplierProfileChange.Status.SUBMITTED
        ).order_by("created_at")
    )


def bank_account_of(uow: UnitOfWork, supplier: Supplier) -> str | None:
    """Clear bank account for payment files (P16) — confidential: ``purchasing.supplier.update`` only."""
    c.require(uow.ctx, "purchasing.supplier.update", supplier)
    row = master_data(supplier)
    return crypto.decrypt(row.bank_account_enc) if row and row.bank_account_enc else None


# --------------------------------------------------------------------------- RFQs and quotes (T-PORTAL-SUP-01)


def own_rfqs(supplier: Supplier) -> Any:
    return Rfq.objects.filter(
        company_id=supplier.company_id, invited_vendors__contains=[str(supplier.id)], status__in=VISIBLE_RFQ
    )


def own_rfq(supplier: Supplier, rfq_id: uuid.UUID) -> Rfq:
    rfq: Rfq | None = own_rfqs(supplier).filter(id=rfq_id).first()
    if rfq is None:
        raise NotFound("not found", code="portal_document_not_owned")
    return rfq


def rfq_open(rfq: Rfq) -> bool:
    return bool(rfq.status == Rfq.Status.SENT and rfq.deadline >= clock.now())


def own_quotes(supplier: Supplier, rfq: Rfq) -> Any:
    return VendorQuote.objects.filter(rfq=rfq, supplier_id=supplier.id)


QUOTE_LINE_FIELDS = ("line_no", "quantity", "unit_price", "discount_pct", "tax_code", "lead_time_days", "notes")


def submit_quote(uow: UnitOfWork, supplier: Supplier, rfq_id: uuid.UUID, data: Mapping[str, Any]) -> VendorQuote:
    c.reject_unknown(data, ("lines", "currency", "valid_until", "lead_time_days"))
    rfq = own_rfq(supplier, rfq_id)
    if not rfq_open(rfq):
        raise PortalRfqClosed(f"the RFQ {rfq.number} is closed for quotes (deadline {rfq.deadline.isoformat()})")
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValidationFailed("lines must be a non-empty list", field="lines")
    for i, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        c.reject_unknown(line, QUOTE_LINE_FIELDS, f" in lines[{i}]")
    body: dict[str, Any] = {"currency": data.get("currency"), "lines": [dict(x) for x in lines]}
    if data.get("lead_time_days") is not None:
        body["lead_time_days"] = data["lead_time_days"]
    if data.get("valid_until"):
        body["valid_until"] = data["valid_until"]
    with portal_resources(supplier):
        quote, _warnings = purchasing_contract.portal_submit_quote(uow, rfq.id, supplier.id, body)
    return quote


# --------------------------------------------------------------------------- orders (T-PORTAL-SUP-02)


def own_orders(supplier: Supplier) -> Any:
    return PurchaseOrder.objects.filter(company_id=supplier.company_id, supplier_id=supplier.id, status__in=VISIBLE_PO)


def own_order(supplier: Supplier, order_id: uuid.UUID) -> PurchaseOrder:
    order: PurchaseOrder | None = own_orders(supplier).filter(id=order_id).first()
    if order is None:
        raise NotFound("not found", code="portal_document_not_owned")
    return order


def last_answer(order: PurchaseOrder) -> PoAcknowledgement | None:
    return (
        PoAcknowledgement.objects.filter(company_id=order.company_id, purchase_order_id=order.id)
        .order_by("-answered_at")
        .first()
    )


def acknowledge(
    uow: UnitOfWork, supplier: Supplier, account_id: uuid.UUID, order_id: uuid.UUID, accepted: bool, comment: str
) -> PurchaseOrder:
    """SENT → ACKNOWLEDGED (P11a contract) or a change request (the order stays SENT for the buyer to revise)."""
    order = own_order(supplier, order_id)
    if order.status != PurchaseOrder.Status.SENT:
        raise InvalidStateTransition(f"an order in status {order.status} cannot be acknowledged")
    if not accepted and not comment.strip():
        raise ReasonRequired("describe the changes you request", field="comment")
    if accepted:
        with portal_resources(supplier):
            order = purchasing_contract.portal_acknowledge_order(uow, order.id, supplier.id)
    PoAcknowledgement.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=order.company_id,
        supplier_id=supplier.id,
        party_id=supplier.party_id,
        external_account_id=account_id,
        purchase_order_id=order.id,
        accepted=accepted,
        comment=c.text_in(comment, "comment", 2000),
        answered_at=clock.now(),
    )
    c.audit(
        uow,
        "purchasing.order.portal_answer",
        "purchasing.order",
        order.id,
        order.company_id,
        {"accepted": accepted, "channel": "portal"},
    )
    return order


# --------------------------------------------------------------------------- shipment notices (receiving)


def own_shipments(supplier: Supplier) -> Any:
    return ShipmentNotice.objects.filter(company_id=supplier.company_id, supplier_id=supplier.id)


def create_shipment(uow: UnitOfWork, supplier: Supplier, data: Mapping[str, Any]) -> ShipmentNotice:
    c.reject_unknown(data, ("purchase_order_id", "expected_date", "lines", "tracking"))
    order = own_order(supplier, c.uuid_req(data.get("purchase_order_id"), "purchase_order_id"))
    if order.status not in SHIPPABLE_PO:
        raise InvalidStateTransition(f"an order in status {order.status} cannot get a shipment notice")
    expected = c.date_in(data.get("expected_date"), "expected_date")
    raw = data.get("lines")
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("lines must be a non-empty list", field="lines")
    open_qty = {ln.line_no: ln.quantity - ln.received_qty for ln in PurchaseOrderLine.objects.filter(order_id=order.id)}
    announced: dict[int, Decimal] = {}
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        c.reject_unknown(line, ("line_no", "quantity"), f" in lines[{i}]")
        no = line.get("line_no")
        if not isinstance(no, int) or isinstance(no, bool) or no not in open_qty or no in announced:
            raise ValidationFailed(f"lines[{i}].line_no is not an open line of the order", field="lines")
        qty, _uom = c.qty_in(line.get("quantity"), f"lines[{i}].quantity")
        if qty > open_qty[no]:
            raise ValidationFailed(f"lines[{i}].quantity exceeds the open quantity {open_qty[no]}", field="lines")
        announced[no] = qty
    notice = ShipmentNotice.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=order.company_id,
        number=c.next_number(uow, order.company_id, "shipment_notice", clock.business_date()),
        supplier_id=supplier.id,
        party_id=supplier.party_id,
        purchase_order_id=order.id,
        expected_date=expected,
        tracking=c.text_in(data.get("tracking"), "tracking", 255),
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    for no, qty in sorted(announced.items()):
        ShipmentNoticeLine.objects.create(
            tenant_id=notice.tenant_id,
            company_id=notice.company_id,
            notice=notice,
            party_id=supplier.party_id,
            order_line_no=no,
            quantity=qty,
        )
    c.audit(
        uow,
        "purchasing.shipment_notice.create",
        "purchasing.shipment_notice",
        notice.id,
        order.company_id,
        {"order": order.number, "channel": "portal"},
    )
    return notice


def notices_for_receiving(
    uow: UnitOfWork, company_id: uuid.UUID, order_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """What receiving sees: announced shipments (PO, expected date, tracking, lines) — ``shipment_notice.read``."""
    c.require(uow.ctx, "purchasing.shipment_notice.read", {"company_id": company_id})
    qs = ShipmentNotice.objects.filter(company_id=company_id, status=ShipmentNotice.Status.ANNOUNCED)
    if order_id is not None:
        qs = qs.filter(purchase_order_id=order_id)
    out = []
    for n in qs.order_by("expected_date", "number"):
        order = PurchaseOrder.objects.filter(id=n.purchase_order_id).first()
        out.append(
            {
                "id": str(n.id),
                "number": n.number,
                "purchase_order_id": str(n.purchase_order_id),
                "purchase_order_number": order.number if order else "",
                "supplier_id": str(n.supplier_id),
                "expected_date": n.expected_date.isoformat(),
                "tracking": n.tracking,
                "lines": [
                    {"line_no": ln.order_line_no, "quantity": str(ln.quantity)}
                    for ln in n.lines.order_by("order_line_no")
                ],
            }
        )
    return out


# --------------------------------------------------------------------------- documents and invoice uploads


def create_document(uow: UnitOfWork, supplier: Supplier, data: Mapping[str, Any]) -> SupplierDocument:
    c.reject_unknown(data, ("document_type", "file_name", "expires_on"))
    kind = str(data.get("document_type") or "")
    if kind not in DOCUMENT_TYPES:
        raise ValidationFailed(f"document_type must be one of {', '.join(DOCUMENT_TYPES)}", field="document_type")
    name = c.text_in(data.get("file_name"), "file_name", 255)
    if not name.strip():
        raise ValidationFailed("file_name is required", field="file_name")
    return SupplierDocument.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=supplier.company_id,
        supplier_id=supplier.id,
        party_id=supplier.party_id,
        document_type=kind,
        file_name=name,
        expires_on=c.date_in(data["expires_on"], "expires_on") if data.get("expires_on") else None,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )


def upload_invoice(uow: UnitOfWork, supplier: Supplier, data: Mapping[str, Any]) -> SupplierInvoiceUpload:
    """An electronic invoice file for P11 ingestion: stored as a DRAFT item (hash + size), never posted here."""
    c.reject_unknown(data, ("number", "xml_file"))
    number = c.text_in(data.get("number"), "number", 60).strip()
    if not number:
        raise ValidationFailed("number is required", field="number")
    digest, size = "", 0
    if data.get("xml_file"):
        try:
            raw = base64.b64decode(str(data["xml_file"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationFailed("xml_file must be base64", field="xml_file") from exc
        if len(raw) > MAX_XML_BYTES:
            raise ValidationFailed("xml_file is larger than 2 MB", field="xml_file")
        if not raw.lstrip().startswith(b"<"):
            raise ValidationFailed("xml_file is not an XML document", field="xml_file")
        digest, size = hashlib.sha256(raw).hexdigest(), len(raw)
    if SupplierInvoiceUpload.objects.filter(
        company_id=supplier.company_id, supplier_id=supplier.id, number=number
    ).exists():
        raise ValidationFailed("this invoice number was already uploaded", field="number")
    return SupplierInvoiceUpload.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=supplier.company_id,
        supplier_id=supplier.id,
        party_id=supplier.party_id,
        number=number,
        xml_sha256=digest,
        xml_size=size,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )


# --------------------------------------------------------------------------- payments and KPIs (T-PORTAL-SUP-05)


def own_open_items(supplier: Supplier) -> Any:
    return ApOpenItem.objects.filter(company_id=supplier.company_id, supplier_id=supplier.id)


def own_payments(supplier: Supplier) -> list[tuple[ApOpenItem, PaymentStatusInfo]]:
    """Vouchers (CE, P16 ``PaymentStatus``) applied to the supplier's own payables."""
    svc = services.optional(PaymentStatus)
    if svc is None:
        return []
    out: list[tuple[ApOpenItem, PaymentStatusInfo]] = []
    for item in own_open_items(supplier).order_by("document_date", "document_number"):
        doc = DocumentRef(item.document_type, item.document_id, item.document_number)
        out.extend((item, info) for info in svc.for_document(supplier.company_id, doc))
    return out


def kpis(supplier: Supplier, period_start: dt.date, period_end: dt.date) -> dict[str, Any]:
    """The four dashboard KPIs of ``module-registry.json › portals › proveedores``, scoped to the supplier."""
    today = clock.business_date()
    open_rfqs = own_rfqs(supplier).filter(status=Rfq.Status.SENT, deadline__gte=clock.local_midnight_utc(today)).count()
    to_confirm = PurchaseOrder.objects.filter(
        company_id=supplier.company_id, supplier_id=supplier.id, status=PurchaseOrder.Status.SENT
    ).count()
    payable = sum(
        (Decimal(x.balance_cop) for x in own_open_items(supplier).exclude(status=ApOpenItem.Status.PAID)), ZERO
    )
    paid = sum(
        (
            info.amount
            for _item, info in own_payments(supplier)
            if info.state == "POSTED" and info.posted_on is not None and period_start <= info.posted_on <= period_end
        ),
        ZERO,
    )
    return {
        "sp_rfq_abiertas": open_rfqs,
        "sp_oc_por_confirmar": to_confirm,
        "sp_facturas_por_pagar": payable,
        "sp_pagos_periodo": paid,
    }
