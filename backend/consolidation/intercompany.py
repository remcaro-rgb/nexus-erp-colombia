"""Intercompany automation (P16 [P], shared/07 §6, S10).

* Agreements between company pairs (A sells to B) with account roles, pricing rule, auto-mirror, warehouse.
* IC sale: A's entries and B's **mirror vendor invoice** in the caller's unit of work, linked by ``ic_link_id``.
  The user posting in A needs ``intercompany.document.post`` in A; B's side is written by the IC **system
  principal** acting on behalf of that user (recorded causation in the document, the ledger entry and the
  events). A failure on either side rolls back both (one database transaction).
* IC settlement (B pays A) and netting, both sides at once.
* IC reconciliation report: A's IC position against B must mirror B's position against A.

Journal lines against the partner carry ``party = (company id, kind "company")`` so balances can be paired.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction

from consolidation import access, ledgerdata
from consolidation.errors import DocumentImmutable, InvalidStateTransition, invalid
from consolidation.interface import IcLine, IcResult, IcSaleRequest, IcSettlementRequest
from consolidation.models import (
    DEFAULT_ROLES,
    HUNDRED,
    IcDocumentType,
    IntercompanyAgreement,
    IntercompanyDocument,
    IntercompanyLink,
    LinkKind,
    MasterStatus,
    PricingRule,
)
from kernel import clock, sequences, services, tenancy
from kernel.errors import NotFound, SequenceNotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingResult, PostingService
from kernel.models import Sequence
from kernel.tenancy import PrincipalType, TenantContext
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
Q4 = Decimal("0.0001")
#: Deterministic id of the intercompany system principal (writes B-side documents on behalf of the user).
SYSTEM_PRINCIPAL = uuid.uuid5(uuid.NAMESPACE_URL, "urn:nexus-erp:system:intercompany")
MIRROR_SOURCE = "intercompany.mirror_vendor_invoice"
SETTLEMENT_SOURCE = "intercompany.settlement"
NETTING_SOURCE = "intercompany.netting"
POST_PERMISSION = "intercompany.document.post"
ACCOUNT_KEYS = (*DEFAULT_ROLES, "pricing_rule", "markup_pct", "auto_mirror", "default_warehouse_id")
SEQ_BY_TYPE = {
    IcDocumentType.MIRROR_VENDOR_INVOICE: ("IC-FPM", "FPM-"),
    IcDocumentType.SETTLEMENT: ("IC-PAG", "PIC-"),
    IcDocumentType.NETTING: ("IC-CRU", "CIC-"),
}


# --------------------------------------------------------------------------- agreements


def _uuid(value: Any, pointer: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise invalid(f"{pointer.strip('/')} must be a UUID", pointer, "format") from None


def _apply_accounts(obj: IntercompanyAgreement, accounts: Any) -> None:
    if not isinstance(accounts, dict):
        raise invalid("accounts must be an object", "/accounts", "type")
    unknown = sorted(set(accounts) - set(ACCOUNT_KEYS))
    if unknown:
        raise invalid(f"unknown account key(s): {', '.join(unknown)}", f"/accounts/{unknown[0]}", "unknown")
    roles = {k: v for k, v in accounts.items() if k in DEFAULT_ROLES}
    for k, v in roles.items():
        if not isinstance(v, str) or not v.strip() or len(v) > 60:
            raise invalid(f"accounts.{k} must be an account role code", f"/accounts/{k}", "format")
    obj.accounts = {**{k: v for k, v in (obj.accounts or {}).items() if k in DEFAULT_ROLES}, **roles}
    if "pricing_rule" in accounts:
        if accounts["pricing_rule"] not in PricingRule.values:
            raise invalid("pricing_rule must be TRANSFER_PRICE or COST_PLUS", "/accounts/pricing_rule", "enum")
        obj.pricing_rule = accounts["pricing_rule"]
    if "markup_pct" in accounts:
        try:
            pct = Decimal(str(accounts["markup_pct"]))
        except ArithmeticError:
            raise invalid("markup_pct must be a decimal string", "/accounts/markup_pct", "format") from None
        if isinstance(accounts["markup_pct"], float) or not ZERO <= pct <= Decimal(1000):
            raise invalid("markup_pct must be a decimal string between 0 and 1000", "/accounts/markup_pct", "range")
        obj.markup_pct = pct
    if "auto_mirror" in accounts:
        if not isinstance(accounts["auto_mirror"], bool):
            raise invalid("auto_mirror must be a boolean", "/accounts/auto_mirror", "type")
        obj.auto_mirror = accounts["auto_mirror"]
    if "default_warehouse_id" in accounts:
        raw = accounts["default_warehouse_id"]
        obj.default_warehouse_id = None if raw is None else _uuid(raw, "/accounts/default_warehouse_id")


def agreement_accounts(obj: IntercompanyAgreement) -> dict[str, Any]:
    out: dict[str, Any] = {k: obj.role(k) for k in DEFAULT_ROLES}
    out.update(
        pricing_rule=obj.pricing_rule,
        markup_pct=str(obj.markup_pct),
        auto_mirror=obj.auto_mirror,
        default_warehouse_id=str(obj.default_warehouse_id) if obj.default_warehouse_id else None,
    )
    return out


def visible_agreements(ctx: TenantContext, action: str = "intercompany.agreement.read") -> list[IntercompanyAgreement]:
    """Agreements of companies the principal may read (either side)."""
    qs = IntercompanyAgreement.objects.filter(tenant_id=ctx.tenant_id)
    return [a for a in qs if access.allowed(ctx, action, [a.company_a_id]) or access.allowed(ctx, action, [a.company_b_id])]


def get_agreement(ctx: TenantContext, pk: uuid.UUID, action: str, lock: bool = False) -> IntercompanyAgreement:
    qs = IntercompanyAgreement.objects.filter(tenant_id=ctx.tenant_id, id=pk)
    if lock:
        qs = qs.select_for_update()
    obj = qs.first()
    if obj is None:
        raise NotFound("agreement not found")
    if action.endswith(".read"):
        if not (access.allowed(ctx, action, [obj.company_a_id]) or access.allowed(ctx, action, [obj.company_b_id])):
            raise NotFound("agreement not found")
    elif not access.allowed(ctx, action, [obj.company_a_id, obj.company_b_id]):
        if obj.company_a_id in ctx.company_ids or obj.company_b_id in ctx.company_ids:
            access.require(ctx, action, [obj.company_a_id, obj.company_b_id])
        raise NotFound("agreement not found")
    return obj


def create_agreement(uow: UnitOfWork, data: dict[str, Any]) -> IntercompanyAgreement:
    ctx = uow.ctx
    for key in ("code", "name", "company_a_id", "company_b_id", "accounts"):
        if data.get(key) in (None, ""):
            raise invalid(f"{key} is required", f"/{key}", "required")
    a, b = _uuid(data["company_a_id"], "/company_a_id"), _uuid(data["company_b_id"], "/company_b_id")
    if a == b:
        raise invalid("an agreement needs two different companies", "/company_b_id", "same_company")
    access.require(ctx, "intercompany.agreement.create", [a, b])
    obj = IntercompanyAgreement(
        tenant_id=ctx.tenant_id,
        code=_text(data["code"], "/code", 40),
        name=_text(data["name"], "/name", 200),
        company_a_id=a,
        company_b_id=b,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    _apply_accounts(obj, data["accounts"])
    try:
        with transaction.atomic(using=uow.using):
            obj.save()
    except IntegrityError:
        raise invalid("an agreement with this code or company pair already exists", "/code", "duplicate") from None
    access.audit(uow, "create", "intercompany.agreement", obj.id, a, {"code": obj.code, "company_b_id": str(b)})
    return obj


def update_agreement(uow: UnitOfWork, obj: IntercompanyAgreement, data: dict[str, Any]) -> IntercompanyAgreement:
    if obj.status == MasterStatus.ARCHIVED:
        raise InvalidStateTransition("an archived agreement cannot be changed")
    fields = []
    if "code" in data:
        obj.code = _text(data["code"], "/code", 40)
        fields.append("code")
    if "name" in data:
        obj.name = _text(data["name"], "/name", 200)
        fields.append("name")
    for key in ("company_a_id", "company_b_id"):
        if key in data and _uuid(data[key], f"/{key}") != getattr(obj, key):
            if obj.links.exists():
                raise DocumentImmutable("the companies of an agreement with IC documents cannot change")
            raise invalid("the company pair of an agreement cannot change; create a new agreement", f"/{key}", "immutable")
    if "accounts" in data:
        _apply_accounts(obj, data["accounts"])
        fields += ["accounts", "pricing_rule", "markup_pct", "auto_mirror", "default_warehouse_id"]
    if fields:
        try:
            with transaction.atomic(using=uow.using):
                obj.save_versioned(uow.ctx.principal_id, fields)
        except IntegrityError:
            raise invalid("an agreement with this code already exists", "/code", "duplicate") from None
        access.audit(uow, "update", "intercompany.agreement", obj.id, obj.company_a_id, {"fields": fields})
    return obj


def archive_agreement(uow: UnitOfWork, obj: IntercompanyAgreement) -> None:
    """Masters are never deleted once used (C1-5): ``DELETE`` archives."""
    if obj.status == MasterStatus.ARCHIVED:
        return
    obj.status = MasterStatus.ARCHIVED
    obj.save_versioned(uow.ctx.principal_id, ["status"])
    access.audit(uow, "archive", "intercompany.agreement", obj.id, obj.company_a_id)


def _text(value: Any, pointer: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise invalid(f"{pointer.strip('/')} must be a non-empty string of at most {max_len}", pointer, "format")
    return value.strip()


# --------------------------------------------------------------------------- system principal


@contextlib.contextmanager
def acting_as_system(uow: UnitOfWork, companies: set[uuid.UUID]) -> Iterator[TenantContext]:
    """Swap the unit of work to the IC system principal (``on_behalf_of`` = the user) for ``companies``.

    Same transaction, same tenant: the B-side rows commit or roll back with A's. Restored afterwards; on an error
    the transaction is aborted anyway and the original context is restored without touching the connection.
    """
    original = uow.ctx
    user = original.on_behalf_of if original.principal_type != PrincipalType.USER else original.principal_id
    system = TenantContext(
        tenant_id=original.tenant_id,
        company_ids=frozenset(original.company_ids | companies),
        principal_id=SYSTEM_PRINCIPAL,
        principal_type=PrincipalType.SERVICE,
        on_behalf_of=user or original.principal_id,
        channel=original.channel,
        correlation_id=original.correlation_id,
    )
    uow.ctx = system
    try:
        with tenancy.activate(system):
            tenancy.bind(system, uow.using)
            yield system
    except BaseException:
        uow.ctx = original
        raise
    uow.ctx = original
    tenancy.bind(original, uow.using)


def _agreement_for(ctx: TenantContext, a: uuid.UUID, b: uuid.UUID, lock: bool = True) -> IntercompanyAgreement:
    qs = IntercompanyAgreement.objects.filter(tenant_id=ctx.tenant_id, company_a_id=a, company_b_id=b)
    if lock:
        qs = qs.select_for_update()
    obj = qs.first()
    if obj is None:
        raise invalid("no intercompany agreement for this company pair", "/company_b_id", "agreement_missing")
    if obj.status != MasterStatus.ACTIVE:
        raise InvalidStateTransition("the intercompany agreement is archived")
    return obj


def _number(uow: UnitOfWork, company: uuid.UUID, doc_type: str, day: dt.date) -> str:
    code, prefix = SEQ_BY_TYPE[IcDocumentType(doc_type)]
    try:
        return sequences.allocate(company, code, prefix, on_date=day).formatted
    except SequenceNotFound:
        try:
            with transaction.atomic(using=uow.using):
                Sequence.objects.create(
                    tenant_id=uow.ctx.tenant_id, company_id=company, sequence_code=code, prefix=prefix, padding=6
                )
        except IntegrityError:
            pass  # created concurrently
        return sequences.allocate(company, code, prefix, on_date=day).formatted


def _post(uow: UnitOfWork, request: PostingRequest) -> PostingResult:
    return services.get(PostingService).post(uow, request)


def _q(value: Decimal) -> Decimal:
    return value.quantize(Q4)


def _validate_lines(lines: tuple[IcLine, ...]) -> None:
    if not lines:
        raise invalid("an intercompany document needs lines", "/lines", "required")
    for i, ln in enumerate(lines):
        for name in ("quantity", "unit_price", "tax_amount", "unit_cost"):
            v = getattr(ln, name)
            if not isinstance(v, Decimal) or v < 0:
                raise invalid(f"{name} must be a non-negative Decimal", f"/lines/{i}/{name}", "format")
        if ln.quantity == ZERO:
            raise invalid("quantity must be positive", f"/lines/{i}/quantity", "range")


def _check_pricing(agreement: IntercompanyAgreement, lines: tuple[IcLine, ...]) -> None:
    if agreement.pricing_rule != PricingRule.COST_PLUS:
        return
    factor = Decimal(1) + agreement.markup_pct / HUNDRED
    for i, ln in enumerate(lines):
        if ln.item_id is not None and _q(ln.unit_cost * factor) != _q(ln.unit_price):
            raise invalid(
                f"unit_price must be cost × (1 + {agreement.markup_pct} %) under the COST_PLUS agreement",
                f"/lines/{i}/unit_price",
                "pricing_rule",
            )


# --------------------------------------------------------------------------- IC sale (S10)


def post_sale(uow: UnitOfWork, request: IcSaleRequest) -> IcResult:
    """A's IC sales entries + B's mirror, atomically (S10)."""
    ctx = uow.ctx
    access.require(ctx, POST_PERMISSION, [request.company_a_id])
    agreement = _agreement_for(ctx, request.company_a_id, request.company_b_id)
    prior = _existing_link(ctx, LinkKind.SALE, request.source)
    if prior is not None:
        return _result(prior, replayed=True)
    _validate_lines(request.lines)
    _check_pricing(agreement, request.lines)
    subtotal = sum((ln.subtotal for ln in request.lines), ZERO)
    tax = sum((ln.tax_amount for ln in request.lines), ZERO)
    cost = sum((ln.cost for ln in request.lines), ZERO)
    party_b = PartyRef(request.company_b_id, ledgerdata.COMPANY_PARTY_KIND)
    lines = [
        PostingLine(agreement.role("ic_receivable"), "DEBIT", subtotal + tax, request.currency, party=party_b),
        PostingLine(agreement.role("revenue"), "CREDIT", subtotal, request.currency, party=party_b),
    ]
    if tax:
        lines.append(PostingLine(agreement.role("vat_payable"), "CREDIT", tax, request.currency, party=party_b))
    if cost:
        lines += [
            PostingLine(agreement.role("cogs"), "DEBIT", cost, request.currency),
            PostingLine(agreement.role("inventory"), "CREDIT", cost, request.currency),
        ]
    posted = _post(
        uow,
        PostingRequest(
            request.company_a_id,
            request.document_date,
            request.source,
            "ic_sale",
            tuple(lines),
            journal_code="GENERAL",
        ),
    )
    return mirror_sales_invoice(uow, replace(request, a_entry_ids=(posted.entry_id,)), agreement=agreement)


def mirror_sales_invoice(
    uow: UnitOfWork, request: IcSaleRequest, agreement: IntercompanyAgreement | None = None
) -> IcResult:
    """B's mirror vendor invoice for A's IC sales invoice ``request.source`` (inside the caller's UoW)."""
    ctx = uow.ctx
    access.require(ctx, POST_PERMISSION, [request.company_a_id])
    agreement = agreement or _agreement_for(ctx, request.company_a_id, request.company_b_id)
    prior = _existing_link(ctx, LinkKind.SALE, request.source)
    if prior is not None:
        return _result(prior, replayed=True)
    _validate_lines(request.lines)
    if not agreement.auto_mirror:
        raise InvalidStateTransition("auto-mirror is off for this agreement; B registers the vendor invoice")
    subtotal = sum((ln.subtotal for ln in request.lines), ZERO)
    tax = sum((ln.tax_amount for ln in request.lines), ZERO)
    goods = sum((ln.subtotal for ln in request.lines if ln.item_id is not None), ZERO)
    cost = sum((ln.cost for ln in request.lines if ln.item_id is not None), ZERO)
    services_amount = subtotal - goods
    user_ctx = ctx
    with acting_as_system(uow, {request.company_b_id}) as system:
        number = _number(uow, request.company_b_id, IcDocumentType.MIRROR_VENDOR_INVOICE, request.document_date)
        doc = IntercompanyDocument(
            tenant_id=system.tenant_id,
            agreement=agreement,
            company_id=request.company_b_id,
            doc_type=IcDocumentType.MIRROR_VENDOR_INVOICE,
            number=number,
            document_date=request.document_date,
            counterpart_company_id=request.company_a_id,
            source_type=request.source.type,
            source_id=request.source.id,
            source_number=request.source.number or "",
            currency=request.currency,
            subtotal=_q(subtotal),
            tax_amount=_q(tax),
            total=_q(subtotal + tax),
            lines=[_line_json(i, ln) for i, ln in enumerate(request.lines, start=1)],
            created_by=system.principal_id,
            on_behalf_of=system.on_behalf_of,
            created_at=clock.now(),
        )
        doc.save()
        party_a = PartyRef(request.company_a_id, ledgerdata.COMPANY_PARTY_KIND)
        lines: list[PostingLine] = []
        if goods:
            lines.append(PostingLine(agreement.role("inventory"), "DEBIT", goods, request.currency))
        if services_amount:
            lines.append(PostingLine(agreement.role("expense"), "DEBIT", services_amount, request.currency))
        if tax:
            lines.append(PostingLine(agreement.role("vat_recoverable"), "DEBIT", tax, request.currency))
        lines.append(PostingLine(agreement.role("ic_payable"), "CREDIT", subtotal + tax, request.currency, party=party_a))
        posted = _post(
            uow,
            PostingRequest(
                request.company_b_id,
                request.document_date,
                DocumentRef(MIRROR_SOURCE, doc.id, number),
                "ic_mirror",
                tuple(lines),
            ),
        )
        doc.entry_ids = [str(posted.entry_id)]
        IntercompanyDocument.objects.filter(pk=doc.pk).update(entry_ids=doc.entry_ids)
        link = IntercompanyLink.objects.create(
            tenant_id=system.tenant_id,
            agreement=agreement,
            kind=LinkKind.SALE,
            company_a_id=request.company_a_id,
            company_b_id=request.company_b_id,
            document_date=request.document_date,
            a_document_type=request.source.type,
            a_document_id=request.source.id,
            a_document_number=request.source.number or "",
            a_entry_ids=[str(x) for x in request.a_entry_ids],
            b_document_id=doc.id,
            b_entry_ids=[str(posted.entry_id)],
            currency=request.currency,
            amount=_q(subtotal),
            tax_amount=_q(tax),
            cost_amount=_q(cost),
            goods_amount=_q(goods),
            created_by=user_ctx.principal_id,
            on_behalf_of=system.on_behalf_of,
            created_at=clock.now(),
        )
        access.audit(
            uow,
            "create",
            "intercompany.document",
            doc.id,
            request.company_b_id,
            {"number": number, "ic_link_id": str(link.id), "source": request.source.type, "on_behalf_of": str(user_ctx.principal_id)},
            "mirror of an intercompany sales invoice",
        )
    access.audit(uow, "post", "intercompany.link", link.id, request.company_a_id, {"b_document": number})
    return _result(link)


def _line_json(no: int, ln: IcLine) -> dict[str, Any]:
    return {
        "line_no": no,
        "description": ln.description[:255],
        "item_id": str(ln.item_id) if ln.item_id else None,
        "quantity": str(ln.quantity),
        "unit_price": str(ln.unit_price),
        "tax_amount": str(ln.tax_amount),
        "amount": str(_q(ln.subtotal)),
    }


def _existing_link(ctx: TenantContext, kind: str, source: DocumentRef) -> IntercompanyLink | None:
    return IntercompanyLink.objects.filter(
        tenant_id=ctx.tenant_id, kind=kind, a_document_type=source.type, a_document_id=source.id
    ).first()


def _result(link: IntercompanyLink, replayed: bool = False) -> IcResult:
    doc = IntercompanyDocument.objects.filter(id=link.b_document_id).first() if link.b_document_id else None
    return IcResult(
        link.id,
        link.b_document_id,
        doc.number if doc else "",
        tuple(uuid.UUID(x) for x in link.a_entry_ids),
        tuple(uuid.UUID(x) for x in link.b_entry_ids),
        replayed,
    )


# --------------------------------------------------------------------------- settlement / netting


def settle(uow: UnitOfWork, request: IcSettlementRequest) -> IcResult:
    """B pays A: B Dr IC_PAYABLE / Cr BANK; A Dr BANK / Cr IC_RECEIVABLE (one transaction)."""
    return _two_sided(uow, request, LinkKind.SETTLEMENT)


def net(uow: UnitOfWork, request: IcSettlementRequest) -> IcResult:
    """Netting without cash: each side offsets its IC payable against its IC receivable with the partner."""
    return _two_sided(uow, request, LinkKind.NETTING)


def _two_sided(uow: UnitOfWork, request: IcSettlementRequest, kind: str) -> IcResult:
    ctx = uow.ctx
    access.require(ctx, POST_PERMISSION, [request.company_a_id])
    agreement = _agreement_for(ctx, request.company_a_id, request.company_b_id)
    prior = _existing_link(ctx, kind, request.source)
    if prior is not None:
        return _result(prior, replayed=True)
    if not isinstance(request.amount, Decimal) or request.amount <= 0:
        raise invalid("amount must be a positive Decimal", "/amount", "range")
    amount = _q(request.amount)
    party_a = PartyRef(request.company_a_id, ledgerdata.COMPANY_PARTY_KIND)
    party_b = PartyRef(request.company_b_id, ledgerdata.COMPANY_PARTY_KIND)
    if kind == LinkKind.SETTLEMENT:
        a_lines = (
            PostingLine(agreement.role("bank"), "DEBIT", amount, request.currency),
            PostingLine(agreement.role("ic_receivable"), "CREDIT", amount, request.currency, party=party_b),
        )
        b_lines = (
            PostingLine(agreement.role("ic_payable"), "DEBIT", amount, request.currency, party=party_a),
            PostingLine(agreement.role("bank"), "CREDIT", amount, request.currency),
        )
        doc_type, source_type = IcDocumentType.SETTLEMENT, SETTLEMENT_SOURCE
    else:
        a_lines = (
            PostingLine(agreement.role("ic_payable"), "DEBIT", amount, request.currency, party=party_b),
            PostingLine(agreement.role("ic_receivable"), "CREDIT", amount, request.currency, party=party_b),
        )
        b_lines = (
            PostingLine(agreement.role("ic_payable"), "DEBIT", amount, request.currency, party=party_a),
            PostingLine(agreement.role("ic_receivable"), "CREDIT", amount, request.currency, party=party_a),
        )
        doc_type, source_type = IcDocumentType.NETTING, NETTING_SOURCE
    a_post = _post(uow, PostingRequest(request.company_a_id, request.document_date, request.source, f"ic_{kind.lower()}", a_lines))
    user_ctx = ctx
    with acting_as_system(uow, {request.company_b_id}) as system:
        number = _number(uow, request.company_b_id, doc_type, request.document_date)
        doc = IntercompanyDocument.objects.create(
            tenant_id=system.tenant_id,
            agreement=agreement,
            company_id=request.company_b_id,
            doc_type=doc_type,
            number=number,
            document_date=request.document_date,
            counterpart_company_id=request.company_a_id,
            source_type=request.source.type,
            source_id=request.source.id,
            source_number=request.source.number or "",
            currency=request.currency,
            subtotal=amount,
            total=amount,
            created_by=system.principal_id,
            on_behalf_of=system.on_behalf_of,
            created_at=clock.now(),
        )
        b_post = _post(
            uow,
            PostingRequest(request.company_b_id, request.document_date, DocumentRef(source_type, doc.id, number), f"ic_{kind.lower()}", b_lines),
        )
        IntercompanyDocument.objects.filter(pk=doc.pk).update(entry_ids=[str(b_post.entry_id)])
        link = IntercompanyLink.objects.create(
            tenant_id=system.tenant_id,
            agreement=agreement,
            kind=kind,
            company_a_id=request.company_a_id,
            company_b_id=request.company_b_id,
            document_date=request.document_date,
            a_document_type=request.source.type,
            a_document_id=request.source.id,
            a_document_number=request.source.number or "",
            a_entry_ids=[str(a_post.entry_id)],
            b_document_id=doc.id,
            b_entry_ids=[str(b_post.entry_id)],
            currency=request.currency,
            amount=amount,
            created_by=user_ctx.principal_id,
            on_behalf_of=system.on_behalf_of,
            created_at=clock.now(),
        )
        access.audit(uow, "create", "intercompany.document", doc.id, request.company_b_id, {"number": number, "kind": kind})
    return _result(link)


def record_consumption(uow: UnitOfWork, link_id: uuid.UUID, amount: Decimal) -> None:
    """The buyer consumed/sold ``amount`` (transfer-price value) of the IC goods of ``link_id``."""
    ctx = uow.ctx
    link = IntercompanyLink.objects.select_for_update().filter(tenant_id=ctx.tenant_id, id=link_id, kind=LinkKind.SALE).first()
    if link is None or link.company_b_id not in ctx.company_ids:
        raise NotFound("intercompany link not found")
    access.require(ctx, POST_PERMISSION, [link.company_b_id])
    if not isinstance(amount, Decimal) or amount <= 0:
        raise invalid("amount must be a positive Decimal", "/amount", "range")
    new = _q(link.consumed_amount + amount)
    if new > link.goods_amount:
        raise ValidationFailed("consumption exceeds the IC goods of the link", pointer="/amount", reason="range")
    IntercompanyLink.objects.filter(pk=link.pk).update(consumed_amount=new)
    access.audit(uow, "update", "intercompany.link", link.id, link.company_b_id, {"consumed_amount": str(new)})


# --------------------------------------------------------------------------- reconciliation


def reconciliation_rows(
    ctx: TenantContext,
    until: dt.date,
    company_a: uuid.UUID | None = None,
    company_b: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Per agreement: A's net IC position vs B (debit-positive) against B's net position vs A.

    Mirrors when ``a_position + b_position == 0``. Only agreements whose two companies the principal may read
    (``intercompany.reconciliation.read``) are listed — nothing about a hidden company leaks.
    """
    rows = []
    action = "intercompany.reconciliation.read"
    for ag in IntercompanyAgreement.objects.filter(tenant_id=ctx.tenant_id).order_by("code"):
        if company_a and ag.company_a_id != company_a:
            continue
        if company_b and ag.company_b_id != company_b:
            continue
        if not access.allowed(ctx, action, [ag.company_a_id, ag.company_b_id]):
            continue
        a_pos = position(ctx, ag, ag.company_a_id, ag.company_b_id, until)
        b_pos = position(ctx, ag, ag.company_b_id, ag.company_a_id, until)
        diff = _q(a_pos + b_pos)
        rows.append(
            {
                "agreement_id": str(ag.id),
                "agreement": ag.code,
                "company_a_id": str(ag.company_a_id),
                "company_b_id": str(ag.company_b_id),
                "a_receivable_net": str(_q(a_pos)),
                "b_payable_net": str(_q(-b_pos)),
                "difference": str(diff),
                "status": "MIRRORED" if diff == ZERO else "MISMATCH",
            }
        )
    return rows


def ic_accounts(ctx: TenantContext, ag: IntercompanyAgreement, company: uuid.UUID) -> list[str]:
    codes: list[str] = []
    for key in ("ic_receivable", "ic_payable"):
        for code in ledgerdata.role_accounts(ctx, company, ag.role(key)):
            if code not in codes:
                codes.append(code)
    return codes


def position(
    ctx: TenantContext, ag: IntercompanyAgreement, holder: uuid.UUID, counterpart: uuid.UUID, until: dt.date
) -> Decimal:
    balances = ledgerdata.party_balances(ctx, holder, counterpart, ic_accounts(ctx, ag, holder), until)
    return sum(balances.values(), ZERO)


def link_to_json(link: IntercompanyLink) -> dict[str, Any]:
    return {
        "ic_link_id": str(link.id),
        "kind": link.kind,
        "agreement_id": str(link.agreement_id),
        "company_a_id": str(link.company_a_id),
        "company_b_id": str(link.company_b_id),
        "document_date": link.document_date.isoformat(),
        "a_document": {"type": link.a_document_type, "id": str(link.a_document_id), "number": link.a_document_number},
        "b_document_id": str(link.b_document_id) if link.b_document_id else None,
        "amount": {"amount": str(link.amount), "currency": link.currency},
    }


class DbIntercompanyService:
    """``IntercompanyService`` implementation (``consolidation.interface``)."""

    def mirror_sales_invoice(self, uow: UnitOfWork, request: IcSaleRequest) -> IcResult:
        return mirror_sales_invoice(uow, request)

    def post_sale(self, uow: UnitOfWork, request: IcSaleRequest) -> IcResult:
        return post_sale(uow, request)

    def settle(self, uow: UnitOfWork, request: IcSettlementRequest) -> IcResult:
        return settle(uow, request)

    def net(self, uow: UnitOfWork, request: IcSettlementRequest) -> IcResult:
        return net(uow, request)

    def record_consumption(self, uow: UnitOfWork, link_id: uuid.UUID, amount: Decimal) -> None:
        record_consumption(uow, link_id, amount)
