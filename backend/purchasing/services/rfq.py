"""RFQ → vendor quotes → comparison → award → approval → purchase orders (F-PUR-CYCLE).

States (FEATURES-ADDENDUM §14): DRAFT → SENT → CLOSED (deadline) → EVALUATED (comparison snapshot) →
AWARD_PENDING_APPROVAL → AWARDED → PO_CREATED | CANCELLED. ``:reopen`` (reason) returns a CLOSED /
EVALUATED RFQ to SENT with a new deadline and readmits its LATE quotes (D-010).

Every function runs inside the caller's unit of work and authorizes through ``PolicyService``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.db.models import Max

from kernel import clock, services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.tax import TaxLineInput, TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.money import FX, UNIT_COST, q_amount, round_half_up
from kernel.uow import UnitOfWork
from localization_co.holidays import add_business_days
from purchasing import scoring
from purchasing.errors import (
    QUOTE_LATE_WARNING,
    AwardNotApproved,
    AwardReasonRequired,
    InvalidStateTransition,
    QuoteCurrencyRateMissing,
    ReasonRequired,
    RfqDeadlinePassed,
    SodViolation,
)
from purchasing.models import (
    Award,
    AwardLine,
    PurchaseOrder,
    PurchaseOrderLine,
    QuoteComparison,
    Rfq,
    RfqLine,
    Supplier,
    VendorQuote,
    VendorQuoteLine,
)
from purchasing.services.common import (
    audit,
    currency_in,
    date_in,
    datetime_in,
    decimal_in,
    dims_in,
    lock,
    next_number,
    qty_in,
    rate_on,
    require,
    settings_for,
    text_in,
    uuid_in,
    uuid_req,
)

S = Rfq.Status
Q = VendorQuote.QuoteStatus
AWARD_APPROVAL_POLICY = "purchasing.rfq.award"
AWARD_DOCUMENT = "purchasing.rfq_award"
FUNCTIONAL = "COP"
RECOMMENDED_VENDORS = 3

RFQ_FIELDS = (
    "document_date",
    "posting_date",
    "currency",
    "notes",
    "terms",
    "deadline",
    "invited_vendors",
    "scoring_weights",
    "lines",
)
RFQ_LINE_FIELDS = ("line_no", "item_id", "description", "quantity", "uom", "tax_code", "dimensions", "source_line_id")
QUOTE_FIELDS = (
    "document_date",
    "posting_date",
    "party",
    "currency",
    "fx_rate",
    "notes",
    "lead_time_days",
    "payment_terms_days",
    "valid_until",
    "freight_amount",
    "lines",
)
QUOTE_LINE_FIELDS = (
    "line_no",
    "rfq_line_id",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "factor_to_base",
    "presentation_id",
    "discount_pct",
    "tax_code",
    "tax_rate_pct",
    "tax_recoverable",
    "freight_amount",
    "lead_time_days",
    "notes",
    "amount",
    "account_code",
    "dimensions",
)


def _check_fields(data: Mapping[str, Any], allowed: Sequence[str], where: str = "") -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown properties{where}: {', '.join(unknown)}", fields=unknown)


def _transition(rfq: Rfq, allowed: Sequence[str], action: str) -> None:
    if rfq.status not in allowed:
        raise InvalidStateTransition(f"cannot {action} an RFQ in status {rfq.status}", status_now=rfq.status)


def _int_in(raw: Any, field: str, default: int = 0) -> int:
    if raw in (None, ""):
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 3650:
        raise ValidationFailed(f"{field} must be an integer 0–3650", field=field)
    return raw


def default_deadline(document_date: dt.date, business_days: int) -> dt.datetime:
    """D-010: ``business_days`` Bogotá business days after the document date, end of that day (local time)."""
    day = add_business_days(document_date, business_days)
    local = dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=clock.tz("America/Bogota"))
    return local.astimezone(dt.UTC)


def _save(obj: Any, uow: UnitOfWork, fields: list[str]) -> None:
    obj.save_versioned(uow.ctx.principal_id, fields)


# --------------------------------------------------------------------------- RFQ


def _invited(uow: UnitOfWork, company_id: uuid.UUID, raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("at least one invited vendor is required (D-010)", field="invited_vendors")
    out: list[str] = []
    for i, item in enumerate(raw):
        sid = uuid_req(item, f"invited_vendors[{i}]")
        ok = Supplier.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=sid, active=True).exists()
        if not ok:
            raise ValidationFailed(f"invited_vendors[{i}] is not an active supplier", field="invited_vendors")
        if str(sid) not in out:
            out.append(str(sid))
    return out


def _rfq_lines(uow: UnitOfWork, rfq: Rfq, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("an RFQ needs at least one line", field="lines")
    seen: set[int] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        _check_fields(line, RFQ_LINE_FIELDS, f" in lines[{i}]")
        line_no = line.get("line_no", i + 1)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{i}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        qty, uom = qty_in(line.get("quantity"), f"lines[{i}].quantity")
        uom = uom or str(line.get("uom") or "")
        if not uom:
            raise ValidationFailed(f"lines[{i}] needs the base unit of measure", field="lines")
        RfqLine.objects.create(
            tenant_id=rfq.tenant_id,
            company_id=rfq.company_id,
            rfq=rfq,
            line_no=line_no,
            item_id=uuid_in(line.get("item_id"), f"lines[{i}].item_id", required=False),
            description=text_in(line.get("description"), "description", 500),
            quantity=qty,
            uom=uom[:20],
            tax_code=text_in(line.get("tax_code"), "tax_code", 40),
            dimensions=dims_in(line.get("dimensions")),
            source_line_id=uuid_in(line.get("source_line_id"), f"lines[{i}].source_line_id", required=False),
        )


def create_rfq(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, source: DocumentRef | None = None
) -> tuple[Rfq, list[str]]:
    """New DRAFT RFQ. Returns ``(rfq, warnings)``; fewer than 3 invited vendors is a warning only (D-010)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.rfq.create", {"company_id": company_id})
    _check_fields(data, RFQ_FIELDS)
    settings = settings_for(ctx, company_id)
    doc_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    deadline = (
        datetime_in(data["deadline"], "deadline")
        if data.get("deadline")
        else default_deadline(doc_date, settings.rfq_deadline_business_days)
    )
    weights = scoring.validate_weights(data.get("scoring_weights") or settings.scoring_weights)
    invited = _invited(uow, company_id, data.get("invited_vendors"))
    rfq = Rfq.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        number=next_number(uow, company_id, "rfq", doc_date),
        document_date=doc_date,
        deadline=deadline,
        currency=currency_in(data.get("currency")),
        notes=text_in(data.get("notes"), "notes"),
        terms=text_in(data.get("terms"), "terms"),
        invited_vendors=invited,
        scoring_weights={k: str(v) for k, v in weights.items()},
        source={"type": source.type, "id": str(source.id), "number": source.number} if source else None,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _rfq_lines(uow, rfq, data.get("lines"))
    rfq.source_lines = [str(x) for x in rfq.lines.exclude(source_line_id=None).values_list("source_line_id", flat=True)]
    if rfq.source_lines:
        rfq.save(update_fields=["source_lines"])
    audit(uow, "purchasing.rfq.create", "purchasing.rfq", rfq.id, company_id, {"number": rfq.number})
    warnings = ["fewer_than_3_vendors"] if len(invited) < RECOMMENDED_VENDORS else []
    return rfq, warnings


