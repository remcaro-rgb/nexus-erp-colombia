"""Customer-portal services (F-PORTAL-CUS, part P10b) — repository layer of every portal read and write.

Security model (D-018, D-033, F-PORTAL-CORE):

* the party and company always come from the external account bound to the token (``PortalIdentity``), never
  from the request; every query filters by ``tenant_id`` + ``company_id`` + ``party_id`` here (repository
  layer) **and** through :meth:`iam.policy.engine.PolicyEngine.scope_queryset` (portal scope) — a foreign id is a
  404 (``portal_document_not_owned``, no existence oracle);
* reads of the sales documents go through the frozen P10a projections in :mod:`sales.interfaces`
  (allow-listed ``Customer*`` fields: never costs, margins, price snapshots or formulas);
* nothing here posts to the ledger: quote acceptance creates a DRAFT sales order, a simulated payment success
  leaves a DRAFT recibo de caja for treasury (D-021). Internal steps (quoting a request, approving a profile
  change) run with the internal user's own ``sales.*`` permissions.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from iam.policy.engine import get_engine
from kernel import clock, ids
from kernel.errors import NotFound, ValidationFailed
from kernel.models import Sequence
from kernel.money import minor_units, q_amount, round_half_up
from kernel.sequences import allocate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales import interfaces as core
from sales.errors import InvalidStateTransition
from sales.models import ArApplication, ArOpenItem, Invoice, Party, Quote, Receipt, SalesNote, SalesOrder
from sales.portal.models import CustomerRequest, PaymentIntent
from sales.services import ar
from sales.services import lines as L
from sales.services.common import audit, decimal_in, next_number, require, text_in, unknown_keys

#: Label of every synthetic fiscal artefact served by the portal in this DEV-only edition (T-PORTAL-CUS-05).
FISCAL_LABEL = "SINTÉTICO — sin validez fiscal"
#: Simulated payment provider (D-021): a fixture, never a real gateway.
PROVIDER = "SIMULADO (FIXTURE_NOT_CERTIFIED)"
REQUEST_SEQUENCE = ("SAL-PORTAL-REQ", "SOLC-")

PERM_QUOTE_ACCEPT = "portal.customer_quote.update"
PERM_STATEMENT = "portal.customer_statement.read"
PERM_INTENT_READ = "portal.customer_payment_intent.read"
PERM_INTENT_CREATE = "portal.customer_payment_intent.create"
PERM_REQUEST_READ = "portal.customer_request.read"
PERM_REQUEST_CREATE = "portal.customer_request.create"
PERM_PROFILE = "portal.customer_profile.read"
#: internal (``/api/v1/sales/``) permissions of the back-office side
PERM_INTERNAL_REQUEST_READ = "sales.portal_request.read"
PERM_INTERNAL_REQUEST_UPDATE = "sales.portal_request.update"
PERM_INTERNAL_REQUEST_APPROVE = "sales.portal_request.approve"
PERM_INTERNAL_INTENT_READ = "sales.payment_intent.read"

#: Party fields a customer may ask to change (applied only after an internal approval).
PROFILE_FIELDS = ("email", "einvoice_email", "phone", "address", "municipality_code", "trade_name")


class PaymentIntentAmountInvalid(ValidationFailed):
    code, title = "payment_intent_amount_invalid", "Payment intent amount invalid"


class PortalDocumentNotOwned(NotFound):
    code, title = "portal_document_not_owned", "Document not owned by the portal account"


@dataclass(frozen=True, slots=True)
class Scope:
    """The token's external account: tenant, company, party (never a value of the request)."""

    ctx: TenantContext
    company_id: uuid.UUID
    party_id: uuid.UUID
    account_id: uuid.UUID


