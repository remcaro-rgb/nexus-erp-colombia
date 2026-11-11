"""Bank statements and reconciliation by exact voucher id (F-TRE-RC "reconciliation with bank statements").

* ``POST /statements`` opens a statement of a bank account for a period; ``:import`` reads a generic CSV
  (synthetic mapping ``bank_account,date,description,reference,amount``; per-bank formats are pending EG-5).
* ``:match`` links a statement line to ONE posted voucher of the same fund with the same signed amount
  (RC inflow +, CE outflow −); ``parameters.voucher_id`` names it, else the auto-match looks for a posted,
  unmatched voucher whose number equals the line reference. ``:unmatch`` undoes it. A voucher is matched once.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock
from kernel.errors import NotFound, ValidationFailed
from kernel.uow import UnitOfWork
from treasury.errors import InvalidStateTransition, StatementLineAlreadyMatched
from treasury.models import BankAccount, BankStatement, StatementLine, TreasuryVoucher
from treasury.services.common import (
    audit,
    date_in,
    decimal_in,
    lock,
    require,
    text_in,
    unknown_keys,
    uuid_req,
)

CSV_COLUMNS = ("bank_account", "date", "description", "reference", "amount")
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _period(raw: Any) -> tuple[Any, Any]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed("period must be an object {from, to}", field="period")
    unknown_keys(raw, ("from", "to"), " in period")
    start = date_in(raw.get("from"), "period.from")
    end = None if raw.get("to") in (None, "") else date_in(raw.get("to"), "period.to")
    if end is not None and end < start:
        raise ValidationFailed("period.to must not be before period.from", field="period")
    return start, end


def create_statement(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> BankStatement:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.statement.create", {"company_id": company_id})
    unknown_keys(data, ("bank_account_id", "period"))
    bid = uuid_req(data.get("bank_account_id"), "bank_account_id")
    account = BankAccount.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=bid).first()
    if account is None:
        raise ValidationFailed("bank_account_id is not a bank account of the company", field="bank_account_id")
    start, end = _period(data.get("period"))
    row = BankStatement.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        bank_account=account,
        date_from=start,
        date_to=end,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "treasury.statement.create", "treasury.statement", row.id, company_id, {})
    return row


def add_line(
    uow: UnitOfWork, statement: BankStatement, day: Any, amount: Decimal, description: str = "", reference: str = ""
) -> StatementLine:
    last = statement.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    return StatementLine.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=statement.company_id,
        statement=statement,
        line_no=last + 1,
        date=day,
        description=description[:255],
        reference=reference[:120],
        amount=amount,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )


def import_csv(uow: UnitOfWork, company_id: uuid.UUID, content: bytes) -> list[BankStatement]:
    """Generic CSV import (synthetic mapping): one statement per bank account code, covering the line dates."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.statement.create", {"company_id": company_id})
    if len(content) > MAX_IMPORT_BYTES:
        raise ValidationFailed("the statement file is larger than 2 MB", field="file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed("the statement file must be UTF-8 CSV", field="file") from exc
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
        raise ValidationFailed(f"the CSV header must be {','.join(CSV_COLUMNS)}", field="file")
    rows: dict[str, list[dict[str, Any]]] = {}
    for n, raw in enumerate(reader, start=2):
        code = (raw.get("bank_account") or "").strip()
        if not code:
            raise ValidationFailed(f"row {n}: bank_account is required", field="file")
        rows.setdefault(code, []).append(
            {
                "date": date_in(raw.get("date"), f"row {n} date"),
                "amount": decimal_in(raw.get("amount"), f"row {n} amount"),
                "description": text_in(raw.get("description"), "description", 255),
                "reference": text_in(raw.get("reference"), "reference", 120),
            }
        )
    if not rows:
        raise ValidationFailed("the statement file has no rows", field="file")
    out: list[BankStatement] = []
    for code, lines in rows.items():
        account = BankAccount.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).first()
        if account is None:
            raise ValidationFailed(f"bank account {code} does not exist", field="file")
        statement = BankStatement.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            bank_account=account,
            date_from=min(x["date"] for x in lines),
            date_to=max(x["date"] for x in lines),
            source="CSV",
            created_by=ctx.principal_id,
            updated_by=ctx.principal_id,
        )
        for x in lines:
            add_line(uow, statement, x["date"], x["amount"], x["description"], x["reference"])
        out.append(statement)
        audit(uow, "treasury.statement.import", "treasury.statement", statement.id, company_id, {"lines": len(lines)})
    return out