def update_rfq(uow: UnitOfWork, rfq: Rfq, data: Mapping[str, Any]) -> Rfq:
    require(uow.ctx, "purchasing.rfq.update", rfq)
    _check_fields(data, RFQ_FIELDS)
    _transition(rfq, (S.DRAFT,), "edit")
    fields: list[str] = []
    if "document_date" in data:
        rfq.document_date = date_in(data["document_date"], "document_date")
        fields.append("document_date")
    if "deadline" in data:
        rfq.deadline = datetime_in(data["deadline"], "deadline")
        fields.append("deadline")
    if "currency" in data:
        rfq.currency = currency_in(data["currency"])
        fields.append("currency")
    for key in ("notes", "terms"):
        if key in data:
            setattr(rfq, key, text_in(data[key], key))
            fields.append(key)
    if "invited_vendors" in data:
        rfq.invited_vendors = _invited(uow, rfq.company_id, data["invited_vendors"])
        fields.append("invited_vendors")
    if "scoring_weights" in data:
        rfq.scoring_weights = {k: str(v) for k, v in scoring.validate_weights(data["scoring_weights"]).items()}
        fields.append("scoring_weights")
    if "lines" in data:
        rfq.lines.all().delete()
        _rfq_lines(uow, rfq, data["lines"])
    _save(rfq, uow, fields)
    audit(uow, "purchasing.rfq.update", "purchasing.rfq", rfq.id, rfq.company_id, {"fields": sorted(data)})
    return rfq


def delete_rfq(uow: UnitOfWork, rfq: Rfq) -> None:
    require(uow.ctx, "purchasing.rfq.delete", rfq)
    _transition(rfq, (S.DRAFT,), "delete")
    audit(uow, "purchasing.rfq.delete", "purchasing.rfq", rfq.id, rfq.company_id, {"number": rfq.number})
    rfq.lines.all().delete()
    rfq.delete()


