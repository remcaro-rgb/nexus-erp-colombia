"""Pure consolidation engine (no database): translation, aggregation, eliminations, NCI (P16 [A], S10).

Sign convention: every balance is **debit-positive** (``debit − credit``) in the member's functional currency
(COP in this edition, D-001). The service layer reads the trial balances, maps local accounts to the group chart
and resolves the IC facts; this module only computes, deterministically and in ``Decimal``:

1. **Translation** (IAS 21 style): assets and liabilities at the closing rate, income and expenses at the average
   rate, equity at the member's historical rate; the difference is the CTA, booked to OCI (``cta`` account).
2. **Aggregation** by method: FULL (100 %), PROPORTIONAL (ownership %), EQUITY (not aggregated: the parent picks
   up its share of the associate's result against the investment).
3. **Eliminations** in the consolidation book: IC balances (receivable vs payable; a mismatch goes to the
   ``ic_difference`` account and is reported), IC revenue vs COGS, unrealized profit in the buyer's ending
   inventory (S10), investment vs equity (difference = goodwill or bargain-purchase gain).
4. **Non-controlling interest** of FULL subsidiaries owned < 100 %: share of equity, of the current result and of
   the CTA.
5. Manual adjustments (balanced).

Every generated entry is balanced by construction; amounts are quantized to 4 decimals (ROUND_HALF_UP).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
Q4 = Decimal("0.0001")

BALANCE_TYPES = ("asset", "liability", "equity")
RESULT_TYPES = ("income", "expense")

#: Generated group accounts (synthetic group chart, overridable in ``ConsolidationGroup.settings``).
DEFAULT_ACCOUNTS: dict[str, tuple[str, str, str]] = {
    "cta": ("381595", "Diferencia en conversión — ORI (SINTÉTICO)", "equity"),
    "nci": ("390505", "Participación no controladora (SINTÉTICO)", "equity"),
    "nci_profit": ("599005", "Resultado atribuible a la participación no controladora (SINTÉTICO)", "expense"),
    "goodwill": ("160505", "Plusvalía (SINTÉTICO)", "asset"),
    "bargain_gain": ("429905", "Ganancia en compra en condiciones ventajosas (SINTÉTICO)", "income"),
    "ic_difference": ("199995", "Diferencias intercompañía por conciliar (SINTÉTICO)", "asset"),
    "equity_method_income": ("424005", "Participación en resultados de asociadas (SINTÉTICO)", "income"),
    "equity_method_investment": ("120505", "Inversiones en asociadas — método de participación (SINTÉTICO)", "asset"),
}


def q4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TbRow:
    """One trial-balance row of a member, already mapped to the group chart."""

    local_account: str
    group_account: str
    group_name: str
    account_type: str  # asset | liability | equity | income | expense | memo
    balance: Decimal  # debit-positive, functional currency


@dataclass(frozen=True, slots=True)
class Rates:
    """Presentation-currency units per functional-currency unit."""

    closing: Decimal = ONE
    average: Decimal = ONE
    historical: Decimal = ONE

    def for_type(self, account_type: str) -> Decimal:
        if account_type in RESULT_TYPES:
            return self.average
        if account_type == "equity":
            return self.historical
        return self.closing


@dataclass(frozen=True, slots=True)
class Member:
    company_id: uuid.UUID
    method: str  # FULL | PROPORTIONAL | EQUITY
    ownership_pct: Decimal
    is_parent: bool
    rows: tuple[TbRow, ...]
    rates: Rates = field(default_factory=Rates)
    #: parent's local account code holding the investment in this member (eliminated against its equity)
    investment_account: str = ""

    @property
    def share(self) -> Decimal:
        return self.ownership_pct / HUNDRED

    @property
    def weight(self) -> Decimal:
        """Fraction of the member's lines aggregated into the group."""
        if self.method == "PROPORTIONAL" and not self.is_parent:
            return self.share
        if self.method == "EQUITY" and not self.is_parent:
            return ZERO
        return ONE


@dataclass(frozen=True, slots=True)
class IcBalanceRow:
    """Net balance (debit-positive, functional currency) a holder keeps on an IC account against a counterpart."""

    holder_id: uuid.UUID
    counterpart_id: uuid.UUID
    group_account: str
    balance: Decimal