def _uuid(raw: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise PortalDocumentNotOwned("not found") from exc


def own(scope: Scope, action: str, queryset: Any) -> Any:
    """Repository-level party filter + the policy engine's portal scope (defence in depth)."""
    qs = queryset.filter(tenant_id=scope.ctx.tenant_id, company_id=scope.company_id, party_id=scope.party_id)
    return get_engine().scope_queryset(scope.ctx, action, qs)


def party_of(scope: Scope) -> Party:
    # the party *is* the row (no ``party_id`` column): explicit tenant + company + id filter
    party = Party.objects.filter(tenant_id=scope.ctx.tenant_id, company_id=scope.company_id, id=scope.party_id).first()
    if party is None or "customer" not in (party.roles or []):
        raise PortalDocumentNotOwned("the external account is not linked to a customer of this company")
    return party


def _money(amount: Decimal, currency: str) -> dict[str, str]:
    return {"amount": f"{q_amount(amount):f}", "currency": currency}


def _stamp(obj: Any) -> dict[str, Any]:
    return {
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        "channel": "portal",
    }


# --------------------------------------------------------------------------- sales documents (P10a projections)


def quotes(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_quotes(scope.ctx, scope.company_id, scope.party_id)


def quote(scope: Scope, pk: Any) -> dict[str, Any]:
    return core.portal_quote(scope.ctx, scope.company_id, scope.party_id, pk)


def orders(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_orders(scope.ctx, scope.company_id, scope.party_id)


def order(scope: Scope, pk: Any) -> dict[str, Any]:
    return core.portal_order(scope.ctx, scope.company_id, scope.party_id, pk)


def invoices(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_invoices(scope.ctx, scope.company_id, scope.party_id)


def invoice(scope: Scope, pk: Any) -> dict[str, Any]:
    return core.portal_invoice(scope.ctx, scope.company_id, scope.party_id, pk)


def credit_notes(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_credit_notes(scope.ctx, scope.company_id, scope.party_id)


def credit_note(scope: Scope, pk: Any) -> dict[str, Any]:
    key = _uuid(pk)
    for row in credit_notes(scope):
        if row["id"] == str(key):
            return row
    raise PortalDocumentNotOwned("not found")


def receipts(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_receipts(scope.ctx, scope.company_id, scope.party_id)


def receipt(scope: Scope, pk: Any) -> dict[str, Any]:
    key = _uuid(pk)
    for row in receipts(scope):
        if row["id"] == str(key):
            return row
    raise PortalDocumentNotOwned("not found")


def open_items(scope: Scope) -> list[dict[str, Any]]:
    return core.portal_open_items(scope.ctx, scope.company_id, scope.party_id)


def own_invoice_row(scope: Scope, pk: Any) -> Invoice:
    """The POSTED invoice model row (for the synthetic PDF/XML), after the projection proved ownership."""
    invoice(scope, pk)  # permission + party ownership (404 otherwise)
    row: Invoice | None = own(
        scope, core.PERM_INVOICE, Invoice.objects.filter(id=_uuid(pk), status=Invoice.Status.POSTED)
    ).first()
    if row is None:
        raise PortalDocumentNotOwned("not found")
    return row


def own_note_row(scope: Scope, pk: Any) -> SalesNote:
    credit_note(scope, pk)
    row: SalesNote | None = own(
        scope,
        core.PERM_CREDIT_NOTE,
        SalesNote.objects.filter(id=_uuid(pk), kind=SalesNote.Kind.CREDIT, status=SalesNote.Status.POSTED),
    ).first()
    if row is None:
        raise PortalDocumentNotOwned("not found")
    return row


# --------------------------------------------------------------------------- quote acceptance (customer)


def accept_quote(uow: UnitOfWork, scope: Scope, pk: Any) -> tuple[Quote, SalesOrder]:
    """Customer accepts a SENT quote → quote ACCEPTED → DRAFT sales order (quote ORDERED), one unit of work.

    The order enters the normal internal cycle (credit check + reservation at ``:confirm``); nothing posts.
    """
    require(scope.ctx, PERM_QUOTE_ACCEPT, {"company_id": scope.company_id, "party_id": scope.party_id})
    quote(scope, pk)  # ownership + visibility (drafts are internal → 404)
    q = own(scope, PERM_QUOTE_ACCEPT, Quote.objects.select_for_update().filter(id=_uuid(pk))).first()
    if q is None:
        raise PortalDocumentNotOwned("not found")
    if q.status != Quote.Status.SENT:
        raise InvalidStateTransition(f"a {q.status} quote cannot be accepted", status_now=q.status)
    today = clock.business_date()
    if q.valid_until is not None and q.valid_until < today:
        raise InvalidStateTransition("the quote is no longer valid", status_now="EXPIRED")
    L.require_overrides_approved(uow, q)
    from sales.services.documents import order_from_quote

    q.status = Quote.Status.ACCEPTED
    sales_order = order_from_quote(uow, q)
    sales_order.channel = "portal"
    sales_order.save(update_fields=["channel"])
    q.status, q.order_id = Quote.Status.ORDERED, sales_order.id
    q.version += 1
    q.updated_by = scope.ctx.principal_id
    q.save()
    CustomerRequest.objects.filter(
        tenant_id=scope.ctx.tenant_id, company_id=scope.company_id, party_id=scope.party_id, quote_id=q.id
    ).update(request_status=CustomerRequest.Status.ORDERED, order_id=sales_order.id, updated_at=clock.now())
    audit(
        uow,
        "portal.customer.quote_accepted",
        "sales.quote",
        q.id,
        scope.company_id,
        {"order": sales_order.number, "channel": "portal", "external_account": str(scope.account_id)},
    )
    return q, sales_order


# --------------------------------------------------------------------------- requests (quote / profile / general)

REQUEST_KEYS = ("request_type", "message", "items", "changes")


def _request_number(uow: UnitOfWork, company_id: uuid.UUID) -> str:
    code, prefix = REQUEST_SEQUENCE
    Sequence.objects.get_or_create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        sequence_code=code,
        prefix=prefix,
        defaults={"padding": 6},
    )
    return allocate(company_id, code, prefix, on_date=clock.business_date()).formatted


def _items(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        raise ValidationFailed("a quote request needs 1–100 items {item_id, quantity}", field="items")
    out = []
    for n, it in enumerate(raw):
        if not isinstance(it, Mapping):
            raise ValidationFailed(f"items[{n}] must be an object", field="items")
        unknown_keys(it, ("item_id", "quantity", "uom"), f" in items[{n}]")
        try:
            item_id = uuid.UUID(str(it.get("item_id")))
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"items[{n}].item_id must be a uuid", field="items") from exc
        qty = decimal_in(it.get("quantity"), f"items[{n}].quantity", positive=True)
        row = {"item_id": str(item_id), "quantity": f"{qty:f}"}
        if it.get("uom"):
            row["uom"] = text_in(it.get("uom"), "uom", 20)
        out.append(row)
    return out


def _changes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping) or not raw:
        raise ValidationFailed("a profile change needs at least one field in changes", field="changes")
    unknown_keys(raw, PROFILE_FIELDS, " in changes")
    return {k: text_in(v, k, 254) for k, v in raw.items()}


def create_request(uow: UnitOfWork, scope: Scope, data: Mapping[str, Any]) -> CustomerRequest:
    require(scope.ctx, PERM_REQUEST_CREATE, {"company_id": scope.company_id, "party_id": scope.party_id})
    unknown_keys(data, REQUEST_KEYS)
    party = party_of(scope)
    kind = str(data.get("request_type") or "").strip().upper()
    if kind not in CustomerRequest.Type.values:
        raise ValidationFailed("request_type must be QUOTE, PROFILE_CHANGE or GENERAL", field="request_type")
    message = text_in(data.get("message"), "message", 10000)
    items = _items(data.get("items")) if kind == CustomerRequest.Type.QUOTE else []
    changes = _changes(data.get("changes")) if kind == CustomerRequest.Type.PROFILE_CHANGE else {}
    if kind != CustomerRequest.Type.QUOTE and data.get("items"):
        raise ValidationFailed("items are only allowed in a QUOTE request", field="items")
    if kind != CustomerRequest.Type.PROFILE_CHANGE and data.get("changes"):
        raise ValidationFailed("changes are only allowed in a PROFILE_CHANGE request", field="changes")
    if kind == CustomerRequest.Type.GENERAL and not message:
        raise ValidationFailed("message is required", field="message")
    req = CustomerRequest.objects.create(
        tenant_id=scope.ctx.tenant_id,
        company_id=scope.company_id,
        party=party,
        external_account_id=scope.account_id,
        number=_request_number(uow, scope.company_id),
        request_type=kind,
        message=message,
        items=items,
        changes=changes,
        created_by=scope.ctx.principal_id,
        updated_by=scope.ctx.principal_id,
    )
    audit(
        uow,
        "portal.customer.request_created",
        "sales.portal_request",
        req.id,
        scope.company_id,
        {"type": kind, "number": req.number, "channel": "portal"},
    )
    return req


def requests(scope: Scope) -> Any:
    return own(scope, PERM_REQUEST_READ, CustomerRequest.objects.all())


def request_json(r: CustomerRequest) -> dict[str, Any]:
    status = {
        "SUBMITTED": "SUBMITTED",
        "QUOTED": "APPROVED",
        "ORDERED": "APPROVED",
        "APPROVED": "APPROVED",
        "REJECTED": "REJECTED",
        "RESOLVED": "POSTED",
    }
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "status": status.get(r.request_status, "SUBMITTED"),
        "number": r.number,
        "request_type": r.request_type,
        "message": r.message,
        "items": list(r.items or []),
        "changes": dict(r.changes or {}),
        "request_status": r.request_status,
        "resolution": r.resolution,
        "quote_id": str(r.quote_id) if r.quote_id else None,
        "order_id": str(r.order_id) if r.order_id else None,
        "version": r.version,
        "audit": _stamp(r),
    }


# ----------------------------------------------------------------- internal side of the requests (back office)


def internal_requests(ctx: TenantContext) -> Any:
    require(ctx, PERM_INTERNAL_REQUEST_READ)
    qs = get_engine().scope_queryset(ctx, PERM_INTERNAL_REQUEST_READ, CustomerRequest.objects.all())
    return qs.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))


def internal_request(ctx: TenantContext, pk: Any) -> CustomerRequest:
    try:
        key = uuid.UUID(str(pk))
    except (TypeError, ValueError) as exc:
        raise NotFound("not found") from exc
    row: CustomerRequest | None = internal_requests(ctx).filter(id=key).first()
    if row is None:
        raise NotFound("not found")
    return row


def _lock_request(ctx: TenantContext, pk: Any) -> CustomerRequest:
    row = internal_request(ctx, pk)
    locked = CustomerRequest.objects.select_for_update().filter(id=row.id).first()
    assert locked is not None  # noqa: S101 - just read in the same transaction
    return locked


def _decide(uow: UnitOfWork, req: CustomerRequest, status: str, resolution: str) -> None:
    req.request_status = status
    req.resolution = resolution
    req.decided_by, req.decided_at = uow.ctx.principal_id, clock.now()
    req.version += 1
    req.updated_by, req.updated_at = uow.ctx.principal_id, clock.now()
    req.save()


def quote_request(uow: UnitOfWork, pk: Any, params: Mapping[str, Any]) -> tuple[CustomerRequest, Quote]:
    """Internal: turn a QUOTE request into a DRAFT quote (D-024 prices resolved by the P10a engine).

    ``params``: ``lines`` (optional, P10a quote lines; default = the requested items), ``valid_until``,
    ``send`` (true → the quote is sent to the customer right away, P10a ``:send`` rules).
    """
    from sales.services.documents import create_quote, quote_action

    ctx = uow.ctx
    require(ctx, PERM_INTERNAL_REQUEST_UPDATE)
    req = _lock_request(ctx, pk)
    if req.request_type != CustomerRequest.Type.QUOTE:
        raise ValidationFailed("only QUOTE requests can be quoted", field="request_type")
    if req.request_status != CustomerRequest.Status.SUBMITTED:
        raise InvalidStateTransition(f"a {req.request_status} request cannot be quoted", status_now=req.request_status)
    unknown_keys(params, ("lines", "valid_until", "send", "currency"), " in parameters")
    lines = params.get("lines") or [
        {"item_id": i["item_id"], "quantity": i["quantity"], **({"uom": i["uom"]} if i.get("uom") else {})}
        for i in req.items
    ]
    body: dict[str, Any] = {
        "party": str(req.party_id),
        "document_date": clock.business_date().isoformat(),
        "lines": lines,
        "notes": f"Solicitud del portal {req.number}",
    }
    if params.get("valid_until"):
        body["valid_until"] = params["valid_until"]
    if params.get("currency"):
        body["currency"] = params["currency"]
    q = create_quote(uow, req.company_id, body)
    q.channel = "portal"
    q.source = {"type": "sales.portal_request", "id": str(req.id), "number": req.number}
    q.save(update_fields=["channel", "source"])
    if params.get("send"):
        q = quote_action(uow, q, "send", "", {})
    req.quote_id = q.id
    _decide(uow, req, CustomerRequest.Status.QUOTED, f"Cotización {q.number}")
    audit(uow, "sales.portal_request.quote", "sales.portal_request", req.id, req.company_id, {"quote": q.number})
    return req, q


def approve_request(uow: UnitOfWork, pk: Any, reason: str) -> CustomerRequest:
    """Internal: apply a PROFILE_CHANGE (P10a ``update_party`` rules) or close a GENERAL request."""
    from sales.party.service import update_party

    ctx = uow.ctx
    require(ctx, PERM_INTERNAL_REQUEST_APPROVE)
    req = _lock_request(ctx, pk)
    if req.request_status != CustomerRequest.Status.SUBMITTED:
        raise InvalidStateTransition(
            f"a {req.request_status} request is already decided", status_now=req.request_status
        )
    if req.created_by == ctx.principal_id:
        from sales.errors import SodViolation

        raise SodViolation("the requester cannot approve its own request")
    if req.request_type == CustomerRequest.Type.PROFILE_CHANGE:
        party = Party.objects.filter(tenant_id=ctx.tenant_id, company_id=req.company_id, id=req.party_id).first()
        if party is None:
            raise NotFound("not found")
        update_party(uow, party, dict(req.changes))
        _decide(uow, req, CustomerRequest.Status.APPROVED, reason or "Cambio aplicado")
    elif req.request_type == CustomerRequest.Type.GENERAL:
        _decide(uow, req, CustomerRequest.Status.RESOLVED, reason or "Atendida")
    else:
        raise ValidationFailed("a QUOTE request is answered with :quote", field="request_type")
    audit(uow, "sales.portal_request.approve", "sales.portal_request", req.id, req.company_id, {}, reason)
    return req


def reject_request(uow: UnitOfWork, pk: Any, reason: str) -> CustomerRequest:
    ctx = uow.ctx
    require(ctx, PERM_INTERNAL_REQUEST_UPDATE)
    if not reason.strip():
        raise ValidationFailed("reason is required", code="reason_required", field="reason")
    req = _lock_request(ctx, pk)
    if req.request_status != CustomerRequest.Status.SUBMITTED:
        raise InvalidStateTransition(
            f"a {req.request_status} request is already decided", status_now=req.request_status
        )
    _decide(uow, req, CustomerRequest.Status.REJECTED, reason)
    audit(uow, "sales.portal_request.reject", "sales.portal_request", req.id, req.company_id, {}, reason)
    return req


# --------------------------------------------------------------------------- payment intents (D-021)

INTENT_KEYS = ("documents", "amount")


def _effective_intent_status(pi: PaymentIntent) -> str:
    if pi.intent_status == PaymentIntent.Status.SUCCEEDED_SIMULATED and pi.receipt_id is not None:
        posted = Receipt.objects.filter(
            tenant_id=pi.tenant_id, company_id=pi.company_id, id=pi.receipt_id, status=Receipt.Status.POSTED
        ).exists()
        if posted:
            return PaymentIntent.Status.CONFIRMED_BY_TREASURY
    return str(pi.intent_status)


def intent_json(pi: PaymentIntent) -> dict[str, Any]:
    status = _effective_intent_status(pi)
    generic = {"CREATED": "DRAFT", "SUCCEEDED_SIMULATED": "SUBMITTED", "FAILED": "CANCELLED"}
    return {
        "id": pi.id,
        "tenant_id": pi.tenant_id,
        "status": generic.get(status, "POSTED"),
        "documents": list(pi.documents or []),
        "amount": _money(pi.amount, pi.currency),
        "intent_status": status,
        "provider": pi.provider,
        "version": pi.version,
        "audit": _stamp(pi),
    }


def _open_invoice_items(scope: Scope, document_ids: Iterable[uuid.UUID]) -> list[ArOpenItem]:
    wanted = list(document_ids)
    rows = list(
        own(
            scope,
            PERM_INTENT_CREATE,
            ArOpenItem.objects.filter(document_type="sales.invoice", document_id__in=wanted, balance__gt=0),
        ).order_by("due_date", "document_number", "id")
    )
    if {r.document_id for r in rows} != set(wanted):
        # another party's invoice, an unknown id or an invoice already paid: same answer (no oracle)
        raise PortalDocumentNotOwned("every document must be an open invoice of your account")
    return rows


def _amount(raw: Any) -> tuple[Decimal, str]:
    if not isinstance(raw, Mapping):
        raise PaymentIntentAmountInvalid("amount must be {amount, currency}", field="amount")
    currency = str(raw.get("currency") or "")
    if isinstance(raw.get("amount"), float):
        raise PaymentIntentAmountInvalid("amount.amount must be a decimal string (floats are refused)", field="amount")
    try:
        value = Decimal(str(raw.get("amount")))
    except ArithmeticError as exc:
        raise PaymentIntentAmountInvalid("amount.amount must be a decimal string", field="amount") from exc
    if not value.is_finite() or value <= 0:
        raise PaymentIntentAmountInvalid("the amount must be greater than zero", field="amount")
    if value != round_half_up(value, minor_units(currency or "COP")):
        raise PaymentIntentAmountInvalid("too many decimals for the currency", field="amount")
    return value, currency


def create_intent(uow: UnitOfWork, scope: Scope, data: Mapping[str, Any]) -> PaymentIntent:
    require(scope.ctx, PERM_INTENT_CREATE, {"company_id": scope.company_id, "party_id": scope.party_id})
    unknown_keys(data, INTENT_KEYS)
    party = party_of(scope)
    docs = data.get("documents")
    if not isinstance(docs, list) or not docs or len(docs) > 50:
        raise ValidationFailed("documents must list 1–50 invoice ids", field="documents")
    try:
        doc_ids = list(dict.fromkeys(uuid.UUID(str(d)) for d in docs))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("documents must be invoice ids", field="documents") from exc
    amount, currency = _amount(data.get("amount"))
    items = _open_invoice_items(scope, doc_ids)
    currencies = {i.currency for i in items}
    if currencies != {currency}:
        raise PaymentIntentAmountInvalid("the currency must be the currency of the invoices", field="amount")
    open_total = sum((i.balance for i in items), Decimal(0))
    if amount > open_total:
        raise PaymentIntentAmountInvalid("the amount exceeds the open balance of the invoices", field="amount")
    pi = PaymentIntent.objects.create(
        tenant_id=scope.ctx.tenant_id,
        company_id=scope.company_id,
        party=party,
        external_account_id=scope.account_id,
        documents=[str(d) for d in doc_ids],
        amount=amount,
        currency=currency,
        provider=PROVIDER,
        provider_ref=f"SIM-{ids.new_id().hex[:12].upper()}",
        created_by=scope.ctx.principal_id,
        updated_by=scope.ctx.principal_id,
    )
    audit(
        uow,
        "portal.customer.payment_intent_created",
        "sales.payment_intent",
        pi.id,
        scope.company_id,
        {"documents": len(doc_ids), "channel": "portal", "provider": "SIMULADO"},
    )
    return pi


def intents(scope: Scope) -> Any:
    return own(scope, PERM_INTENT_READ, PaymentIntent.objects.all())


def simulate_intent(uow: UnitOfWork, scope: Scope, pk: Any, outcome: str) -> PaymentIntent:
    """Simulated provider callback (DEV fixture). Success → DRAFT recibo de caja for treasury; NEVER posts."""
    require(scope.ctx, PERM_INTENT_CREATE, {"company_id": scope.company_id, "party_id": scope.party_id})
    if outcome not in ("success", "failure"):
        raise ValidationFailed("parameters.outcome must be success or failure", field="parameters")
    pi: PaymentIntent | None = own(
        scope, PERM_INTENT_CREATE, PaymentIntent.objects.select_for_update().filter(id=_uuid(pk))
    ).first()
    if pi is None:
        raise PortalDocumentNotOwned("not found")
    if pi.intent_status != PaymentIntent.Status.CREATED:
        raise InvalidStateTransition(f"a {pi.intent_status} payment intent was already processed")
    now = clock.now()
    if outcome == "failure":
        pi.intent_status, pi.failure_reason = PaymentIntent.Status.FAILED, "Pago rechazado (simulado)"
    else:
        pi.receipt_id = _draft_receipt(uow, scope, pi).id
        pi.intent_status = PaymentIntent.Status.SUCCEEDED_SIMULATED
    pi.simulated_at = now
    pi.version += 1
    pi.updated_at, pi.updated_by = now, scope.ctx.principal_id
    pi.save()
    audit(
        uow,
        "portal.customer.payment_intent_simulated",
        "sales.payment_intent",
        pi.id,
        scope.company_id,
        {"outcome": outcome, "receipt": str(pi.receipt_id) if pi.receipt_id else None},
    )
    return pi


def _draft_receipt(uow: UnitOfWork, scope: Scope, pi: PaymentIntent) -> Receipt:
    """DRAFT recibo de caja (sales ``Receipt``) applied to the intent's invoices, oldest due first.

    Treasury reviews it in ``/api/v1/sales/receipts`` and posts it with ``:post`` (its own permission); the
    open items and the ledger stay untouched until then (D-021).
    """
    items = _open_invoice_items(scope, [uuid.UUID(d) for d in pi.documents])
    left = pi.amount
    applications = []
    for item in items:
        if left <= 0:
            break
        take = min(item.balance, left)
        applications.append({"open_item_id": str(item.id), "amount": f"{q_amount(take):f}"})
        left -= take
    if left > 0:  # an invoice was paid in the meantime: the provider result cannot be applied any more
        raise PaymentIntentAmountInvalid("the amount exceeds the open balance of the invoices", field="amount")
    from sales.pricing import engine

    day = clock.business_date()
    rc = Receipt.objects.create(
        tenant_id=scope.ctx.tenant_id,
        company_id=scope.company_id,
        number=next_number(uow, scope.company_id, "receipt", day),
        document_date=day,
        party_id=scope.party_id,
        currency=pi.currency,
        fx_rate=engine.document_fx(uow, scope.company_id, pi.currency, day, None),
        amount=pi.amount,
        bank_role="BANK",
        withholdings=[],
        applications=applications,
        created_by=scope.ctx.principal_id,
        updated_by=scope.ctx.principal_id,
    )
    audit(
        uow,
        "sales.receipt.create",
        "sales.receipt",
        rc.id,
        scope.company_id,
        {"number": rc.number, "source": "portal.payment_intent", "payment_intent": str(pi.id)},
    )
    return rc


def internal_intents(ctx: TenantContext) -> Any:
    require(ctx, PERM_INTERNAL_INTENT_READ)
    qs = get_engine().scope_queryset(ctx, PERM_INTERNAL_INTENT_READ, PaymentIntent.objects.all())
    return qs.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))


