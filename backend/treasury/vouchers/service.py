"""Recibos de caja (RC) and comprobantes de egreso (CE) — FEATURES-ADDENDUM F-TRE-RC / F-TRE-CE.

States: DRAFT → (submit) SUBMITTED (= PENDING_APPROVAL, when the amount reaches the threshold) → APPROVED →
(post) POSTED → (reverse) REVERSED by a reversal voucher in an open period; DRAFT → CANCELLED (never numbered).

Line kinds are derived from the contract line (``CashReceiptLine`` / ``DisbursementLine``):

* ``open_item_id`` → ``AR_APPLICATION`` (RC, P10 ``ArOpenItems``) / ``AP_APPLICATION`` (CE, ``ApOpenItems``);
  amount = ``applied_amount``;
* ``concept_id`` → ``CONCEPT`` (``TreasuryConcept``: account role, default dimensions); amount = ``applied_amount``
  or ``unit_price`` × ``quantity``;
* ``tax_code`` alone → ``WITHHOLDING``: ``unit_price`` is the tax base, the amount comes from the P06 tax engine
  (D-029; unknown parameters fail closed with ``tax_parameter_unknown``);
* none of these → ``ADVANCE`` (customer / vendor advance) for the unapplied remainder; amount = ``applied_amount``
  or ``unit_price``.

Posting (one unit of work with the number allocation and the open-item updates; any failure rolls back all):

* RC: Dr fund Σ received; Cr AR per applied open item; Cr CUSTOMER_ADVANCE; Dr WHT_RECEIVABLE per tax code;
  Cr concept accounts. Received = applications + advances + concepts − withholdings.
* CE: Cr fund Σ paid; Dr AP per applied open item; Dr VENDOR_ADVANCE; Cr WHT_PAYABLE per tax code; Dr concept
  accounts.
* Reversal: mirror entry (``PostingService.reverse``) in an open period, open items re-opened, a numbered
  reversal voucher linked by ``reversal_of``; the original number stays used and the original row immutable.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from kernel import clock, services
from kernel.errors import DomainError, NotFound, ValidationFailed
from kernel.interfaces.ar import Application, ArOpenItems
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.tax import TaxLineInput, TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.money import q_amount
from kernel.uow import UnitOfWork
from treasury.errors import (
    INSUFFICIENT_FUNDS_WARNING,
    InvalidStateTransition,
    SodViolation,
    TaxParameterUnknown,
    TreasuryApprovalRequired,
    TreasuryCurrencyMismatch,
    TreasuryInsufficientFunds,
    TreasuryOpenItemMismatchParty,
    TreasuryOverapplication,
    TreasuryPeriodClosed,
)
from treasury.interfaces import DISBURSEMENT_TYPE, POLICY_CE, POLICY_DUAL, POLICY_RC, RECEIPT_TYPE, ApOpenItems
from treasury.models import DIM_SLOTS, Fund, TreasuryConcept, TreasuryVoucher, TreasuryVoucherLine
from treasury.services.common import (
    ROLE_AP,
    ROLE_AR,
    ROLE_CUSTOMER_ADVANCE,
    ROLE_VENDOR_ADVANCE,
    ROLE_WHT_PAYABLE,
    ROLE_WHT_RECEIVABLE,
    SEQ_CE,
    SEQ_RC,
    audit,
    currency_in,
    date_in,
    dimension_values,
    dims_in,
    lock,
    money_in,
    next_number,
    qty_in,
    rate_on,
    require,
    settings_for,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)
from treasury.vouchers import funds

V = TreasuryVoucher
Side = Literal["DEBIT", "CREDIT"]
L = TreasuryVoucherLine
HEADER_FIELDS = (
    "document_date",
    "posting_date",
    "party",
    "currency",
    "notes",
    "fund_id",
    "payment_method",
    "reference",
    "branch_id",
    "lines",
)
LINE_FIELDS = (
    "line_no",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "amount",  # readOnly in the contract: echoed values are ignored
    "tax_code",
    "account_code",
    "dimensions",
    "concept_id",
    "open_item_id",
    "applied_amount",
)
EDITABLE = (V.State.DRAFT, V.State.REJECTED)
INFLOW = {L.Kind.AR_APPLICATION, L.Kind.AP_APPLICATION, L.Kind.ADVANCE, L.Kind.CONCEPT}


@dataclass(frozen=True, slots=True)
class Kind:
    """Per-type constants (RC vs CE)."""

    code: str
    doc_type: str
    perm: str
    sequence: str
    policy: str
    party_kind: str
    direction: str  # concept direction accepted besides BOTH
    event: str

    @property
    def is_receipt(self) -> bool:
        return self.code == V.Type.RC


KINDS = {
    V.Type.RC: Kind("RC", RECEIPT_TYPE, "treasury.receipt", SEQ_RC, POLICY_RC, "customer", "IN", "treasury.receipt"),
    V.Type.CE: Kind(
        "CE", DISBURSEMENT_TYPE, "treasury.disbursement", SEQ_CE, POLICY_CE, "vendor", "OUT", "treasury.disbursement"
    ),
}


def kind_of(voucher: TreasuryVoucher) -> Kind:
    return KINDS[V.Type(voucher.voucher_type)]


def ref(voucher: TreasuryVoucher) -> DocumentRef:
    return DocumentRef(kind_of(voucher).doc_type, voucher.id, voucher.number or None)


# --------------------------------------------------------------------------- input


@dataclass
class LineSpec:
    line_no: int
    kind: str
    amount: Decimal = Decimal(0)
    description: str = ""
    open_item_id: uuid.UUID | None = None
    open_item_document: dict[str, Any] = field(default_factory=dict)
    concept: TreasuryConcept | None = None
    tax_code: str = ""
    tax_base: Decimal | None = None
    dims: dict[str, uuid.UUID] = field(default_factory=dict)


def _amount_of(raw: Mapping[str, Any], n: int, currency: str, *, base: bool = False) -> Decimal:
    if not base and raw.get("applied_amount") not in (None, ""):
        return money_in(raw.get("applied_amount"), f"lines[{n}].applied_amount", currency)
    if raw.get("unit_price") in (None, ""):
        raise ValidationFailed(f"lines[{n}] needs an amount (applied_amount or unit_price)", field="lines")
    price = money_in(raw.get("unit_price"), f"lines[{n}].unit_price", currency)
    return q_amount(price * qty_in(raw.get("quantity"), f"lines[{n}].quantity"))


def _concept(uow: UnitOfWork, company_id: uuid.UUID, raw: Any, n: int, k: Kind) -> TreasuryConcept:
    cid = uuid_req(raw, f"lines[{n}].concept_id")
    concept = TreasuryConcept.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=cid).first()
    if concept is None or concept.status != "ACTIVE":
        raise ValidationFailed(f"lines[{n}].concept_id is not an active concept of the company", field="lines")
    if concept.direction not in (k.direction, TreasuryConcept.Direction.BOTH):
        raise ValidationFailed(
            f"concept {concept.code} ({concept.direction}) cannot be used on a {k.code}", field="lines"
        )
    return concept


def parse_lines(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    k: Kind,
    party_id: uuid.UUID | None,
    currency: str,
    raw_lines: Any,
) -> list[LineSpec]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationFailed("a voucher needs at least one line", field="lines")
    if len(raw_lines) > 500:
        raise ValidationFailed("at most 500 lines", field="lines")
    seen: set[int] = set()
    out: list[LineSpec] = []
    for n, raw in enumerate(raw_lines):
        if not isinstance(raw, Mapping):
            raise ValidationFailed(f"lines[{n}] must be an object", field="lines")
        unknown_keys(raw, LINE_FIELDS, f" in lines[{n}]")
        try:
            line_no = int(raw.get("line_no") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"lines[{n}].line_no must be an integer", field="lines") from exc
        if line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{n}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        if raw.get("account_code") not in (None, ""):
            raise ValidationFailed(
                "accounts are derived (open item, concept, tax code, advance), never typed",
                field="lines",
                code="field_forbidden",
            )
        if raw.get("item_id") not in (None, ""):
            raise ValidationFailed("treasury lines carry no item", field="lines", code="field_forbidden")
        spec = LineSpec(
            line_no=line_no, kind=L.Kind.ADVANCE, description=text_in(raw.get("description"), "description", 255)
        )
        spec.dims = dims_in(raw.get("dimensions"), f"lines[{n}].dimensions")
        if raw.get("open_item_id") not in (None, ""):
            spec.kind = L.Kind.AR_APPLICATION if k.is_receipt else L.Kind.AP_APPLICATION
            spec.open_item_id = uuid_in(raw.get("open_item_id"), f"lines[{n}].open_item_id")
            spec.amount = _amount_of(raw, n, currency)
            if party_id is None:
                raise ValidationFailed("applying open items needs the voucher party", field="party")
            _check_open_item(uow, company_id, k, party_id, currency, spec)
        elif raw.get("concept_id") not in (None, ""):
            spec.kind = L.Kind.CONCEPT
            concept = _concept(uow, company_id, raw.get("concept_id"), n, k)
            spec.concept = concept
            spec.amount = _amount_of(raw, n, currency)
            if not spec.dims:
                spec.dims = {s: uuid.UUID(str(v)) for s, v in (concept.default_dimensions or {}).items() if v}
            if concept.requires_dimension and not spec.dims:
                raise ValidationFailed(
                    f"concept {concept.code} requires an analytical dimension", field="lines", code="dimension_required"
                )
            if concept.requires_party and party_id is None:
                raise ValidationFailed(f"concept {concept.code} requires a party", field="party")
            spec.description = spec.description or concept.name
        elif raw.get("tax_code") not in (None, ""):
            spec.kind = L.Kind.WITHHOLDING
            spec.tax_code = text_in(raw.get("tax_code"), "tax_code", 40)
            if raw.get("applied_amount") not in (None, ""):
                raise ValidationFailed(
                    "withholding amounts come from the tax engine (D-029); send the base in unit_price",
                    field="lines",
                    code="field_forbidden",
                )
            spec.tax_base = _amount_of(raw, n, currency, base=True)
            if party_id is None:
                raise ValidationFailed("withholdings need the voucher party (tax profile, D-029)", field="party")
        else:
            spec.amount = _amount_of(raw, n, currency)
            if party_id is None:
                raise ValidationFailed("an advance needs the voucher party", field="party")
        if spec.kind != L.Kind.WITHHOLDING and spec.amount <= 0:
            raise ValidationFailed(f"lines[{n}] amount must be positive", field="lines")
        out.append(spec)
    return out


def _check_open_item(
    uow: UnitOfWork, company_id: uuid.UUID, k: Kind, party_id: uuid.UUID, currency: str, spec: LineSpec
) -> None:
    oid = spec.open_item_id
    if k.is_receipt:
        items = {i.id: i for i in services.get(ArOpenItems).open_items(company_id, party_id)}
        ar = items.get(oid) if oid else None
        if ar is None:
            raise TreasuryOpenItemMismatchParty(
                "the open item is not an open receivable of the voucher party", open_item_id=str(oid)
            )
        item_currency, open_amount, doc = ar.currency, ar.open_amount, ar.document
    else:
        ap = services.get(ApOpenItems).get(company_id, oid) if oid else None
        if ap is None:
            raise NotFound("open item not found")
        if ap.party_id != party_id:
            raise TreasuryOpenItemMismatchParty("the open item is a payable of another party", open_item_id=str(oid))
        item_currency, open_amount, doc = ap.currency, ap.open_amount, ap.document
    if item_currency != currency or currency != "COP":
        raise TreasuryCurrencyMismatch(
            "open items are applied in COP vouchers of the item currency (foreign-currency application: CCR P16a-2)",
            open_item_id=str(oid),
        )
    if spec.amount > open_amount:
        raise TreasuryOverapplication(
            f"application {spec.amount} exceeds the open balance {open_amount} of {doc.number}", open_item_id=str(oid)
        )
    spec.open_item_document = {"type": doc.type, "id": str(doc.id), "number": doc.number or ""}
    spec.description = spec.description or (doc.number or "")


def _party_in(raw: Any, k: Kind) -> tuple[uuid.UUID | None, str]:
    if raw in (None, ""):
        return None, ""
    if not isinstance(raw, Mapping):
        raise ValidationFailed("party must be an object {id, kind}", field="party")
    unknown_keys(raw, ("id", "kind", "name", "tax_id"), " in party")
    pid = uuid_in(raw.get("id"), "party.id")
    kind = str(raw.get("kind") or k.party_kind)
    if kind not in ("customer", "vendor", "employee", "other"):
        raise ValidationFailed("party.kind must be customer, vendor, employee or other", field="party")
    return pid, kind


# --------------------------------------------------------------------------- withholdings


def compute_withholdings(
    uow: UnitOfWork, voucher: TreasuryVoucher, specs: list[LineSpec], day: dt.date
) -> dict[str, Any]:
    """Withholding amounts from the P06 tax engine (D-029); unknown parameters fail closed."""
    wanted = [s for s in specs if s.kind == L.Kind.WITHHOLDING]
    if not wanted:
        return {}
    if voucher.party_id is None:  # pragma: no cover - parse_lines refuses it
        raise ValidationFailed("withholdings need the voucher party", field="party")
    inputs = [TaxLineInput(str(s.line_no), s.tax_base or Decimal(0), (s.tax_code,)) for s in wanted]
    try:
        result = services.get(TaxService).withholdings(
            voucher.company_id, kind_of(voucher).doc_type, day, voucher.party_id, inputs
        )
    except DomainError as exc:
        if exc.code in ("parameter_unknown", "tax_parameter_unknown"):
            raise TaxParameterUnknown(str(exc.detail), **exc.extra) from exc
        raise
    by_line: dict[str, Any] = {r.line_ref: r for r in result.lines}
    for s in wanted:
        hit = by_line.get(str(s.line_no))
        if hit is None:
            raise TaxParameterUnknown(f"no withholding computed for {s.tax_code}", tax_code=s.tax_code)
        s.amount = q_amount(hit.amount)
    return {
        "parameters": [dict(p) for p in result.parameter_snapshots],
        "warnings": list(result.warnings),
        "rates": {str(r.line_ref): f"{r.rate:f}" for r in result.lines},
    }


def totals(specs: list[LineSpec]) -> tuple[Decimal, Decimal]:
    gross = sum((s.amount for s in specs if s.kind in INFLOW), Decimal(0))
    wht = sum((s.amount for s in specs if s.kind == L.Kind.WITHHOLDING), Decimal(0))
    return gross - wht, wht


def specs_of(voucher: TreasuryVoucher) -> list[LineSpec]:
    return [
        LineSpec(
            line_no=ln.line_no,
            kind=ln.kind,
            amount=ln.amount,
            description=ln.description,
            open_item_id=ln.open_item_id,
            open_item_document=dict(ln.open_item_document or {}),
            concept=ln.concept,
            tax_code=ln.tax_code,
            tax_base=ln.tax_base,
            dims={s: v for s in DIM_SLOTS if (v := getattr(ln, s))},
        )
        for ln in voucher.lines.select_related("concept").order_by("line_no")
    ]


def _write_lines(uow: UnitOfWork, voucher: TreasuryVoucher, specs: list[LineSpec], rates: Mapping[str, str]) -> None:
    voucher.lines.all().delete()
    for s in specs:
        L.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=voucher.company_id,
            voucher=voucher,
            line_no=s.line_no,
            kind=s.kind,
            description=s.description,
            open_item_id=s.open_item_id,
            open_item_document=s.open_item_document,
            concept=s.concept,
            tax_code=s.tax_code,
            tax_base=s.tax_base,
            tax_rate=Decimal(rates[str(s.line_no)]) if str(s.line_no) in rates else None,
            amount=s.amount,
            amount_cop=q_amount(s.amount * voucher.fx_rate),
            **{slot: s.dims.get(slot) for slot in DIM_SLOTS},
        )


def _refresh(uow: UnitOfWork, voucher: TreasuryVoucher, specs: list[LineSpec]) -> None:
    """Recompute withholdings and totals of a draft and rewrite its lines."""
    snap = compute_withholdings(uow, voucher, specs, voucher.posting_date or voucher.document_date)
    total, wht = totals(specs)
    if total <= 0:
        raise ValidationFailed("the voucher must move a positive amount through the fund", field="lines")
    voucher.total, voucher.withholding_total = total, wht
    voucher.tax_snapshot = {k: v for k, v in snap.items() if k != "rates"}
    _write_lines(uow, voucher, specs, snap.get("rates", {}))


# --------------------------------------------------------------------------- create / update / cancel


def _apply_header(
    uow: UnitOfWork, voucher: TreasuryVoucher, data: Mapping[str, Any], k: Kind, *, partial: bool
) -> None:
    ctx = uow.ctx
    company_id = voucher.company_id
    if not partial or "document_date" in data:
        voucher.document_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    if "posting_date" in data:
        voucher.posting_date = (
            None if data.get("posting_date") in (None, "") else date_in(data.get("posting_date"), "posting_date")
        )
    if not partial or "currency" in data:
        voucher.currency = currency_in(data.get("currency"))
    if not partial or "fund_id" in data:
        fid = uuid_req(data.get("fund_id"), "fund_id")
        fund = Fund.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=fid).first()
        if fund is None or fund.status != "ACTIVE":
            raise ValidationFailed("fund_id is not an active fund of the company", field="fund_id")
        voucher.fund = fund
    if voucher.fund.currency != voucher.currency:
        raise TreasuryCurrencyMismatch(
            f"fund {voucher.fund.code} holds {voucher.fund.currency}, the voucher is in {voucher.currency}"
        )
    if not partial or "payment_method" in data:
        method = str(data.get("payment_method") or "")
        if method not in V.Method.values:
            raise ValidationFailed(
                f"payment_method must be one of {', '.join(V.Method.values)}", field="payment_method"
            )
        voucher.payment_method = method
    if not partial or "party" in data:
        voucher.party_id, voucher.party_kind = _party_in(data.get("party"), k)
    if "branch_id" in data:
        voucher.branch_id = uuid_in(data.get("branch_id"), "branch_id", required=False)
    if "reference" in data:
        voucher.reference = text_in(data.get("reference"), "reference", 255)
    if "notes" in data:
        voucher.notes = text_in(data.get("notes"), "notes")
    day = voucher.posting_date or voucher.document_date
    rate = rate_on(ctx, company_id, voucher.currency, day)
    if rate is None:
        raise ValidationFailed(
            f"no exchange rate for {voucher.currency} on {day}", field="currency", code="fx_rate_missing"
        )
    voucher.fx_rate = rate


def create_voucher(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    voucher_type: str,
    data: Mapping[str, Any],
    *,
    source: DocumentRef | None = None,
) -> TreasuryVoucher:
    ctx = uow.ctx
    ctx.require_company(company_id)
    k = KINDS[V.Type(voucher_type)]
    require(ctx, f"{k.perm}.create", {"company_id": company_id})
    unknown_keys(data, HEADER_FIELDS)
    voucher = V(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        voucher_type=k.code,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    if source is not None:
        voucher.source_type, voucher.source_id, voucher.source_number = source.type, source.id, source.number or ""
    _apply_header(uow, voucher, data, k, partial=False)
    specs = parse_lines(uow, company_id, k, voucher.party_id, voucher.currency, data.get("lines"))
    voucher.save()
    _refresh(uow, voucher, specs)
    voucher.save(update_fields=["total", "withholding_total", "tax_snapshot"])
    audit(uow, f"{k.perm}.create", k.doc_type, voucher.id, company_id, {"total": f"{voucher.total:f}"})
    return voucher


def update_voucher(uow: UnitOfWork, voucher: TreasuryVoucher, data: Mapping[str, Any]) -> TreasuryVoucher:
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.update", voucher)
    unknown_keys(data, HEADER_FIELDS)
    if not data:
        raise ValidationFailed("nothing to update")
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id), voucher.id)
    if voucher.status not in EDITABLE:
        raise InvalidStateTransition(
            f"a {voucher.status} voucher cannot be edited (posted vouchers change only by reversal)",
            code="document_immutable",
        )
    _apply_header(uow, voucher, data, k, partial=True)
    raw_lines = data.get("lines")
    specs = (
        parse_lines(uow, voucher.company_id, k, voucher.party_id, voucher.currency, raw_lines)
        if raw_lines is not None
        else specs_of(voucher)
    )
    if raw_lines is None:  # header change: re-validate kept lines against the new party/currency
        for s in specs:
            if s.kind in (L.Kind.AR_APPLICATION, L.Kind.AP_APPLICATION):
                if voucher.party_id is None:
                    raise ValidationFailed("applying open items needs the voucher party", field="party")
                _check_open_item(uow, voucher.company_id, k, voucher.party_id, voucher.currency, s)
    _refresh(uow, voucher, specs)
    voucher.status = V.State.DRAFT
    voucher.save_versioned(
        ctx.principal_id,
        [
            "document_date",
            "posting_date",
            "currency",
            "fx_rate",
            "fund",
            "payment_method",
            "party_id",
            "party_kind",
            "branch_id",
            "reference",
            "notes",
            "total",
            "withholding_total",
            "tax_snapshot",
            "status",
        ],
    )
    audit(uow, f"{k.perm}.update", k.doc_type, voucher.id, voucher.company_id, {"fields": sorted(data)})
    return voucher


def cancel_voucher(uow: UnitOfWork, voucher: TreasuryVoucher) -> TreasuryVoucher:
    """DELETE of a draft: DRAFT/REJECTED → CANCELLED (never numbered; the row stays for the audit trail)."""
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.delete", voucher)
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id), voucher.id)
    if voucher.status not in EDITABLE:
        raise InvalidStateTransition(f"a {voucher.status} voucher cannot be deleted", code="document_immutable")
    voucher.status = V.State.CANCELLED
    voucher.save_versioned(ctx.principal_id, ["status"])
    audit(uow, f"{k.perm}.delete", k.doc_type, voucher.id, voucher.company_id, {"status": voucher.status})
    return voucher


# --------------------------------------------------------------------------- approvals


def approval_need(ctx: Any, voucher: TreasuryVoucher) -> tuple[bool, bool]:
    """``(needs_approval, dual)`` from the company thresholds (COP) and the concept thresholds."""
    cfg = settings_for(ctx, voucher.company_id)
    amount_cop = q_amount(voucher.total * voucher.fx_rate)
    k = kind_of(voucher)
    threshold = cfg.rc_approval_threshold if k.is_receipt else cfg.ce_approval_threshold
    needs = threshold is not None and amount_cop >= threshold
    for ln in voucher.lines.filter(kind=L.Kind.CONCEPT).select_related("concept"):
        if ln.concept is not None and ln.concept.approval_threshold is not None:
            needs = needs or amount_cop >= ln.concept.approval_threshold
    dual = not k.is_receipt and amount_cop >= cfg.dual_approval_threshold
    return needs or dual, dual


def _request_approval(uow: UnitOfWork, voucher: TreasuryVoucher, dual: bool, reason: str) -> uuid.UUID:
    code = POLICY_DUAL if dual else kind_of(voucher).policy
    try:
        req = services.get(WorkflowService).request_approval(uow, voucher.company_id, ref(voucher), code, reason)
    except NotFound as exc:  # fail closed: no policy configured → nobody can post it
        raise TreasuryApprovalRequired(
            f"approval policy {code} is not configured; the voucher cannot be approved", policy=code
        ) from exc
    return req.id


def submit(uow: UnitOfWork, voucher: TreasuryVoucher, reason: str = "") -> TreasuryVoucher:
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.update", voucher)
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id), voucher.id)
    if voucher.status not in EDITABLE:
        raise InvalidStateTransition(f"a {voucher.status} voucher cannot be submitted")
    _plan(uow, voucher, specs_of(voucher), simulate=True)  # same validations as :post
    needs, dual = approval_need(ctx, voucher)
    voucher.dual_approval = dual
    voucher.approved_by = []
    if needs:
        voucher.approval_request_id = _request_approval(uow, voucher, dual, reason)
        voucher.status = V.State.SUBMITTED
    else:
        voucher.status = V.State.APPROVED
        voucher.approved_at = clock.now()
    voucher.save_versioned(
        ctx.principal_id, ["status", "approval_request_id", "dual_approval", "approved_by", "approved_at"]
    )
    audit(uow, f"{k.perm}.submit", k.doc_type, voucher.id, voucher.company_id, {"status": voucher.status})
    return voucher


def decide(uow: UnitOfWork, voucher: TreasuryVoucher, approve: bool, reason: str) -> TreasuryVoucher:
    """Approve / reject through P07 (D-023: the creator never decides; dual approval needs two people)."""
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.approve", voucher)
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id), voucher.id)
    if voucher.status != V.State.SUBMITTED or voucher.approval_request_id is None:
        raise InvalidStateTransition(f"a {voucher.status} voucher is not waiting for approval")
    me = ctx.principal_id
    if me == voucher.created_by or (ctx.on_behalf_of and ctx.on_behalf_of == voucher.created_by):
        raise SodViolation("the creator of a voucher cannot approve or reject it (SoD)")
    if str(me) in voucher.approved_by:
        raise SodViolation("you already approved this voucher; a second approver is required")
    if not approve and not reason.strip():
        raise ValidationFailed("a rejection needs a reason", field="reason", code="reason_required")
    state = services.get(WorkflowService).decide(uow, voucher.approval_request_id, approve, reason).state
    fields = ["status", "approved_by", "approved_at"]
    if not approve or state == "REJECTED":
        voucher.status = V.State.REJECTED
        voucher.reason = reason
        fields.append("reason")
    else:
        voucher.approved_by = [*voucher.approved_by, str(me)]
        if state == "APPROVED":
            if voucher.dual_approval and len(set(voucher.approved_by)) < 2:
                raise TreasuryApprovalRequired(
                    f"policy {POLICY_DUAL} must require two different approvers", policy=POLICY_DUAL
                )
            voucher.status = V.State.APPROVED
            voucher.approved_at = clock.now()
    voucher.save_versioned(ctx.principal_id, fields)
    action = "approve" if approve else "reject"
    audit(uow, f"{k.perm}.{action}", k.doc_type, voucher.id, voucher.company_id, {"status": voucher.status}, reason)
    return voucher


# --------------------------------------------------------------------------- posting


@dataclass
class Plan:
    request: PostingRequest
    lines: list[PostingLine]
    snapshot: dict[str, Any]
    tax: dict[str, Any]
    warnings: list[str]


def _period_closed(exc: DomainError) -> bool:
    return exc.code in ("period_closed", "treasury_period_closed")


def check_period(uow: UnitOfWork, company_id: uuid.UUID, day: dt.date) -> None:
    periods = services.optional(PeriodService)
    if periods is None:
        return
    try:
        periods.assert_open(uow.ctx, company_id, day)
    except DomainError as exc:
        if _period_closed(exc) or exc.code == "not_found":
            raise TreasuryPeriodClosed(f"the period of {day} is not open for posting", date=day.isoformat()) from exc
        raise


def posting_lines(voucher: TreasuryVoucher, specs: list[LineSpec], fund_role: str) -> list[PostingLine]:
    k = kind_of(voucher)
    fund_side: Side = "DEBIT" if k.is_receipt else "CREDIT"
    other_side: Side = "CREDIT" if k.is_receipt else "DEBIT"
    party = PartyRef(voucher.party_id, voucher.party_kind or k.party_kind) if voucher.party_id else None
    cur, fx = voucher.currency, voucher.fx_rate
    memo = voucher.number or voucher.reference
    out = [PostingLine(fund_role, fund_side, voucher.total, cur, fx, memo=memo)]
    for s in specs:
        dims = dimension_values(s.dims)
        text = f"{memo} {s.description}".strip()[:500]
        if s.kind == L.Kind.AR_APPLICATION:
            out.append(PostingLine(ROLE_AR, "CREDIT", s.amount, cur, fx, party=party, dimensions=dims, memo=text))
        elif s.kind == L.Kind.AP_APPLICATION:
            out.append(PostingLine(ROLE_AP, "DEBIT", s.amount, cur, fx, party=party, dimensions=dims, memo=text))
        elif s.kind == L.Kind.ADVANCE:
            role = ROLE_CUSTOMER_ADVANCE if k.is_receipt else ROLE_VENDOR_ADVANCE
            out.append(PostingLine(role, other_side, s.amount, cur, fx, party=party, dimensions=dims, memo=text))
        elif s.kind == L.Kind.CONCEPT and s.concept is not None:
            out.append(
                PostingLine(
                    s.concept.account_role, other_side, s.amount, cur, fx, party=party, dimensions=dims, memo=text
                )
            )
        elif s.kind == L.Kind.WITHHOLDING:
            role = ROLE_WHT_RECEIVABLE if k.is_receipt else ROLE_WHT_PAYABLE
            out.append(
                PostingLine(
                    role, fund_side, s.amount, cur, fx, party=party, tax_code=s.tax_code, dimensions=dims, memo=text
                )
            )
    return out


def _plan(uow: UnitOfWork, voucher: TreasuryVoucher, specs: list[LineSpec], *, simulate: bool) -> Plan:
    """Shared by ``:simulate``, ``:submit`` and ``:post``: same validations, same journal."""
    day = voucher.posting_date or voucher.document_date
    check_period(uow, voucher.company_id, day)
    tax = compute_withholdings(uow, voucher, specs, day)
    total, wht = totals(specs)
    if total <= 0:
        raise ValidationFailed("the voucher must move a positive amount through the fund", field="lines")
    voucher.total, voucher.withholding_total = total, wht
    snapshot = funds.snapshot(uow.ctx, voucher.fund)
    warnings = list(tax.get("warnings", []))
    if not kind_of(voucher).is_receipt and Decimal(snapshot["book_balance"]) - q_amount(total * voucher.fx_rate) < 0:
        if not voucher.fund.allow_overdraft:
            raise TreasuryInsufficientFunds(
                f"fund {voucher.fund.code} has {snapshot['book_balance']} and does not allow overdraft",
                book_balance=snapshot["book_balance"],
            )
        warnings.append(INSUFFICIENT_FUNDS_WARNING)
    role = funds.account_role(uow.ctx, voucher.fund)
    lines = posting_lines(voucher, specs, role)
    request = PostingRequest(
        company_id=voucher.company_id,
        posting_date=day,
        source=ref(voucher),
        purpose="treasury_voucher",
        lines=tuple(lines),
        journal_code="BANK" if voucher.fund.fund_type == Fund.FundType.BANK else "CASH",
        branch_id=voucher.branch_id,
    )
    if simulate:
        try:
            services.get(PostingService).simulate(uow, request)
        except DomainError as exc:
            if _period_closed(exc):
                raise TreasuryPeriodClosed(str(exc.detail)) from exc
            raise
    return Plan(request, lines, snapshot, tax, warnings)


def simulate(uow: UnitOfWork, voucher: TreasuryVoucher) -> dict[str, Any]:
    """``:simulate`` — the exact journal of ``:post`` (``SimulationResult``), nothing persisted."""
    k = kind_of(voucher)
    require(uow.ctx, f"{k.perm}.read", voucher)
    plan = _plan(uow, voucher, specs_of(voucher), simulate=True)
    cur = "COP"
    rows = []
    for ln in plan.lines:
        cop = f"{q_amount(ln.amount * ln.fx_rate):f}"
        zero = "0.0000"
        rows.append(
            {
                "account": ln.account_role,
                "debit": {"amount": cop if ln.side == "DEBIT" else zero, "currency": cur},
                "credit": {"amount": cop if ln.side == "CREDIT" else zero, "currency": cur},
                "dimensions": {
                    s: str(getattr(ln.dimensions, s)) for s in DIM_SLOTS if getattr(ln.dimensions, s) is not None
                },
            }
        )
    return {
        "persisted": False,
        "documents": [
            {
                "type": k.doc_type,
                "id": str(voucher.id),
                "total": {"amount": f"{voucher.total:f}", "currency": voucher.currency},
                "withholding_total": {"amount": f"{voucher.withholding_total:f}", "currency": voucher.currency},
                "fund_snapshot": plan.snapshot,
            }
        ],
        "journal_entries": [
            {"journal": plan.request.journal_code, "posting_date": plan.request.posting_date.isoformat(), "lines": rows}
        ],
        "stock_moves": [],
        "explain": [f"{ln.side} {ln.account_role} {ln.amount:f} {ln.currency}" for ln in plan.lines],
        "warnings": [{"code": w, "message": w} for w in plan.warnings],
    }


def post(uow: UnitOfWork, voucher: TreasuryVoucher) -> TreasuryVoucher:
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.post", voucher)
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id).select_related("fund"), voucher.id)
    if voucher.status in EDITABLE:
        needs, dual = approval_need(ctx, voucher)
        if needs:
            raise TreasuryApprovalRequired(
                "the voucher reaches the approval threshold; submit it and wait for the approval", dual=dual
            )
    elif voucher.status != V.State.APPROVED:
        raise InvalidStateTransition(f"a {voucher.status} voucher cannot be posted")
    specs = specs_of(voucher)
    plan = _plan(uow, voucher, specs, simulate=False)
    cfg = settings_for(ctx, voucher.company_id)
    day = plan.request.posting_date
    voucher.number = next_number(
        uow, voucher.company_id, k.sequence, cfg.rc_prefix if k.is_receipt else cfg.ce_prefix, day
    )
    source = ref(voucher)
    apps = [(s.open_item_id, s.amount) for s in specs if s.open_item_id is not None]
    if apps:
        if k.is_receipt:
            services.get(ArOpenItems).apply(
                uow, voucher.company_id, source, [Application(oid, amount) for oid, amount in apps]
            )
        else:
            services.get(ApOpenItems).apply(uow, voucher.company_id, source, apps)
    lines = posting_lines(voucher, specs, funds.account_role(ctx, voucher.fund))
    request = PostingRequest(
        company_id=voucher.company_id,
        posting_date=day,
        source=source,
        purpose="treasury_voucher",
        lines=tuple(lines),
        journal_code=plan.request.journal_code,
        branch_id=voucher.branch_id,
    )
    try:
        entry = services.get(PostingService).post(uow, request)
    except DomainError as exc:
        if _period_closed(exc):
            raise TreasuryPeriodClosed(str(exc.detail)) from exc
        raise
    _write_lines(uow, voucher, specs, plan.tax.get("rates", {}))
    voucher.lines.update(posted=True)
    voucher.status = V.State.POSTED
    voucher.posting_date = day
    voucher.posted, voucher.posted_at, voucher.posted_by = True, clock.now(), ctx.principal_id
    voucher.posted_entry_id = entry.entry_id
    voucher.fund_snapshot = plan.snapshot
    voucher.tax_snapshot = {k2: v for k2, v in plan.tax.items() if k2 != "rates"}
    voucher.warnings = plan.warnings
    voucher.save_versioned(
        ctx.principal_id,
        [
            "number",
            "status",
            "posting_date",
            "posted",
            "posted_at",
            "posted_by",
            "posted_entry_id",
            "fund_snapshot",
            "tax_snapshot",
            "warnings",
            "total",
            "withholding_total",
        ],
    )
    party = (
        {"id": str(voucher.party_id), "kind": voucher.party_kind or k.party_kind}
        if voucher.party_id
        else {"id": str(voucher.company_id), "kind": "other"}  # pure concept voucher: the company itself
    )
    key = "receipt_id" if k.is_receipt else "disbursement_id"
    uow.emit(
        f"{k.event}.posted.v1",
        {
            key: str(voucher.id),
            "number": voucher.number,
            "party": party,
            "amount": {"amount": f"{q_amount(voucher.total):f}", "currency": voucher.currency},
            "journal_entry_id": str(entry.entry_id),
        },
        company_id=voucher.company_id,
    )
    audit(
        uow,
        f"{k.perm}.post",
        k.doc_type,
        voucher.id,
        voucher.company_id,
        {"number": voucher.number, "journal_entry": str(entry.entry_id)},
    )
    return voucher


def reverse(uow: UnitOfWork, voucher: TreasuryVoucher, reason: str, on: dt.date | None = None) -> TreasuryVoucher:
    """POSTED → REVERSED: mirror entry in an open period, open items re-opened, numbered reversal voucher."""
    ctx = uow.ctx
    k = kind_of(voucher)
    require(ctx, f"{k.perm}.void", voucher)
    if not reason.strip():
        raise ValidationFailed("a reversal needs a reason", field="reason", code="reason_required")
    voucher = lock(V.objects.filter(tenant_id=ctx.tenant_id).select_related("fund"), voucher.id)
    if voucher.status != V.State.POSTED or voucher.reversal_of_id is not None or voucher.posted_entry_id is None:
        raise InvalidStateTransition(f"a {voucher.status} voucher cannot be reversed")
    day = on or clock.business_date()
    check_period(uow, voucher.company_id, day)
    source = ref(voucher)
    if voucher.lines.filter(open_item_id__isnull=False).exists():
        if k.is_receipt:
            services.get(ArOpenItems).unapply(uow, voucher.company_id, source)
        else:
            services.get(ApOpenItems).unapply(uow, voucher.company_id, source)
    try:
        entry = services.get(PostingService).reverse(uow, voucher.posted_entry_id, day, reason)
    except DomainError as exc:
        if _period_closed(exc):
            raise TreasuryPeriodClosed(str(exc.detail)) from exc
        raise
    cfg = settings_for(ctx, voucher.company_id)
    mirror = V.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=voucher.company_id,
        voucher_type=voucher.voucher_type,
        number=next_number(uow, voucher.company_id, k.sequence, cfg.rc_prefix if k.is_receipt else cfg.ce_prefix, day),
        document_date=day,
        posting_date=day,
        branch_id=voucher.branch_id,
        party_id=voucher.party_id,
        party_kind=voucher.party_kind,
        currency=voucher.currency,
        fx_rate=voucher.fx_rate,
        fund=voucher.fund,
        payment_method=voucher.payment_method,
        reference=voucher.number,
        status=V.State.POSTED,
        total=voucher.total,
        withholding_total=voucher.withholding_total,
        notes=f"Reversión de {voucher.number}",
        reason=reason,
        source_type=k.doc_type,
        source_id=voucher.id,
        source_number=voucher.number,
        approved_by=list(voucher.approved_by),
        posted=True,
        posted_at=clock.now(),
        posted_by=ctx.principal_id,
        posted_entry_id=entry.entry_id,
        reversal_of=voucher,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    for ln in voucher.lines.order_by("line_no"):
        L.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=voucher.company_id,
            voucher=mirror,
            line_no=ln.line_no,
            kind=ln.kind,
            description=ln.description,
            open_item_id=ln.open_item_id,
            open_item_document=ln.open_item_document,
            concept=ln.concept,
            tax_code=ln.tax_code,
            tax_base=ln.tax_base,
            tax_rate=ln.tax_rate,
            amount=ln.amount,
            amount_cop=ln.amount_cop,
            posted=True,
            **{slot: getattr(ln, slot) for slot in DIM_SLOTS},
        )
    # The DB trigger lets a posted header change only status POSTED → REVERSED and its link (C1-5).
    changed = V.objects.filter(pk=voucher.pk, version=voucher.version, status=V.State.POSTED).update(
        status=V.State.REVERSED,
        reversed_by_id=mirror.id,
        version=voucher.version + 1,
        updated_at=clock.now(),
        updated_by=ctx.principal_id,
    )
    if changed != 1:  # pragma: no cover - the row is locked above
        raise InvalidStateTransition("the voucher changed concurrently")
    key = "receipt_id" if k.is_receipt else "disbursement_id"
    uow.emit(
        f"{k.event}.reversed.v1",
        {key: str(voucher.id), "reversal_entry_id": str(entry.entry_id), "reason": reason[:10000]},
        company_id=voucher.company_id,
    )
    audit(
        uow,
        f"{k.perm}.void",
        k.doc_type,
        voucher.id,
        voucher.company_id,
        {"reversal_voucher": str(mirror.id), "reversal_entry": str(entry.entry_id)},
        reason,
    )
    voucher.refresh_from_db()
    return voucher
