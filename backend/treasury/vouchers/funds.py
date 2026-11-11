"""Fund helpers: the GL account of a fund and its ``FundSnapshot`` (book balance, unreconciled vouchers).

A fund's money account is the ledger ``Account`` of its active bank account or cash box (``account_id``); postings
address it as ``account:<code>`` (P05 ``PostingService`` explicit-account form). The book balance is read from the
primary book's journal lines (read-only, through ``django.apps``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.apps import apps
from django.db.models import Sum

from kernel import clock
from kernel.errors import ValidationFailed
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from treasury.models import BankAccount, CashBox, Fund, StatementLine, TreasuryVoucher
from treasury.services.common import ledger_account


def account_id(ctx: TenantContext, fund: Fund) -> Any:
    master: BankAccount | CashBox | None
    if fund.fund_type == Fund.FundType.BANK:
        master = (
            BankAccount.objects.filter(tenant_id=ctx.tenant_id, fund=fund, status="ACTIVE").order_by("code").first()
        )
    else:
        master = CashBox.objects.filter(tenant_id=ctx.tenant_id, fund=fund, status="ACTIVE").order_by("code").first()
    if master is None:
        raise ValidationFailed(
            f"fund {fund.code} has no active bank account / cash box with a GL account", field="fund_id"
        )
    return master.account_id


def account_role(ctx: TenantContext, fund: Fund) -> str:
    account = ledger_account(ctx, fund.company_id, account_id(ctx, fund))
    return f"account:{account.code}"


def book_balance(ctx: TenantContext, fund: Fund) -> Decimal:
    model = apps.get_model("ledger", "JournalLine")
    row = model.objects.filter(
        tenant_id=ctx.tenant_id,
        company_id=fund.company_id,
        account_id=account_id(ctx, fund),
        entry__book__primary=True,
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    return q_amount((row["d"] or Decimal(0)) - (row["c"] or Decimal(0)))


def unreconciled(ctx: TenantContext, fund: Fund) -> Decimal:
    matched = StatementLine.objects.filter(tenant_id=ctx.tenant_id, voucher__isnull=False).values("voucher_id")
    rows = (
        TreasuryVoucher.objects.filter(tenant_id=ctx.tenant_id, company_id=fund.company_id, fund=fund, posted=True)
        .exclude(id__in=matched)
        .exclude(status=TreasuryVoucher.State.REVERSED)
        .filter(reversal_of__isnull=True)
    )
    total = Decimal(0)
    for vtype, amount in rows.values_list("voucher_type", "total"):
        total += amount if vtype == TreasuryVoucher.Type.RC else -amount
    return q_amount(total)


def snapshot(ctx: TenantContext, fund: Fund) -> dict[str, Any]:
    """``FundSnapshot`` (fund, as_of, book_balance, unreconciled) — shown at entry and stored at POST."""
    return {
        "fund_id": str(fund.id),
        "fund_code": fund.code,
        "as_of": clock.now().isoformat(),
        "currency": "COP",
        "book_balance": f"{book_balance(ctx, fund):f}",
        "unreconciled": f"{unreconciled(ctx, fund):f}",
        "allow_overdraft": fund.allow_overdraft,
    }