@dataclass(frozen=True, slots=True)
class IcSale:
    """IC sales of ``seller`` to ``buyer`` in the fiscal year to date (transfer price, functional currency)."""

    seller_id: uuid.UUID
    buyer_id: uuid.UUID
    revenue_account: str  # group account of the seller's IC revenue
    cogs_account: str  # group account of COGS
    inventory_account: str  # group account of the buyer's inventory
    revenue: Decimal
    unrealized_profit: Decimal  # margin still in the buyer's ending inventory


@dataclass(frozen=True, slots=True)
class Adjustment:
    group_account: str
    amount: Decimal  # positive = debit, negative = credit (presentation currency)
    memo: str = ""


@dataclass(slots=True)
class LineDraft:
    company_id: uuid.UUID | None
    group_account: str
    debit: Decimal
    credit: Decimal
    memo: str = ""


@dataclass(slots=True)
class EntryDraft:
    kind: str
    memo: str
    lines: list[LineDraft] = field(default_factory=list)

    def add(self, company_id: uuid.UUID | None, account: str, amount: Decimal, memo: str = "") -> None:
        """Debit-positive ``amount`` (negative = credit); zero amounts are skipped."""
        amount = q4(amount)
        if amount == ZERO:
            return
        if amount > 0:
            self.lines.append(LineDraft(company_id, account, amount, ZERO, memo))
        else:
            self.lines.append(LineDraft(company_id, account, ZERO, -amount, memo))

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)

    def balance(self, company_id: uuid.UUID | None, account: str, memo: str = "") -> None:
        """Close the entry with one residual line on ``account``."""
        self.add(company_id, account, self.total_credit - self.total_debit, memo)