def send_rfq(uow: UnitOfWork, rfq: Rfq) -> Rfq:
    """DRAFT → SENT. Vendor notification (portal / local mail capture) is P11b's portal + P07 notifications."""
    require(uow.ctx, "purchasing.rfq.update", rfq)
    _transition(rfq, (S.DRAFT,), "send")
    now = clock.now()
    if rfq.deadline <= now:
        raise RfqDeadlinePassed("the response deadline is already in the past")
    rfq.status, rfq.sent_at = S.SENT, now
    _save(rfq, uow, ["status", "sent_at"])
    audit(uow, "purchasing.rfq.send", "purchasing.rfq", rfq.id, rfq.company_id, {"status": S.SENT})
    return rfq


def close_rfq(uow: UnitOfWork, rfq: Rfq, *, system: bool = False) -> Rfq:
    """SENT → CLOSED once the deadline passed (``:close`` or the scheduled job)."""
    if not system:
        require(uow.ctx, "purchasing.rfq.update", rfq)
    _transition(rfq, (S.SENT,), "close")
    now = clock.now()
    if now < rfq.deadline:
        raise InvalidStateTransition("the RFQ closes at its deadline", deadline=rfq.deadline.isoformat())
    rfq.status, rfq.closed_at = S.CLOSED, now
    _save(rfq, uow, ["status", "closed_at"])
    audit(uow, "purchasing.rfq.close", "purchasing.rfq", rfq.id, rfq.company_id, {"status": S.CLOSED})
    return rfq


def close_due_rfqs(uow: UnitOfWork, company_id: uuid.UUID) -> list[uuid.UUID]:
    """Scheduler job body: close every SENT RFQ of the company whose deadline passed."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    due = Rfq.objects.select_for_update().filter(
        tenant_id=ctx.tenant_id, company_id=company_id, status=S.SENT, deadline__lte=clock.now()
    )
    closed = []
    for rfq in due.order_by("id"):
        close_rfq(uow, rfq, system=True)
        closed.append(rfq.id)
    return closed


def reopen_rfq(uow: UnitOfWork, rfq: Rfq, reason: str, deadline: Any) -> Rfq:
    """CLOSED/EVALUATED → SENT with a new future deadline; LATE quotes are readmitted (D-010)."""
    require(uow.ctx, "purchasing.rfq.update", rfq)
    _transition(rfq, (S.CLOSED, S.EVALUATED), "reopen")
    reason = (reason or "").strip()
    if not reason:
        raise ReasonRequired("reopening an RFQ requires a reason", field="reason")
    new_deadline = datetime_in(deadline, "deadline") if deadline else None
    if new_deadline is None or new_deadline <= clock.now():
        raise RfqDeadlinePassed("reopening needs a new deadline in the future", field="deadline")
    readmitted = list(
        VendorQuote.objects.filter(rfq=rfq, quote_status=Q.LATE).order_by("id").values_list("id", flat=True)
    )
    VendorQuote.objects.filter(id__in=readmitted).update(quote_status=Q.RECEIVED, updated_by=uow.ctx.principal_id)
    rfq.status, rfq.deadline, rfq.closed_at = S.SENT, new_deadline, None
    rfq.reopen_count += 1
    rfq.reopen_reason = text_in(reason, "reason")
    _save(rfq, uow, ["status", "deadline", "closed_at", "reopen_count", "reopen_reason"])
    audit(
        uow,
        "purchasing.rfq.reopen",
        "purchasing.rfq",
        rfq.id,
        rfq.company_id,
        {"readmitted_quotes": [str(q) for q in readmitted], "deadline": new_deadline.isoformat()},
        reason=reason,
    )
    return rfq


def cancel_rfq(uow: UnitOfWork, rfq: Rfq, reason: str) -> Rfq:
    require(uow.ctx, "purchasing.rfq.update", rfq)
    _transition(rfq, (S.DRAFT, S.SENT, S.CLOSED, S.EVALUATED), "cancel")
    if not (reason or "").strip():
        raise ReasonRequired("cancelling an RFQ requires a reason", field="reason")
    rfq.status = S.CANCELLED
    _save(rfq, uow, ["status"])
    audit(uow, "purchasing.rfq.cancel", "purchasing.rfq", rfq.id, rfq.company_id, {}, reason=reason)
    return rfq


# --------------------------------------------------------------------------- quotes


def _quote_rate(uow: UnitOfWork, company_id: uuid.UUID, currency: str, raw: Any, on: dt.date) -> tuple[Decimal, str]:
    """Rate at the quote date: COP → 1; else the rate given with the quote, else the ledger rate on/before the date."""
    if currency == FUNCTIONAL:
        return Decimal(1), "functional"
    if raw not in (None, ""):
        return round_half_up(decimal_in(raw, "fx_rate", positive=True), FX), "quote"
    rate = rate_on(uow, company_id, currency, on)
    if rate is None:
        raise QuoteCurrencyRateMissing(f"no {currency} rate on or before {on.isoformat()}", currency=currency)
    return rate, "ledger"


def _tax_rate_pct(
    uow: UnitOfWork, company_id: uuid.UUID, party_id: uuid.UUID | None, code: str, on: dt.date
) -> Decimal:
    """Rate of ``code`` from P06's TaxService (fails closed with ``tax_parameter_unknown``)."""
    base = Decimal(100)
    computation = services.get(TaxService).compute(
        company_id, "purchasing.vendor_quote", on, party_id, [TaxLineInput("rate", base, (code,))]
    )
    amount = sum((r.amount for r in computation.lines if r.tax_code == code), Decimal(0))
    return amount * Decimal(100) / base


