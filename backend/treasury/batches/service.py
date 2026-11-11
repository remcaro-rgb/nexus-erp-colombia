"""Payment batches (lotes de pago) — F-TRE-CE: approval with SoD (dual above the threshold) and execution into one
numbered CE per payee, plus a simulated generic bank file labelled ``FIXTURE_NOT_CERTIFIED`` (no live bank; the
per-bank formats are pending EG-5).

Batch lines use the contract ``PaymentBatchLine`` plus the additive fields of CCR P16a-1: ``party`` (payee, a P10
party id), ``open_item_id`` (P11 AP open item; omitted = vendor advance) and ``applied_amount``. A ``tax_code`` on a
line is a withholding computed by the P06 engine on the line amount (D-029).
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock, ids, services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.workflow import WorkflowService
from kernel.money import q_amount
from kernel.uow import UnitOfWork
from treasury.errors import (
    InvalidStateTransition,
    SodViolation,
    TreasuryApprovalRequired,
    TreasuryOpenItemMismatchParty,
    TreasuryOverapplication,
)
from treasury.interfaces import BANK_FILE_LABEL, BATCH_TYPE, POLICY_CE, POLICY_DUAL, ApOpenItems
from treasury.models import DIM_SLOTS, Fund, PaymentBatch, PaymentBatchLine, TreasuryVoucher
from treasury.services.common import (
    SEQ_BATCH,
    audit,
    currency_in,
    date_in,
    dims_in,
    lock,
    money_in,
    next_number,
    qty_in,
    require,
    settings_for,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)
from treasury.vouchers import service as vouchers

B = PaymentBatch
HEADER_FIELDS = ("document_date", "posting_date", "currency", "notes", "fund_id", "payment_date", "lines")
LINE_FIELDS = (
    "line_no",
    "item_id",
    "description",
    "quantity",
    "unit_price",
    "amount",
    "tax_code",
    "account_code",
    "dimensions",
    # CCR P16a-1 (additive): payee and AP open item of the line
    "party",
    "open_item_id",
    "applied_amount",
)


def ref(batch: PaymentBatch) -> DocumentRef:
    return DocumentRef(BATCH_TYPE, batch.id, batch.number)


def _lines(uow: UnitOfWork, batch: PaymentBatch, raw_lines: Any) -> list[PaymentBatchLine]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationFailed("a payment batch needs at least one line", field="lines")
    ap = services.get(ApOpenItems)
    seen: set[int] = set()
    wanted: dict[uuid.UUID, Decimal] = {}
    out: list[PaymentBatchLine] = []
    for n, raw in enumerate(raw_lines):
        if not isinstance(raw, Mapping):
            raise ValidationFailed(f"lines[{n}] must be an object", field="lines")
        unknown_keys(raw, LINE_FIELDS, f" in lines[{n}]")
        if raw.get("account_code") not in (None, "") or raw.get("item_id") not in (None, ""):
            raise ValidationFailed("accounts and items are derived, never typed", field="lines", code="field_forbidden")
        try:
            line_no = int(raw.get("line_no") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"lines[{n}].line_no must be an integer", field="lines") from exc
        if line_no < 1 or line_no in seen:
            raise ValidationFailed(f"lines[{n}].line_no must be a unique positive integer", field="lines")
        seen.add(line_no)
        party = raw.get("party")
        party_id = uuid_in(party.get("id") if isinstance(party, Mapping) else party, f"lines[{n}].party")
        assert party_id is not None  # noqa: S101 - required above
        if raw.get("applied_amount") not in (None, ""):
            amount = money_in(raw.get("applied_amount"), f"lines[{n}].applied_amount", batch.currency)
        else:
            price = money_in(raw.get("unit_price"), f"lines[{n}].unit_price", batch.currency)
            amount = q_amount(price * qty_in(raw.get("quantity"), f"lines[{n}].quantity"))
        if amount <= 0:
            raise ValidationFailed(f"lines[{n}] amount must be positive", field="lines")
        oid = uuid_in(raw.get("open_item_id"), f"lines[{n}].open_item_id", required=False)
        description = text_in(raw.get("description"), "description", 255)
        if oid is not None:
            item = ap.get(batch.company_id, oid)
            if item is None:
                raise NotFound("open item not found")
            if item.party_id != party_id:
                raise TreasuryOpenItemMismatchParty(
                    "the open item is a payable of another party", open_item_id=str(oid)
                )
            wanted[oid] = wanted.get(oid, Decimal(0)) + amount
            if wanted[oid] > item.open_amount:
                raise TreasuryOverapplication(
                    f"the batch pays {wanted[oid]} of {item.document.number} (open {item.open_amount})",
                    open_item_id=str(oid),
                )
            description = description or (item.document.number or "")
        dims = dims_in(raw.get("dimensions"), f"lines[{n}].dimensions")
        out.append(
            PaymentBatchLine(
                tenant_id=uow.ctx.tenant_id,
                company_id=batch.company_id,
                batch=batch,
                line_no=line_no,
                party_id=party_id,
                open_item_id=oid,
                description=description,
                amount=amount,
                tax_code=text_in(raw.get("tax_code"), "tax_code", 40),
                **{slot: dims.get(slot) for slot in DIM_SLOTS},
            )
        )
    return out


def _header(uow: UnitOfWork, batch: PaymentBatch, data: Mapping[str, Any], *, partial: bool) -> None:
    ctx = uow.ctx
    if not partial or "document_date" in data:
        batch.document_date = date_in(data.get("document_date"), "document_date", clock.business_date())
    if not partial or "payment_date" in data:
        batch.payment_date = date_in(data.get("payment_date"), "payment_date")
    if not partial or "currency" in data:
        batch.currency = currency_in(data.get("currency"))
    if not partial or "fund_id" in data:
        fid = uuid_req(data.get("fund_id"), "fund_id")
        fund = Fund.objects.filter(tenant_id=ctx.tenant_id, company_id=batch.company_id, id=fid).first()
        if fund is None or fund.status != "ACTIVE" or fund.fund_type != Fund.FundType.BANK:
            raise ValidationFailed("fund_id must be an active bank fund of the company", field="fund_id")
        batch.fund = fund
    if batch.currency != "COP" or batch.fund.currency != batch.currency:
        raise ValidationFailed("payment batches pay COP from a COP bank fund", field="currency")
    if "notes" in data:
        batch.notes = text_in(data.get("notes"), "notes")


def create_batch(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PaymentBatch:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.payment_batch.create", {"company_id": company_id})
    unknown_keys(data, HEADER_FIELDS)
    batch = B(tenant_id=ctx.tenant_id, company_id=company_id, created_by=ctx.principal_id, updated_by=ctx.principal_id)
    _header(uow, batch, data, partial=False)
    lines = _lines(uow, batch, data.get("lines"))
    code, prefix = SEQ_BATCH
    batch.number = next_number(uow, company_id, code, prefix, batch.document_date)
    batch.total = sum((ln.amount for ln in lines), Decimal(0))
    batch.save()
    PaymentBatchLine.objects.bulk_create(lines)
    audit(uow, "treasury.payment_batch.create", BATCH_TYPE, batch.id, company_id, {"number": batch.number})
    return batch


def update_batch(uow: UnitOfWork, batch: PaymentBatch, data: Mapping[str, Any]) -> PaymentBatch:
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.update", batch)
    unknown_keys(data, HEADER_FIELDS)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status not in (B.State.DRAFT, B.State.REJECTED):
        raise InvalidStateTransition(f"a {batch.status} batch cannot be edited", code="document_immutable")
    _header(uow, batch, data, partial=True)
    if "lines" in data:
        lines = _lines(uow, batch, data.get("lines"))
        batch.lines.all().delete()
        PaymentBatchLine.objects.bulk_create(lines)
        batch.total = sum((ln.amount for ln in lines), Decimal(0))
    batch.status = B.State.DRAFT
    batch.save_versioned(
        ctx.principal_id, ["document_date", "payment_date", "currency", "fund", "notes", "total", "status"]
    )
    audit(uow, "treasury.payment_batch.update", BATCH_TYPE, batch.id, batch.company_id, {"fields": sorted(data)})
    return batch


def cancel_batch(uow: UnitOfWork, batch: PaymentBatch) -> PaymentBatch:
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.delete", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status not in (B.State.DRAFT, B.State.REJECTED):
        raise InvalidStateTransition(f"a {batch.status} batch cannot be deleted", code="document_immutable")
    batch.status = B.State.CANCELLED
    batch.save_versioned(ctx.principal_id, ["status"])
    audit(uow, "treasury.payment_batch.delete", BATCH_TYPE, batch.id, batch.company_id, {"status": batch.status})
    return batch


# --------------------------------------------------------------------------- approval (SoD, dual)


def submit_batch(uow: UnitOfWork, batch: PaymentBatch, reason: str = "") -> PaymentBatch:
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.update", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status not in (B.State.DRAFT, B.State.REJECTED):
        raise InvalidStateTransition(f"a {batch.status} batch cannot be submitted")
    cfg = settings_for(ctx, batch.company_id)
    dual = batch.total >= cfg.dual_approval_threshold
    needs = dual or (cfg.ce_approval_threshold is not None and batch.total >= cfg.ce_approval_threshold)
    batch.dual_approval, batch.approved_by = dual, []
    if needs:
        code = POLICY_DUAL if dual else POLICY_CE
        try:
            req = services.get(WorkflowService).request_approval(uow, batch.company_id, ref(batch), code, reason)
        except NotFound as exc:
            raise TreasuryApprovalRequired(f"approval policy {code} is not configured", policy=code) from exc
        batch.approval_request_id = req.id
        batch.status = B.State.SUBMITTED
    else:
        batch.status = B.State.APPROVED
    batch.save_versioned(ctx.principal_id, ["status", "approval_request_id", "dual_approval", "approved_by"])
    audit(uow, "treasury.payment_batch.submit", BATCH_TYPE, batch.id, batch.company_id, {"status": batch.status})
    return batch


def approve_batch(uow: UnitOfWork, batch: PaymentBatch, approve: bool, reason: str) -> PaymentBatch:
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.approve", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status != B.State.SUBMITTED or batch.approval_request_id is None:
        raise InvalidStateTransition(f"a {batch.status} batch is not waiting for approval")
    me = ctx.principal_id
    if me == batch.created_by or (ctx.on_behalf_of and ctx.on_behalf_of == batch.created_by):
        raise SodViolation("the creator of a payment batch cannot approve it (SoD)")
    if str(me) in batch.approved_by:
        raise SodViolation("you already approved this batch; a second approver is required")
    if not approve and not reason.strip():
        raise ValidationFailed("a rejection needs a reason", field="reason", code="reason_required")
    state = services.get(WorkflowService).decide(uow, batch.approval_request_id, approve, reason).state
    if not approve or state == "REJECTED":
        batch.status = B.State.REJECTED
    else:
        batch.approved_by = [*batch.approved_by, str(me)]
        if state == "APPROVED":
            if batch.dual_approval and len(set(batch.approved_by)) < 2:
                raise TreasuryApprovalRequired(f"policy {POLICY_DUAL} must require two different approvers")
            batch.status = B.State.APPROVED
    batch.save_versioned(ctx.principal_id, ["status", "approved_by"])
    action = "approve" if approve else "reject"
    audit(
        uow,
        f"treasury.payment_batch.{action}",
        BATCH_TYPE,
        batch.id,
        batch.company_id,
        {"status": batch.status},
        reason,
    )
    return batch


# --------------------------------------------------------------------------- bank file and execution


def _file(batch: PaymentBatch, rows: list[tuple[str, str, str, Decimal, str]]) -> None:
    buf = io.StringIO()
    buf.write(f"# {BANK_FILE_LABEL} — archivo bancario SIMULADO (formato genérico CSV, no certificado; EG-5)\n")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["lote", "linea", "comprobante", "beneficiario", "valor", "referencia"])
    for i, (voucher, party, ref_text, amount, currency) in enumerate(rows, start=1):
        writer.writerow([batch.number, i, voucher, party, f"{amount:f}", f"{ref_text} {currency}".strip()])
    content = buf.getvalue()
    batch.file_label = BANK_FILE_LABEL
    batch.file_name = f"{batch.number}-{BANK_FILE_LABEL}.csv"
    batch.file_content = content
    batch.file_sha256 = hashlib.sha256(content.encode()).hexdigest()


def file_info(batch: PaymentBatch) -> dict[str, Any]:
    return {
        "label": batch.file_label,
        "name": batch.file_name,
        "sha256": batch.file_sha256,
        "content": batch.file_content,
        "synthetic": True,
    }


def generate_file(uow: UnitOfWork, batch: PaymentBatch) -> PaymentBatch:
    """Preview (APPROVED: payees and gross amounts) or re-issue (POSTED: one row per executed CE) of the file."""
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.update", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status not in (B.State.APPROVED, B.State.POSTED):
        raise InvalidStateTransition(f"a {batch.status} batch has no bank file yet")
    _file(batch, _rows(batch))
    batch.save_versioned(ctx.principal_id, ["file_label", "file_name", "file_content", "file_sha256"])
    audit(
        uow,
        "treasury.payment_batch.generate_file",
        BATCH_TYPE,
        batch.id,
        batch.company_id,
        {"sha256": batch.file_sha256},
    )
    return batch


def _rows(batch: PaymentBatch) -> list[tuple[str, str, str, Decimal, str]]:
    if batch.status == B.State.POSTED:
        vouchers_ = TreasuryVoucher.objects.filter(
            tenant_id=batch.tenant_id, source_type=BATCH_TYPE, source_id=batch.id
        ).order_by("number")
        return [(v.number, str(v.party_id), v.reference, v.total, v.currency) for v in vouchers_]
    by_party: dict[uuid.UUID, Decimal] = {}
    for ln in batch.lines.order_by("line_no"):
        by_party[ln.party_id] = by_party.get(ln.party_id, Decimal(0)) + ln.amount
    return [("", str(p), batch.number, amount, batch.currency) for p, amount in by_party.items()]


def execute_batch(uow: UnitOfWork, batch: PaymentBatch) -> tuple[PaymentBatch, dict[str, Any]]:
    """APPROVED → POSTED: one CE per payee (gap-free CE numbers, in payee order of first line), all in this UoW."""
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.post", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id).select_related("fund"), batch.id)
    if batch.status != B.State.APPROVED:
        if batch.status == B.State.SUBMITTED:
            raise TreasuryApprovalRequired("the payment batch is waiting for its approval")
        raise InvalidStateTransition(f"a {batch.status} batch cannot be executed")
    groups: dict[uuid.UUID, list[PaymentBatchLine]] = {}
    for ln in batch.lines.order_by("line_no"):
        groups.setdefault(ln.party_id, []).append(ln)
    created: list[TreasuryVoucher] = []
    for party_id, lines in groups.items():
        body_lines: list[dict[str, Any]] = []
        n = 0
        for ln in lines:
            n += 1
            dims = {slot: str(getattr(ln, slot)) for slot in DIM_SLOTS if getattr(ln, slot)}
            entry: dict[str, Any] = {
                "line_no": n,
                "description": ln.description,
                "applied_amount": {"amount": f"{ln.amount:f}", "currency": batch.currency},
                "dimensions": dims,
            }
            if ln.open_item_id is not None:
                entry["open_item_id"] = str(ln.open_item_id)
            body_lines.append(entry)
            if ln.tax_code:
                n += 1
                body_lines.append(
                    {
                        "line_no": n,
                        "tax_code": ln.tax_code,
                        "unit_price": {"amount": f"{ln.amount:f}", "currency": batch.currency},
                        "description": f"Retención {ln.tax_code}",
                    }
                )
        voucher = vouchers.create_voucher(
            uow,
            batch.company_id,
            TreasuryVoucher.Type.CE,
            {
                "document_date": batch.payment_date.isoformat(),
                "currency": batch.currency,
                "fund_id": str(batch.fund_id),
                "payment_method": TreasuryVoucher.Method.TRANSFER,
                "reference": batch.number,
                "party": {"id": str(party_id), "kind": "vendor"},
                "notes": f"Lote de pago {batch.number}",
                "lines": body_lines,
            },
            source=ref(batch),
        )
        # The batch approval (SoD / dual) is the approval of each of its CEs.
        voucher.status = TreasuryVoucher.State.APPROVED
        voucher.approval_request_id = batch.approval_request_id
        voucher.dual_approval = batch.dual_approval
        voucher.approved_by = list(batch.approved_by)
        voucher.approved_at = clock.now()
        voucher.save_versioned(
            ctx.principal_id, ["status", "approval_request_id", "dual_approval", "approved_by", "approved_at"]
        )
        voucher = vouchers.post(uow, voucher)
        PaymentBatchLine.objects.filter(id__in=[ln.id for ln in lines]).update(voucher=voucher)
        created.append(voucher)
    batch.status = B.State.POSTED
    batch.executed_at = clock.now()
    _file(batch, [(v.number, str(v.party_id), v.reference, v.total, v.currency) for v in created])
    batch.save_versioned(
        ctx.principal_id, ["status", "executed_at", "file_label", "file_name", "file_content", "file_sha256"]
    )
    audit(
        uow,
        "treasury.payment_batch.execute",
        BATCH_TYPE,
        batch.id,
        batch.company_id,
        {"vouchers": [v.number for v in created], "file_sha256": batch.file_sha256},
    )
    now = clock.now().isoformat()
    operation = {
        "id": str(ids.new_id()),
        "kind": "treasury_payment_batches_by_execute",
        "status": "SUCCEEDED",
        "progress": "100",
        "started_at": now,
        "finished_at": now,
        "result_links": [
            *({"rel": "disbursement", "href": f"/api/v1/treasury/disbursements/{v.id}"} for v in created),
            {"rel": "payment-batch", "href": f"/api/v1/treasury/payment-batches/{batch.id}"},
        ],
        "error": None,
    }
    return batch, operation


def mark_sent(uow: UnitOfWork, batch: PaymentBatch) -> PaymentBatch:
    ctx = uow.ctx
    require(ctx, "treasury.payment_batch.update", batch)
    batch = lock(B.objects.filter(tenant_id=ctx.tenant_id), batch.id)
    if batch.status != B.State.POSTED or not batch.file_sha256:
        raise InvalidStateTransition("only an executed batch with a bank file can be marked as sent")
    if batch.sent_at is None:
        batch.sent_at = clock.now()
        batch.save_versioned(ctx.principal_id, ["sent_at"])
        audit(uow, "treasury.payment_batch.mark_sent", BATCH_TYPE, batch.id, batch.company_id, {})
    return batch