@dataclass(slots=True)
class Result:
    #: (company_id, group_account) → translated, weighted contribution (presentation currency, debit-positive)
    contributions: dict[tuple[uuid.UUID, str], Decimal]
    accounts: dict[str, tuple[str, str]]  # group_account → (name, type)
    entries: list[EntryDraft]
    ic_differences: list[dict[str, object]]
    translated_totals: dict[uuid.UUID, Decimal]
    ctas: dict[uuid.UUID, Decimal]

    def consolidated(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for (_c, acc), amt in self.contributions.items():
            out[acc] += amt
        for e in self.entries:
            for ln in e.lines:
                out[ln.group_account] += ln.debit - ln.credit
        return dict(out)

    def aggregated(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for (_c, acc), amt in self.contributions.items():
            out[acc] += amt
        return dict(out)

    def eliminations(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for e in self.entries:
            for ln in e.lines:
                out[ln.group_account] += ln.debit - ln.credit
        return dict(out)


def _accounts(settings: dict[str, object] | None) -> dict[str, tuple[str, str, str]]:
    accounts = dict(DEFAULT_ACCOUNTS)
    for key, value in (settings or {}).items():
        if key in accounts and isinstance(value, str) and value:
            _code, name, typ = accounts[key]
            accounts[key] = (value, name, typ)
    return accounts


def translate(member: Member) -> tuple[dict[str, Decimal], Decimal]:
    """Translated balances per group account (unweighted) and the member's CTA (debit-positive)."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in member.rows:
        out[row.group_account] += q4(row.balance * member.rates.for_type(row.account_type))
    cta = -sum(out.values(), ZERO)
    return dict(out), cta


def consolidate(
    members: Iterable[Member],
    *,
    ic_balances: Iterable[IcBalanceRow] = (),
    ic_sales: Iterable[IcSale] = (),
    adjustments: Iterable[Adjustment] = (),
    settings: dict[str, object] | None = None,
) -> Result:
    members = list(members)
    acc = _accounts(settings)
    by_id = {m.company_id: m for m in members}
    parent = next((m for m in members if m.is_parent), None)
    accounts: dict[str, tuple[str, str]] = {}
    contributions: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(lambda: ZERO)
    entries: list[EntryDraft] = []
    translated_totals: dict[uuid.UUID, Decimal] = {}

    for m in members:
        for row in m.rows:
            accounts.setdefault(row.group_account, (row.group_name, row.account_type))
    for code, name, typ in acc.values():
        accounts.setdefault(code, (name, typ))

    translated: dict[uuid.UUID, dict[str, Decimal]] = {}
    ctas: dict[uuid.UUID, Decimal] = {}
    for m in members:
        tb, cta = translate(m)
        translated[m.company_id], ctas[m.company_id] = tb, cta
        translated_totals[m.company_id] = sum(tb.values(), ZERO)
        w = m.weight
        if w == ZERO:
            continue
        weighted_total = ZERO
        for account, amount in tb.items():
            part = q4(amount * w)
            contributions[(m.company_id, account)] += part
            weighted_total += part
        # The CTA is part of the member's translated trial balance (OCI): it closes the weighted lines.
        if weighted_total != ZERO:
            contributions[(m.company_id, acc["cta"][0])] -= weighted_total

    entries.extend(_ic_balance_entries(by_id, ic_balances, acc, translated))
    entries.extend(_ic_sale_entries(by_id, ic_sales))
    if parent is not None:
        entries.extend(_investment_and_nci(parent, members, translated, ctas, acc))
        entries.extend(_equity_method(parent, members, translated, acc))
    adj = list(adjustments)
    if adj:
        e = EntryDraft("ADJUSTMENT", "Ajustes manuales de consolidación")
        for a in adj:
            e.add(None, a.group_account, a.amount, a.memo)
            accounts.setdefault(a.group_account, (a.group_account, _guess_type(a.group_account)))
        entries.append(e)
    entries = [e for e in entries if e.lines]
    ic_diff = [
        {"company_a_id": a, "company_b_id": b, "difference": d}
        for (a, b), d in sorted(_ic_pair_differences(ic_balances, by_id).items(), key=lambda kv: str(kv[0]))
        if d != ZERO
    ]
    contrib = {k: v for k, v in contributions.items() if v != ZERO}
    return Result(contrib, accounts, entries, ic_diff, translated_totals, ctas)


def _guess_type(group_account: str) -> str:
    head = group_account[:1]
    return {"1": "asset", "2": "liability", "3": "equity", "4": "income"}.get(head, "expense")


def _pair_factor(by_id: dict[uuid.UUID, Member], a: uuid.UUID, b: uuid.UUID) -> Decimal | None:
    ma, mb = by_id.get(a), by_id.get(b)
    if ma is None or mb is None or ma.weight == ZERO or mb.weight == ZERO:
        return None
    return min(ma.weight, mb.weight)


def _ic_pair_differences(
    rows: Iterable[IcBalanceRow], by_id: dict[uuid.UUID, Member]
) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    out: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        if _pair_factor(by_id, r.holder_id, r.counterpart_id) is None:
            continue
        key = tuple(sorted((r.holder_id, r.counterpart_id), key=str))
        out[(key[0], key[1])] += r.balance * by_id[r.holder_id].rates.closing
    return {k: q4(v) for k, v in out.items()}


def _ic_balance_entries(
    by_id: dict[uuid.UUID, Member],
    rows: Iterable[IcBalanceRow],
    acc: dict[str, tuple[str, str, str]],
    translated: dict[uuid.UUID, dict[str, Decimal]],
) -> list[EntryDraft]:
    pairs: dict[tuple[str, str], list[IcBalanceRow]] = defaultdict(list)
    for r in rows:
        if _pair_factor(by_id, r.holder_id, r.counterpart_id) is None or r.balance == ZERO:
            continue
        a, b = sorted((str(r.holder_id), str(r.counterpart_id)))
        pairs[(a, b)].append(r)
    out = []
    for (a, b), items in sorted(pairs.items()):
        factor = _pair_factor(by_id, uuid.UUID(a), uuid.UUID(b)) or ONE
        e = EntryDraft("IC_BALANCE", f"Eliminación de saldos intercompañía {a} ↔ {b}")
        for r in items:
            e.add(r.holder_id, r.group_account, -(r.balance * by_id[r.holder_id].rates.closing * factor), "IC")
        e.balance(None, acc["ic_difference"][0], "Diferencia intercompañía por conciliar")
        out.append(e)
    return out


def _ic_sale_entries(by_id: dict[uuid.UUID, Member], sales: Iterable[IcSale]) -> list[EntryDraft]:
    out = []
    for s in sales:
        factor = _pair_factor(by_id, s.seller_id, s.buyer_id)
        if factor is None:
            continue
        seller = by_id[s.seller_id]
        buyer = by_id[s.buyer_id]
        revenue = q4(s.revenue * seller.rates.average * factor)
        if revenue != ZERO:
            e = EntryDraft("IC_REVENUE_COGS", "Eliminación de ingresos y costos intercompañía")
            e.add(s.seller_id, s.revenue_account, revenue, "Ingreso IC")
            e.add(s.buyer_id, s.cogs_account, -revenue, "Costo IC")
            out.append(e)
        profit = q4(s.unrealized_profit * buyer.rates.closing * factor)
        if profit != ZERO:
            e = EntryDraft("UNREALIZED_PROFIT", "Utilidad no realizada en inventario del comprador")
            e.add(s.seller_id, s.cogs_account, profit, "Utilidad no realizada")
            e.add(s.buyer_id, s.inventory_account, -profit, "Inventario")
            out.append(e)
    return out


def _result(tb: dict[str, Decimal], m: Member) -> Decimal:
    """Credit-positive current-year result of a translated trial balance."""
    types = {r.group_account: r.account_type for r in m.rows}
    return -sum((v for k, v in tb.items() if types.get(k) in RESULT_TYPES), ZERO)


def _investment_and_nci(
    parent: Member,
    members: list[Member],
    translated: dict[uuid.UUID, dict[str, Decimal]],
    ctas: dict[uuid.UUID, Decimal],
    acc: dict[str, tuple[str, str, str]],
) -> list[EntryDraft]:
    out = []
    parent_tb = {r.local_account: r for r in parent.rows}
    for m in members:
        if m.is_parent or m.method != "FULL":
            continue
        tb = translated[m.company_id]
        types = {r.group_account: r.account_type for r in m.rows}
        equity = {k: v for k, v in tb.items() if types.get(k) == "equity"}
        equity_cr = -sum(equity.values(), ZERO)  # credit-positive book equity (excl. current result and CTA)
        nci_share = ONE - m.share
        inv_row = parent_tb.get(m.investment_account) if m.investment_account else None
        if inv_row is not None:
            e = EntryDraft("INVESTMENT_EQUITY", "Eliminación inversión vs patrimonio")
            # Goodwill is measured in the functional currency (investment − share × book equity) and, like the
            # investment (an asset of the parent), carried at the closing rate; equity is reversed at its
            # historical rate; the translation residual of the elimination goes to the CTA (IAS 21).
            equity_fc = -sum((r.balance for r in m.rows if r.account_type == "equity"), ZERO)
            goodwill_fc = inv_row.balance - m.share * equity_fc
            for k, v in sorted(equity.items()):
                e.add(m.company_id, k, -v, "Patrimonio de la subsidiaria")  # reverse 100 % of equity
            e.add(parent.company_id, inv_row.group_account, -(inv_row.balance * parent.rates.closing), "Inversión")
            e.add(m.company_id, acc["nci"][0], -(equity_cr * nci_share), "Participación no controladora")
            if goodwill_fc > 0:
                e.add(None, acc["goodwill"][0], goodwill_fc * parent.rates.closing, "Plusvalía")
            else:
                e.add(None, acc["bargain_gain"][0], goodwill_fc * parent.rates.average, "Compra en condiciones ventajosas")
            e.balance(None, acc["cta"][0], "Diferencia en conversión de la eliminación")
            out.append(e)
        elif nci_share > ZERO and equity:
            e = EntryDraft("NCI", "Participación no controladora en el patrimonio")
            for k, v in sorted(equity.items()):
                e.add(m.company_id, k, -(v * nci_share), "Patrimonio atribuible a no controladora")
            e.balance(m.company_id, acc["nci"][0], "Participación no controladora")
            out.append(e)
        if nci_share > ZERO:
            result = _result(tb, m)
            e = EntryDraft("NCI", "Participación no controladora en el resultado y la ORI")
            e.add(m.company_id, acc["nci_profit"][0], result * nci_share, "Resultado atribuible a no controladora")
            e.add(m.company_id, acc["cta"][0], -(ctas[m.company_id] * nci_share), "ORI atribuible a no controladora")
            e.balance(m.company_id, acc["nci"][0], "Participación no controladora")
            out.append(e)
    return out


def _equity_method(
    parent: Member,
    members: list[Member],
    translated: dict[uuid.UUID, dict[str, Decimal]],
    acc: dict[str, tuple[str, str, str]],
) -> list[EntryDraft]:
    out = []
    for m in members:
        if m.is_parent or m.method != "EQUITY":
            continue
        share = q4(_result(translated[m.company_id], m) * m.share)
        if share == ZERO:
            continue
        e = EntryDraft("EQUITY_METHOD", "Método de participación: resultado de la asociada")
        e.add(parent.company_id, acc["equity_method_investment"][0], share, "Inversión en asociada")
        e.add(parent.company_id, acc["equity_method_income"][0], -share, "Participación en resultados")
        out.append(e)
    return out