def _quote_lines(uow: UnitOfWork, quote: VendorQuote, rfq: Rfq, raw: Any) -> None:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("a quote needs at least one line", field="lines")
    rfq_lines = {line.id: line for line in rfq.lines.all()}
    by_no = {line.line_no: line for line in rfq_lines.values()}
    used: set[uuid.UUID] = set()
    for i, line in enumerate(raw):
        if not isinstance(line, Mapping):
            raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
        _check_fields(line, QUOTE_LINE_FIELDS, f" in lines[{i}]")
        target = None
        if line.get("rfq_line_id"):
            target = rfq_lines.get(uuid_in(line["rfq_line_id"], f"lines[{i}].rfq_line_id"))  # type: ignore[arg-type]
        elif isinstance(line.get("line_no"), int):
            target = by_no.get(line["line_no"])
        if target is None:
            raise ValidationFailed(f"lines[{i}] does not reference a line of the RFQ", field="lines")
        if target.id in used:
            raise ValidationFailed(f"lines[{i}] quotes RFQ line {target.line_no} twice", field="lines")
        used.add(target.id)
        qty, uom = qty_in(line.get("quantity", {"value": str(target.quantity), "uom": target.uom}), "quantity")
        uom = uom or target.uom
        factor = decimal_in(line.get("factor_to_base", "1"), f"lines[{i}].factor_to_base", positive=True)
        if uom == target.uom and factor != 1:
            raise ValidationFailed(f"lines[{i}]: the base UoM has factor 1", field="lines")
        tax_code = text_in(line.get("tax_code", target.tax_code), "tax_code", 40)
        if "tax_rate_pct" in line:
            tax_pct = decimal_in(line["tax_rate_pct"], "tax_rate_pct", minimum=Decimal(0), maximum=Decimal(100))
        elif tax_code:
            tax_pct = _tax_rate_pct(uow, quote.company_id, quote.supplier.party_id, tax_code, quote.document_date)
        else:
            tax_pct = Decimal(0)
        freight = line.get("freight_amount")
        VendorQuoteLine.objects.create(
            tenant_id=quote.tenant_id,
            company_id=quote.company_id,
            quote=quote,
            rfq_line=target,
            line_no=target.line_no,
            item_id=target.item_id,
            description=text_in(line.get("description", target.description), "description", 500),
            quantity=qty,
            uom=uom[:20],
            factor_to_base=factor,
            presentation_id=uuid_in(line.get("presentation_id"), "presentation_id", required=False),
            unit_price=round_half_up(
                decimal_in(
                    (line.get("unit_price") or {}).get("amount")
                    if isinstance(line.get("unit_price"), Mapping)
                    else line.get("unit_price"),
                    f"lines[{i}].unit_price",
                    minimum=Decimal(0),
                ),
                UNIT_COST,
            ),
            discount_pct=decimal_in(
                line.get("discount_pct", "0"), "discount_pct", minimum=Decimal(0), maximum=Decimal(100)
            ),
            tax_code=tax_code,
            tax_rate_pct=tax_pct,
            tax_recoverable=bool(line.get("tax_recoverable", True)),
            freight_amount=None if freight in (None, "") else decimal_in(freight, "freight_amount", minimum=Decimal(0)),
            lead_time_days=None
            if line.get("lead_time_days") is None
            else _int_in(line["lead_time_days"], "lead_time_days"),
            notes=text_in(line.get("notes"), "notes"),
        )


def _refresh_quote_totals(quote: VendorQuote) -> None:
    total = Decimal(0)
    for line in quote.lines.all():
        total += q_amount(line.quantity * line.unit_price * (Decimal(100) - line.discount_pct) / Decimal(100))
    quote.total = total
    quote.save(update_fields=["total"])