def _signed(voucher: TreasuryVoucher) -> Decimal:
    total = Decimal(voucher.total)
    return total if voucher.voucher_type == TreasuryVoucher.Type.RC else -total


def match(uow: UnitOfWork, line: StatementLine, voucher_id: uuid.UUID | None) -> StatementLine:
    ctx = uow.ctx
    require(ctx, "treasury.statement_line.update", line)
    line = lock(
        StatementLine.objects.filter(tenant_id=ctx.tenant_id).select_related("statement__bank_account"), line.id
    )
    if line.state == StatementLine.State.MATCHED:
        raise StatementLineAlreadyMatched("the statement line is already matched")
    fund_id = line.statement.bank_account.fund_id
    if fund_id is None:
        raise ValidationFailed("the bank account of the statement has no fund", field="bank_account_id")
    candidates = TreasuryVoucher.objects.select_for_update().filter(
        tenant_id=ctx.tenant_id, company_id=line.company_id, fund_id=fund_id, status=TreasuryVoucher.State.POSTED
    )
    if voucher_id is not None:
        voucher = candidates.filter(id=voucher_id).first()
    else:
        voucher = candidates.filter(number=line.reference).first() if line.reference else None
    if voucher is None:
        raise NotFound("no posted voucher of this bank fund matches the line")
    if _signed(voucher) != line.amount:
        raise ValidationFailed(
            f"the line amount {line.amount} differs from voucher {voucher.number} ({_signed(voucher)})", field="amount"
        )
    if StatementLine.objects.filter(tenant_id=ctx.tenant_id, voucher=voucher).exists():
        raise StatementLineAlreadyMatched(f"voucher {voucher.number} is already reconciled")
    line.voucher, line.state, line.matched_by, line.matched_at = (
        voucher,
        StatementLine.State.MATCHED,
        ctx.principal_id,
        clock.now(),
    )
    line.save_versioned(ctx.principal_id, ["voucher", "state", "matched_by", "matched_at"])
    uow.emit(
        "treasury.statement_line.matched.v1",
        {
            "statement_line_id": str(line.id),
            "matched_to": [{"type": "treasury.receipt" if voucher.voucher_type == "RC" else "treasury.disbursement",
                            "id": str(voucher.id), "number": voucher.number}],
        },
        company_id=line.company_id,
    )  # fmt: skip
    audit(
        uow,
        "treasury.statement_line.match",
        "treasury.statement_line",
        line.id,
        line.company_id,
        {"voucher": voucher.number},
    )
    return line


def unmatch(uow: UnitOfWork, line: StatementLine, reason: str) -> StatementLine:
    ctx = uow.ctx
    require(ctx, "treasury.statement_line.update", line)
    line = lock(StatementLine.objects.filter(tenant_id=ctx.tenant_id), line.id)
    if line.state != StatementLine.State.MATCHED:
        raise InvalidStateTransition("the statement line is not matched")
    line.voucher, line.state, line.matched_by, line.matched_at = None, StatementLine.State.UNMATCHED, None, None
    line.save_versioned(ctx.principal_id, ["voucher", "state", "matched_by", "matched_at"])
    audit(uow, "treasury.statement_line.unmatch", "treasury.statement_line", line.id, line.company_id, {}, reason)
    return line