# --------------------------------------------------------------------------- statement, profile, KPIs


def _party_items(scope: Scope, action: str) -> Any:
    return own(scope, action, ArOpenItem.objects.all())


def statement(scope: Scope, date_from: dt.date, date_to: dt.date) -> dict[str, Any]:
    """ReportResult of the customer's account statement (P10a ``ar.statement``) + aging of the open balance."""
    require(scope.ctx, PERM_STATEMENT, {"company_id": scope.company_id, "party_id": scope.party_id})
    if date_from > date_to:
        raise ValidationFailed("from must not be after to", field="from")
    party = party_of(scope)
    items = _party_items(scope, PERM_STATEMENT)
    body = ar.statement(scope.ctx, party, items, date_from, date_to)
    aging = ar.aging(scope.ctx, items, date_to)
    rows = [
        {
            "date": m["date"],
            "document_type": m["document"]["type"],
            "document_id": m["document"]["id"],
            "number": m["document"]["number"],
            "debit": m["debit"],
            "credit": m["credit"],
            "balance": m["balance"],
        }
        for m in body["lines"]
    ]
    debit = sum((Decimal(r["debit"]) for r in rows), Decimal(0))
    credit = sum((Decimal(r["credit"]) for r in rows), Decimal(0))
    return {
        "report": "sales.customer_statement",
        "parameters": {"from": date_from.isoformat(), "to": date_to.isoformat(), "currency": "COP"},
        "columns": [
            {"key": "date", "label": "Fecha", "type": "date"},
            {"key": "document_type", "label": "Tipo", "type": "string"},
            {"key": "number", "label": "Documento", "type": "string"},
            {"key": "debit", "label": "Cargos", "type": "money"},
            {"key": "credit", "label": "Abonos", "type": "money"},
            {"key": "balance", "label": "Saldo", "type": "money"},
        ],
        "rows": rows,
        "totals": {
            "opening_balance": body["opening_balance"],
            "debit": f"{q_amount(debit):f}",
            "credit": f"{q_amount(credit):f}",
            "closing_balance": body["closing_balance"],
            "currency": "COP",
            "aging": aging["totals"],
            "open_balance": aging["total"],
        },
        "warnings": [FISCAL_LABEL],
        "generated_at": clock.now().isoformat(),
    }