def create_quote(
    uow: UnitOfWork,
    rfq: Rfq,
    data: Mapping[str, Any],
    *,
    channel: str = VendorQuote.Channel.INTERNAL_ENTRY,
    supplier_id: uuid.UUID | None = None,
) -> tuple[VendorQuote, list[str]]:
    """Register a vendor quote. After the deadline it is stored LATE (warning ``quote_late``, D-010).

    ``supplier_id`` is set by the supplier portal (P11b) from the portal token's party scope, never from
    the body; internal entry takes it from ``party.id``.
    """
    ctx = uow.ctx
    require(ctx, "purchasing.quote.create", rfq)
    _check_fields(data, QUOTE_FIELDS)
    _transition(rfq, (S.SENT, S.CLOSED, S.EVALUATED), "receive quotes for")
    sid = supplier_id or uuid_req(data.get("party"), "party")
    if str(sid) not in rfq.invited_vendors:
        raise ValidationFailed("the vendor was not invited to this RFQ", field="party")
    supplier = Supplier.objects.get(tenant_id=ctx.tenant_id, company_id=rfq.company_id, id=sid)
    if VendorQuote.objects.filter(rfq=rfq, supplier=supplier).exclude(quote_status=Q.WITHDRAWN).exists():
        raise Conflict(
            "the vendor already has a quote for this RFQ; update it instead", code="invalid_state_transition"
        )
    now = clock.now()
    late = now > rfq.deadline
    doc_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    currency = currency_in(data.get("currency"), rfq.currency)
    rate, rate_source = _quote_rate(uow, rfq.company_id, currency, data.get("fx_rate"), doc_date)
    quote = VendorQuote.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=rfq.company_id,
        rfq=rfq,
        supplier=supplier,
        number=next_number(uow, rfq.company_id, "quote", doc_date),
        document_date=doc_date,
        received_at=now,
        channel=channel,
        valid_until=date_in(data["valid_until"], "valid_until") if data.get("valid_until") else None,
        currency=currency,
        fx_rate=rate,
        fx_source=rate_source,
        lead_time_days=_int_in(data.get("lead_time_days"), "lead_time_days"),
        payment_terms_days=_int_in(data.get("payment_terms_days"), "payment_terms_days", supplier.payment_terms_days),
        freight_amount=decimal_in(data.get("freight_amount", "0"), "freight_amount", minimum=Decimal(0)),
        quote_status=Q.LATE if late else Q.RECEIVED,
        notes=text_in(data.get("notes"), "notes"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _quote_lines(uow, quote, rfq, data.get("lines"))
    _refresh_quote_totals(quote)
    audit(
        uow,
        "purchasing.quote.create",
        "purchasing.quote",
        quote.id,
        rfq.company_id,
        {"rfq": str(rfq.id), "supplier": str(supplier.id), "status": quote.quote_status, "channel": channel},
    )
    return quote, [QUOTE_LATE_WARNING] if late else []


def update_quote(uow: UnitOfWork, quote: VendorQuote, data: Mapping[str, Any]) -> VendorQuote:
    """Revise a quote while the RFQ is open (SENT); a revision after the deadline is refused."""
    require(uow.ctx, "purchasing.quote.update", quote)
    _check_fields(data, QUOTE_FIELDS)
    rfq = quote.rfq
    _transition(rfq, (S.SENT,), "revise quotes of")
    if clock.now() > rfq.deadline:
        raise RfqDeadlinePassed("quotes cannot be revised after the deadline")
    if quote.quote_status != Q.RECEIVED:
        raise InvalidStateTransition(f"a {quote.quote_status} quote cannot be revised")
    if "party" in data and uuid_in(data["party"], "party") != quote.supplier_id:
        raise ValidationFailed("the vendor of a quote cannot change", field="party")
    fields: list[str] = []
    for key in ("lead_time_days", "payment_terms_days"):
        if key in data:
            setattr(quote, key, _int_in(data[key], key))
            fields.append(key)
    if "freight_amount" in data:
        quote.freight_amount = decimal_in(data["freight_amount"], "freight_amount", minimum=Decimal(0))
        fields.append("freight_amount")
    if "notes" in data:
        quote.notes = text_in(data["notes"], "notes")
        fields.append("notes")
    if "valid_until" in data:
        quote.valid_until = date_in(data["valid_until"], "valid_until") if data["valid_until"] else None
        fields.append("valid_until")
    if "currency" in data or "fx_rate" in data:
        quote.currency = currency_in(data.get("currency"), quote.currency)
        quote.fx_rate, quote.fx_source = _quote_rate(
            uow, quote.company_id, quote.currency, data.get("fx_rate"), quote.document_date
        )
        fields += ["currency", "fx_rate", "fx_source"]
    _save(quote, uow, fields)
    if "lines" in data:
        quote.lines.all().delete()
        _quote_lines(uow, quote, rfq, data["lines"])
        _refresh_quote_totals(quote)
    audit(uow, "purchasing.quote.update", "purchasing.quote", quote.id, quote.company_id, {"fields": sorted(data)})
    return quote


def withdraw_quote(uow: UnitOfWork, quote: VendorQuote, reason: str = "") -> VendorQuote:
    """``DELETE …/quotes/{id}``: quotes are never deleted; they become WITHDRAWN (kept for the audit trail)."""
    require(uow.ctx, "purchasing.quote.delete", quote)
    _transition(quote.rfq, (S.SENT, S.CLOSED, S.EVALUATED), "withdraw quotes of")
    if quote.quote_status not in (Q.RECEIVED, Q.LATE):
        raise InvalidStateTransition(f"a {quote.quote_status} quote cannot be withdrawn")
    quote.quote_status = Q.WITHDRAWN
    _save(quote, uow, ["quote_status"])
    audit(uow, "purchasing.quote.withdraw", "purchasing.quote", quote.id, quote.company_id, {}, reason=reason)
    return quote


# --------------------------------------------------------------------------- comparison


def quote_inputs(rfq: Rfq) -> tuple[list[scoring.QuoteInput], list[scoring.RfqLineSpec]]:
    specs = [
        scoring.RfqLineSpec(line.id, line.line_no, line.quantity, line.uom) for line in rfq.lines.order_by("line_no")
    ]
    inputs: list[scoring.QuoteInput] = []
    quotes = VendorQuote.objects.filter(rfq=rfq).exclude(quote_status=Q.WITHDRAWN).select_related("supplier")
    for q in quotes.order_by("received_at", "id"):
        lines = tuple(
            scoring.QuoteLineInput(
                rfq_line_id=line.rfq_line_id,
                quantity=line.quantity,
                uom=line.uom,
                factor_to_base=line.factor_to_base,
                unit_price=line.unit_price,
                discount_pct=line.discount_pct,
                tax_rate_pct=line.tax_rate_pct,
                tax_recoverable=line.tax_recoverable,
                freight_amount=line.freight_amount,
                quote_line_id=line.id,
            )
            for line in q.lines.order_by("line_no")
        )
        lead = max([q.lead_time_days, *[x.lead_time_days or 0 for x in q.lines.all()]])
        inputs.append(
            scoring.QuoteInput(
                quote_id=q.id,
                vendor_id=q.supplier_id,
                vendor_code=q.supplier.code,
                vendor_name=q.supplier.name,
                received_at=q.received_at,
                currency=q.currency,
                fx_rate=q.fx_rate,
                lead_time_days=lead,
                payment_terms_days=q.payment_terms_days,
                rating=q.supplier.rating,
                lines=lines,
                freight_amount=q.freight_amount,
                status="RECEIVED" if q.quote_status == Q.AWARDED else q.quote_status,
            )
        )
    return inputs, specs


def evaluate_rfq(uow: UnitOfWork, rfq: Rfq) -> QuoteComparison:
    """CLOSED/EVALUATED → EVALUATED: immutable comparison snapshot with the explained recommendation."""
    ctx = uow.ctx
    require(ctx, "purchasing.rfq.update", rfq)
    _transition(rfq, (S.CLOSED, S.EVALUATED), "evaluate")
    inputs, specs = quote_inputs(rfq)
    comparison = scoring.compare(inputs, specs, rfq.scoring_weights)
    for item in [*comparison.ranked]:
        VendorQuote.objects.filter(id=item.normalized.quote.quote_id).update(
            landed_total_cop=item.normalized.comparable_total_cop
        )
    for excluded in comparison.excluded:
        VendorQuote.objects.filter(id=excluded.quote.quote_id).update(landed_total_cop=excluded.comparable_total_cop)
    seq = (QuoteComparison.objects.filter(rfq=rfq).aggregate(m=Max("sequence"))["m"] or 0) + 1
    snapshot = QuoteComparison.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=rfq.company_id,
        rfq=rfq,
        sequence=seq,
        weights={k: str(v) for k, v in comparison.weights.items()},
        snapshot=comparison.to_json(),
        recommended_quote_id=comparison.recommended.normalized.quote.quote_id if comparison.recommended else None,
        explanation=comparison.explanation,
        created_by=ctx.principal_id,
    )
    rfq.status = S.EVALUATED
    _save(rfq, uow, ["status"])
    audit(
        uow,
        "purchasing.rfq.evaluate",
        "purchasing.rfq",
        rfq.id,
        rfq.company_id,
        {"comparison": str(snapshot.id), "recommended": str(snapshot.recommended_quote_id)},
    )
    return snapshot


