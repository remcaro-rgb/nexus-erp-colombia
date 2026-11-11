"""Read-only access to the members' ledgers (P05 tables) for intercompany and consolidation.

Everything here reads posted journal lines of the **primary book** under the caller's bound tenancy (forced RLS:
only the companies of the context are visible). Nothing is written: consolidation never posts to a subsidiary's
books (eliminations live in the consolidation book, ``consolidation_entry``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum

from consolidation.errors import FxRateMissing
from kernel.tenancy import TenantContext
from ledger.models import Account, AccountDetermination, Book, ExchangeRate, JournalLine

ZERO = Decimal(0)
#: ``party_kind`` of journal lines whose party is another company of the tenant (IC partner, shared/07 §6).
COMPANY_PARTY_KIND = "company"


@dataclass(frozen=True, slots=True)
class AccountBalance:
    code: str
    name: str
    type: str
    balance: Decimal  # debit-positive


def period_bounds(period: str) -> tuple[dt.date, dt.date, dt.date]:
    """``(fiscal_year_start, period_start, period_end)`` of ``YYYY-MM`` (calendar fiscal year, Colombia)."""
    year, month = (int(x) for x in period.split("-"))
    start = dt.date(year, month, 1)
    end = (dt.date(year + (month == 12), month % 12 + 1, 1)) - dt.timedelta(days=1)
    return dt.date(year, 1, 1), start, end


def primary_book_id(ctx: TenantContext, company_id: uuid.UUID) -> uuid.UUID | None:
    book = (
        Book.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        .order_by("-primary", "code")
        .values_list("id", flat=True)
        .first()
    )
    return book


def role_accounts(ctx: TenantContext, company_id: uuid.UUID, role: str) -> list[str]:
    """Every account code an active determination maps ``role`` to (most specific/priority first)."""
    rows = (
        AccountDetermination.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, role_code=role, active=True)
        .select_related("account")
        .order_by("-priority", "account__code")
    )
    seen: list[str] = []
    for det in rows:
        if det.account.code not in seen:
            seen.append(det.account.code)
    return seen


def role_account(ctx: TenantContext, company_id: uuid.UUID, role: str) -> str:
    codes = role_accounts(ctx, company_id, role)
    return codes[0] if codes else ""


def _lines(ctx: TenantContext, company_id: uuid.UUID) -> Q:
    book = primary_book_id(ctx, company_id)
    return Q(tenant_id=ctx.tenant_id, company_id=company_id, book_id=book)


def trial_balance(ctx: TenantContext, company_id: uuid.UUID, period: str) -> list[AccountBalance]:
    """Balance-sheet accounts cumulative to the period end; result accounts for the fiscal year to date."""
    fy_start, _start, end = period_bounds(period)
    base = JournalLine.objects.filter(_lines(ctx, company_id), posting_date__lte=end)
    result_types = ("income", "expense")
    cumulative = (
        base.exclude(account__type__in=result_types)
        .values("account__code", "account__name", "account__type")
        .annotate(d=Sum("debit"), c=Sum("credit"))
    )
    ytd = (
        base.filter(account__type__in=result_types, posting_date__gte=fy_start)
        .values("account__code", "account__name", "account__type")
        .annotate(d=Sum("debit"), c=Sum("credit"))
    )
    out = []
    for row in [*cumulative, *ytd]:
        bal = (row["d"] or ZERO) - (row["c"] or ZERO)
        if bal != ZERO:
            out.append(AccountBalance(row["account__code"], row["account__name"], row["account__type"], bal))
    return sorted(out, key=lambda r: r.code)


def account_names(ctx: TenantContext, company_id: uuid.UUID) -> dict[str, tuple[str, str]]:
    return {
        code: (name, typ)
        for code, name, typ in Account.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).values_list(
            "code", "name", "type"
        )
    }


def party_balances(
    ctx: TenantContext,
    company_id: uuid.UUID,
    counterpart_id: uuid.UUID,
    accounts: list[str],
    until: dt.date,
) -> dict[str, Decimal]:
    """Debit-positive balance per account of ``company_id``'s lines whose party is ``counterpart_id``."""
    if not accounts:
        return {}
    rows = (
        JournalLine.objects.filter(
            _lines(ctx, company_id),
            posting_date__lte=until,
            account__code__in=accounts,
            party_id=counterpart_id,
            party_kind=COMPANY_PARTY_KIND,
        )
        .values("account__code")
        .annotate(d=Sum("debit"), c=Sum("credit"))
    )
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in rows:
        out[row["account__code"]] += (row["d"] or ZERO) - (row["c"] or ZERO)
    return dict(out)


def cop_per_unit(ctx: TenantContext, company_id: uuid.UUID, currency: str, start: dt.date, end: dt.date) -> tuple[Decimal, Decimal]:
    """``(closing, average)`` COP per one unit of ``currency`` from ``ledger.ExchangeRate`` of ``company_id``.

    Closing = the last rate on or before ``end``; average = arithmetic mean of the daily rates within
    ``[start, end]`` (closing when the period has no daily rate). No rate at all → ``fx_rate_missing``.
    """
    if currency == "COP":
        return Decimal(1), Decimal(1)
    qs = ExchangeRate.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, currency=currency)
    closing = qs.filter(date__lte=end).order_by("-date").values_list("rate", flat=True).first()
    if closing is None:
        raise FxRateMissing(f"no {currency} exchange rate on or before {end.isoformat()}", currency=currency)
    in_period = list(qs.filter(date__gte=start, date__lte=end).values_list("rate", flat=True))
    average = (sum(in_period, ZERO) / Decimal(len(in_period))) if in_period else closing
    return Decimal(closing), Decimal(average)