def profile(scope: Scope) -> dict[str, Any]:
    party = party_of(scope)
    return {
        "id": party.id,
        "tenant_id": party.tenant_id,
        "legal_name": party.legal_name,
        "nit": f"{party.nit}-{party.dv}" if party.dv else party.nit,
        "email": party.email,
        "version": party.version,
        "audit": {"channel": "portal"},
    }


def kpis(scope: Scope, start: dt.date, end: dt.date) -> dict[str, Decimal | int]:
    """The four ``cp_*`` KPIs of ``module-registry.json › portals › clientes`` (party-scoped)."""
    today = clock.business_date()
    items = _party_items(scope, PERM_PROFILE)
    open_rows = items.filter(balance__gt=0)
    balance = open_rows.aggregate(s=Sum("balance_cop"))["s"] or Decimal(0)
    overdue = open_rows.filter(due_date__lt=today).count()
    running = own(
        scope,
        core.PERM_ORDER,
        SalesOrder.objects.filter(status__in=(SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_DELIVERED)),
    ).count()
    paid = ArApplication.objects.filter(
        tenant_id=scope.ctx.tenant_id,
        company_id=scope.company_id,
        open_item__party_id=scope.party_id,
        source_type="sales.receipt",
        reversed_by__isnull=True,
        applied_on__gte=start,
        applied_on__lte=end,
    ).aggregate(s=Sum("amount_cop"))["s"] or Decimal(0)
    return {
        "cp_saldo_pendiente": q_amount(balance),
        "cp_facturas_vencidas": overdue,
        "cp_pedidos_en_curso": running,
        "cp_pagos_periodo": q_amount(paid),
    }


__all__ = [
    "FISCAL_LABEL",
    "PROVIDER",
    "PaymentIntentAmountInvalid",
    "PortalDocumentNotOwned",
    "Scope",
    "accept_quote",
    "approve_request",
    "create_intent",
    "create_request",
    "kpis",
    "quote_request",
    "reject_request",
    "simulate_intent",
    "statement",
]