def latest_comparison(rfq: Rfq) -> QuoteComparison | None:
    return QuoteComparison.objects.filter(rfq=rfq).order_by("-sequence").first()


# --------------------------------------------------------------------------- award


def award_rfq(uow: UnitOfWork, rfq: Rfq, data: Mapping[str, Any]) -> Award:
    """EVALUATED → AWARD_PENDING_APPROVAL. ``quote_id`` awards every line to one quote; ``lines`` splits
    per RFQ line (``[{rfq_line_id, quote_id}]``). A choice different from the recommendation needs a reason."""
    ctx = uow.ctx
    require(ctx, "purchasing.rfq.approve", rfq)
    _check_fields(data, ("quote_id", "lines", "reason"))
    _transition(rfq, (S.EVALUATED,), "award")
    comparison = latest_comparison(rfq)
    if comparison is None:
        raise InvalidStateTransition("evaluate the RFQ before awarding")
    eligible = {r["quote_id"]: r for r in comparison.snapshot.get("ranked", [])}
    excluded = {r["quote_id"]: r for r in comparison.snapshot.get("excluded", [])}
    rfq_lines = {line.id: line for line in rfq.lines.all()}
    choice: dict[uuid.UUID, uuid.UUID] = {}
    if data.get("lines"):
        if not isinstance(data["lines"], list):
            raise ValidationFailed("lines must be a list", field="lines")
        for i, item in enumerate(data["lines"]):
            if not isinstance(item, Mapping):
                raise ValidationFailed(f"lines[{i}] must be an object", field="lines")
            line_id = uuid_req(item.get("rfq_line_id"), f"lines[{i}].rfq_line_id")
            if line_id not in rfq_lines:
                raise ValidationFailed(f"lines[{i}] is not a line of the RFQ", field="lines")
            choice[line_id] = uuid_req(item.get("quote_id"), f"lines[{i}].quote_id")
        if set(choice) != set(rfq_lines):
            raise ValidationFailed("every RFQ line must be awarded", field="lines")
    else:
        quote_id = uuid_req(data.get("quote_id"), "quote_id")
        choice = dict.fromkeys(rfq_lines, quote_id)
    for quote_id in set(choice.values()):
        if str(quote_id) in excluded:
            reason = excluded[str(quote_id)].get("reason")
            raise InvalidStateTransition(
                f"quote {quote_id} is excluded from scoring ({reason}); reopen the RFQ to readmit it",
                quote_status=reason,
            )
        if str(quote_id) not in eligible:
            raise ValidationFailed("the quote is not part of the latest comparison", field="quote_id")
    recommended = comparison.recommended_quote_id
    differs = any(q != recommended for q in choice.values())
    reason = text_in(data.get("reason"), "reason").strip()
    if differs and not reason:
        raise AwardReasonRequired("the award differs from the recommendation: a reason is required", field="reason")
    if Award.objects.filter(rfq=rfq, status=Award.Status.PENDING_APPROVAL).exists():
        raise InvalidStateTransition("an award is already pending approval")
    award = Award.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=rfq.company_id,
        rfq=rfq,
        comparison=comparison,
        recommended_quote_id=recommended,
        differs_from_recommendation=differs,
        reason=reason,
        proposed_by=ctx.principal_id,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    for line_id, quote_id in sorted(choice.items(), key=lambda kv: rfq_lines[kv[0]].line_no):
        quote_line = VendorQuoteLine.objects.filter(quote_id=quote_id, rfq_line_id=line_id).first()
        if quote_line is None:
            raise ValidationFailed(f"the quote does not cover RFQ line {rfq_lines[line_id].line_no}", field="lines")
        AwardLine.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=rfq.company_id,
            award=award,
            rfq_line=rfq_lines[line_id],
            quote_id=quote_id,
            quote_line=quote_line,
        )
    approval = services.get(WorkflowService).request_approval(
        uow, rfq.company_id, DocumentRef(AWARD_DOCUMENT, award.id, rfq.number), AWARD_APPROVAL_POLICY, reason
    )
    award.approval_request_id = approval.id
    award.save(update_fields=["approval_request_id"])
    rfq.status = S.AWARD_PENDING_APPROVAL
    _save(rfq, uow, ["status"])
    audit(
        uow,
        "purchasing.rfq.award",
        "purchasing.rfq",
        rfq.id,
        rfq.company_id,
        {
            "award": str(award.id),
            "differs_from_recommendation": differs,
            "quotes": sorted(str(q) for q in set(choice.values())),
        },
        reason=reason,
    )
    return award


def pending_award(rfq: Rfq) -> Award:
    award = Award.objects.filter(rfq=rfq, status=Award.Status.PENDING_APPROVAL).order_by("-created_at").first()
    if award is None:
        raise NotFound("no award pending approval")
    return award


def approve_award(uow: UnitOfWork, rfq: Rfq, approve: bool, reason: str = "") -> Award:
    """Decide the pending award. SoD: the approver is neither the RFQ's buyer nor the award proposer.

    Approval → AWARDED, ``purchasing.rfq.awarded.v1`` and one DRAFT purchase order per awarded vendor
    (→ PO_CREATED). Rejection → back to EVALUATED.
    """
    ctx = uow.ctx
    require(ctx, "purchasing.rfq.approve", rfq)
    _transition(rfq, (S.AWARD_PENDING_APPROVAL,), "approve the award of")
    award: Award = lock(Award.objects.filter(rfq=rfq), pending_award(rfq).id)
    if ctx.principal_id in (award.proposed_by, rfq.created_by):
        raise SodViolation("the buyer or the proposer of an award cannot approve it")
    if not approve and not (reason or "").strip():
        raise ReasonRequired("rejecting an award requires a reason", field="reason")
    if award.approval_request_id is None:
        raise AwardNotApproved("the award has no approval request")
    decision = services.get(WorkflowService).decide(uow, award.approval_request_id, approve, reason)
    if decision.state == "PENDING":
        audit(uow, "purchasing.rfq.approve_award.step", "purchasing.rfq", rfq.id, rfq.company_id, {}, reason=reason)
        return award
    award.decided_by, award.decided_at, award.decision_reason = ctx.principal_id, clock.now(), text_in(reason, "reason")
    if decision.state != "APPROVED":
        award.status = Award.Status.REJECTED
        _save(award, uow, ["status", "decided_by", "decided_at", "decision_reason"])
        rfq.status = S.EVALUATED
        _save(rfq, uow, ["status"])
        audit(uow, "purchasing.rfq.reject_award", "purchasing.rfq", rfq.id, rfq.company_id, {}, reason=reason)
        return award
    award.status = Award.Status.APPROVED
    _save(award, uow, ["status", "decided_by", "decided_at", "decision_reason"])
    quote_ids = sorted({line.quote_id for line in award.lines.all()})
    VendorQuote.objects.filter(id__in=quote_ids).update(quote_status=Q.AWARDED)
    rfq.status = S.AWARDED
    rfq.awarded_quote_id = quote_ids[0] if len(quote_ids) == 1 else None
    rfq.award_reason = award.reason
    _save(rfq, uow, ["status", "awarded_quote_id", "award_reason"])
    for quote in VendorQuote.objects.filter(id__in=quote_ids).select_related("supplier").order_by("id"):
        uow.emit(
            "purchasing.rfq.awarded.v1",
            {
                "rfq_id": str(rfq.id),
                "quote_id": str(quote.id),
                "vendor": {"id": str(quote.supplier.party_id or quote.supplier_id), "kind": "vendor"},
                "reason": award.reason or "recomendación del comparativo",
            },
            company_id=rfq.company_id,
        )
    audit(uow, "purchasing.rfq.approve_award", "purchasing.rfq", rfq.id, rfq.company_id, {"award": str(award.id)})
    create_orders_from_award(uow, award)
    return award


def create_orders_from_award(uow: UnitOfWork, award: Award) -> list[PurchaseOrder]:
    """One DRAFT purchase order per awarded vendor; refused with ``award_not_approved`` before approval."""
    from purchasing.services.orders import recompute_total

    ctx = uow.ctx
    require(ctx, "purchasing.order.create", award)
    if award.status != Award.Status.APPROVED:
        raise AwardNotApproved("the award must be approved before purchase orders are created")
    rfq = award.rfq
    if PurchaseOrder.objects.filter(award=award).exists():
        raise InvalidStateTransition("purchase orders were already created for this award")
    by_quote: dict[uuid.UUID, list[AwardLine]] = {}
    for line in award.lines.select_related("quote_line", "rfq_line", "quote").order_by("rfq_line__line_no"):
        by_quote.setdefault(line.quote_id, []).append(line)
    orders: list[PurchaseOrder] = []
    today = clock.business_date()
    for quote_id in sorted(by_quote):
        quote = VendorQuote.objects.select_related("supplier").get(id=quote_id)
        order = PurchaseOrder.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=rfq.company_id,
            number=next_number(uow, rfq.company_id, "order", today),
            document_date=today,
            expected_date=today + dt.timedelta(days=quote.lead_time_days) if quote.lead_time_days else None,
            supplier=quote.supplier,
            currency=quote.currency,
            fx_rate=quote.fx_rate,
            rfq=rfq,
            award=award,
            source={"type": "purchasing.rfq", "id": str(rfq.id), "number": rfq.number},
            created_by=ctx.principal_id,
            updated_by=ctx.principal_id,
        )
        for n, line in enumerate(by_quote[quote_id], start=1):
            ql, rl = line.quote_line, line.rfq_line
            net_per_quoted = ql.unit_price * (Decimal(100) - ql.discount_pct) / Decimal(100)
            PurchaseOrderLine.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=rfq.company_id,
                order=order,
                line_no=n,
                item_id=rl.item_id,
                description=rl.description or ql.description,
                quantity=rl.quantity,
                uom=rl.uom,
                unit_price=round_half_up(net_per_quoted / ql.factor_to_base, UNIT_COST),
                tax_code=ql.tax_code,
                dimensions=rl.dimensions,
                rfq_line=rl,
            )
        recompute_total(order)
        orders.append(order)
        audit(uow, "purchasing.order.create", "purchasing.order", order.id, order.company_id, {"award": str(award.id)})
    rfq.status = S.PO_CREATED
    _save(rfq, uow, ["status"])
    return orders
