"""Consolidation service (P16 [A], flag ``consolidation``): groups, members, chart mapping, runs, consolidation book.

A run reads the members' posted ledgers (primary book) for its period, computes translation, eliminations and
NCI with :mod:`consolidation.engine`, and — on ``:post`` — writes the balanced **consolidation-book** entries
(``consolidation_entry``; append-only). Subsidiaries' books are never touched. Corrections are by reversal.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import IntegrityError, transaction

from consolidation import access, engine, intercompany, ledgerdata
from consolidation.errors import (
    DocumentImmutable,
    FxRateMissing,
    InvalidStateTransition,
    PeriodClosed,
    ReasonRequired,
    SodViolation,
    invalid,
)
from consolidation.models import (
    ConsolidationEntry,
    ConsolidationEntryLine,
    ConsolidationGroup,
    ConsolidationRun,
    ConsolidationRunLine,
    EntryKind,
    GroupAccountMap,
    GroupMember,
    IntercompanyAgreement,
    IntercompanyLink,
    LinkKind,
    MasterStatus,
    Method,
    RunStatus,
)
from kernel import clock
from kernel.errors import NotFound
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
ONE = Decimal(1)
Q4 = Decimal("0.0001")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
RUN_POSTED_EVENT = "consolidation.run.posted.v1"
MISMATCH_EVENT = "intercompany.mismatch.detected.v1"
RUN_SOURCE = "consolidation.run"


# --------------------------------------------------------------------------- groups


def _uuid(value: Any, pointer: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise invalid(f"{pointer.strip('/')} must be a UUID", pointer, "format") from None


def _text(value: Any, pointer: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise invalid(f"{pointer.strip('/')} must be a non-empty string of at most {max_len}", pointer, "format")
    return value.strip()


def _currency(value: Any, pointer: str) -> str:
    if not isinstance(value, str) or not CURRENCY_RE.match(value):
        raise invalid("currency must be an ISO 4217 code", pointer, "format")
    return value


def _companies(value: Any) -> list[uuid.UUID]:
    if not isinstance(value, list) or not value:
        raise invalid("companies must be a non-empty list (the first one is the parent)", "/companies", "required")
    out: list[uuid.UUID] = []
    for i, raw in enumerate(value):
        cid = _uuid(raw, f"/companies/{i}")
        if cid in out:
            raise invalid("a company appears twice in the group", f"/companies/{i}", "duplicate")
        out.append(cid)
    return out


def members(group: ConsolidationGroup) -> list[GroupMember]:
    return list(group.members.order_by("position", "company_id"))


def company_ids(group: ConsolidationGroup) -> list[uuid.UUID]:
    return [m.company_id for m in members(group)]


def group_allowed(ctx: TenantContext, group: ConsolidationGroup, action: str) -> bool:
    return access.allowed(ctx, action, company_ids(group))


def visible_groups(ctx: TenantContext, action: str = "consolidation.group.read") -> list[ConsolidationGroup]:
    qs = ConsolidationGroup.objects.filter(tenant_id=ctx.tenant_id).prefetch_related("members")
    return [g for g in qs if group_allowed(ctx, g, action)]


def get_group(ctx: TenantContext, pk: uuid.UUID, action: str, lock: bool = False) -> ConsolidationGroup:
    qs = ConsolidationGroup.objects.filter(tenant_id=ctx.tenant_id, id=pk)
    if lock:
        qs = qs.select_for_update()
    group = qs.first()
    if group is None:
        raise NotFound("group not found")
    comps = company_ids(group)
    if not all(c in ctx.company_ids for c in comps):
        raise NotFound("group not found")  # a group with a hidden company is not disclosed
    access.require(ctx, action, comps)
    return group


def create_group(uow: UnitOfWork, data: dict[str, Any]) -> ConsolidationGroup:
    ctx = uow.ctx
    for key in ("code", "name", "companies", "presentation_currency"):
        if data.get(key) in (None, "", []):
            raise invalid(f"{key} is required", f"/{key}", "required")
    comps = _companies(data["companies"])
    access.require(ctx, "consolidation.group.create", comps)
    access.require_feature(ctx, comps)
    group = ConsolidationGroup(
        tenant_id=ctx.tenant_id,
        code=_text(data["code"], "/code", 40),
        name=_text(data["name"], "/name", 200),
        parent_company_id=comps[0],
        presentation_currency=_currency(data["presentation_currency"], "/presentation_currency"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    try:
        with transaction.atomic(using=uow.using):
            group.save()
    except IntegrityError:
        raise invalid("a group with this code already exists", "/code", "duplicate") from None
    _sync_members(group, comps)
    access.audit(
        uow,
        "create",
        "consolidation.group",
        group.id,
        comps[0],
        {"code": group.code, "companies": [str(c) for c in comps]},
    )
    return group


def _sync_members(group: ConsolidationGroup, comps: list[uuid.UUID]) -> None:
    existing = {m.company_id: m for m in group.members.all()}
    for cid, m in existing.items():
        if cid not in comps:
            m.delete()
    for pos, cid in enumerate(comps):
        current = existing.get(cid)
        if current is None:
            GroupMember.objects.create(tenant_id=group.tenant_id, group=group, company_id=cid, position=pos)
        elif current.position != pos:
            GroupMember.objects.filter(pk=current.pk).update(position=pos)


def update_group(uow: UnitOfWork, group: ConsolidationGroup, data: dict[str, Any]) -> ConsolidationGroup:
    ctx = uow.ctx
    if group.status == MasterStatus.ARCHIVED:
        raise InvalidStateTransition("an archived group cannot be changed")
    fields: list[str] = []
    if "code" in data:
        group.code = _text(data["code"], "/code", 40)
        fields.append("code")
    if "name" in data:
        group.name = _text(data["name"], "/name", 200)
        fields.append("name")
    if "presentation_currency" in data:
        cur = _currency(data["presentation_currency"], "/presentation_currency")
        if cur != group.presentation_currency and group.runs.filter(status=RunStatus.POSTED).exists():
            raise DocumentImmutable("the presentation currency of a group with posted runs cannot change")
        group.presentation_currency = cur
        fields.append("presentation_currency")
    if "companies" in data:
        comps = _companies(data["companies"])
        access.require(ctx, "consolidation.group.update", comps)
        if comps[0] != group.parent_company_id and group.runs.filter(status=RunStatus.POSTED).exists():
            raise DocumentImmutable("the parent of a group with posted runs cannot change")
        group.parent_company_id = comps[0]
        fields.append("parent_company_id")
        _sync_members(group, comps)
    if fields:
        try:
            with transaction.atomic(using=uow.using):
                group.save_versioned(ctx.principal_id, fields)
        except IntegrityError:
            raise invalid("a group with this code already exists", "/code", "duplicate") from None
        access.audit(uow, "update", "consolidation.group", group.id, group.parent_company_id, {"fields": fields})
    return group


def archive_group(uow: UnitOfWork, group: ConsolidationGroup) -> None:
    if group.status == MasterStatus.ARCHIVED:
        return
    if group.runs.filter(status=RunStatus.DRAFT).exists():
        raise InvalidStateTransition("cancel or post the draft runs of the group first")
    group.status = MasterStatus.ARCHIVED
    group.save_versioned(uow.ctx.principal_id, ["status"])
    access.audit(uow, "archive", "consolidation.group", group.id, group.parent_company_id)


def configure_member(
    uow: UnitOfWork,
    group: ConsolidationGroup,
    company_id: uuid.UUID,
    *,
    ownership_pct: Decimal | None = None,
    method: str | None = None,
    equity_historical_rate: Decimal | None = None,
    investment_account_code: str | None = None,
) -> GroupMember:
    """Ownership %, method, historical equity rate (COP per presentation unit) and investment account."""
    access.require(uow.ctx, "consolidation.group.update", company_ids(group))
    m = group.members.filter(company_id=company_id).first()
    if m is None:
        raise NotFound("member not found")
    if ownership_pct is not None:
        if not isinstance(ownership_pct, Decimal) or not ZERO <= ownership_pct <= engine.HUNDRED:
            raise invalid("ownership_pct must be a Decimal between 0 and 100", "/ownership_pct", "range")
        m.ownership_pct = ownership_pct
    if method is not None:
        if method not in Method.values:
            raise invalid("method must be FULL, PROPORTIONAL or EQUITY", "/method", "enum")
        if company_id == group.parent_company_id and method != Method.FULL:
            raise invalid("the parent is always consolidated in full", "/method", "parent")
        m.method = method
    if equity_historical_rate is not None:
        if not isinstance(equity_historical_rate, Decimal) or equity_historical_rate <= 0:
            raise invalid("equity_historical_rate must be a positive Decimal", "/equity_historical_rate", "range")
        m.equity_historical_rate = equity_historical_rate
    if investment_account_code is not None:
        m.investment_account_code = investment_account_code[:20]
    m.save()
    access.audit(
        uow,
        "update",
        "consolidation.group_member",
        m.id,
        company_id,
        {"method": m.method, "ownership_pct": str(m.ownership_pct)},
    )
    return m


def set_account_map(
    uow: UnitOfWork,
    group: ConsolidationGroup,
    prefix: str,
    group_account: str,
    name: str = "",
    company_id: uuid.UUID | None = None,
) -> GroupAccountMap:
    access.require(uow.ctx, "consolidation.group.update", company_ids(group))
    if not prefix or not group_account:
        raise invalid("prefix and group_account are required", "/local_prefix", "required")
    obj, _ = GroupAccountMap.objects.update_or_create(
        group=group,
        company_id=company_id,
        local_prefix=prefix,
        defaults={"tenant_id": group.tenant_id, "group_account": group_account, "group_name": name},
    )
    return obj


class AccountMapper:
    """Local account → group account: longest prefix, company-specific before group-wide; identity otherwise."""

    def __init__(self, group: ConsolidationGroup) -> None:
        self.maps = list(group.account_maps.all())

    def map(self, company_id: uuid.UUID, code: str, name: str) -> tuple[str, str]:
        best: GroupAccountMap | None = None
        for m in self.maps:
            if m.company_id not in (None, company_id) or not code.startswith(m.local_prefix):
                continue
            if best is None or (len(m.local_prefix), m.company_id is not None) > (
                len(best.local_prefix),
                best.company_id is not None,
            ):
                best = m
        if best is None:
            return code, name
        return best.group_account, best.group_name or name


# --------------------------------------------------------------------------- runs


def _period(value: Any) -> str:
    if not isinstance(value, str) or not PERIOD_RE.match(value):
        raise invalid("period must be YYYY-MM", "/period", "format")
    return value


def _date(value: Any, pointer: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise invalid(f"{pointer.strip('/')} must be an ISO date", pointer, "format") from None


def _decimal(value: Any, pointer: str) -> Decimal:
    if isinstance(value, float | bool):
        raise invalid("decimal values are strings", pointer, "format")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise invalid("must be a decimal string", pointer, "format") from None


def _lines(run: ConsolidationRun, raw: Any) -> None:
    if not isinstance(raw, list):
        raise invalid("lines must be a list", "/lines", "type")
    run.lines.all().delete()
    seen: set[int] = set()
    total = ZERO
    for i, ln in enumerate(raw):
        if not isinstance(ln, dict):
            raise invalid("a line must be an object", f"/lines/{i}", "type")
        allowed = {
            "line_no",
            "item_id",
            "description",
            "quantity",
            "unit_price",
            "amount",
            "tax_code",
            "account_code",
            "dimensions",
        }
        unknown = sorted(set(ln) - allowed)
        if unknown:
            raise invalid(f"unknown field(s): {', '.join(unknown)}", f"/lines/{i}/{unknown[0]}", "unknown")
        no = ln.get("line_no")
        if not isinstance(no, int) or isinstance(no, bool) or no < 1 or no in seen:
            raise invalid("line_no must be a unique positive integer", f"/lines/{i}/line_no", "format")
        seen.add(no)
        account = ln.get("account_code")
        if not isinstance(account, str) or not account.strip():
            raise invalid("account_code (group account) is required", f"/lines/{i}/account_code", "required")
        qty_raw = ln.get("quantity") or {"value": "1", "uom": "UND"}
        if not isinstance(qty_raw, dict):
            raise invalid("quantity must be a Quantity object", f"/lines/{i}/quantity", "type")
        qty = _decimal(qty_raw.get("value", "1"), f"/lines/{i}/quantity/value")
        price_raw = ln.get("unit_price")
        if not isinstance(price_raw, dict) or "amount" not in price_raw:
            raise invalid("unit_price must be a Money object", f"/lines/{i}/unit_price", "required")
        if price_raw.get("currency", run.currency) != run.currency:
            raise invalid("adjustments are in the presentation currency", f"/lines/{i}/unit_price/currency", "currency")
        price = _decimal(price_raw["amount"], f"/lines/{i}/unit_price/amount")
        amount = (qty * price).quantize(Q4)
        total += amount
        ConsolidationRunLine.objects.create(
            tenant_id=run.tenant_id,
            run=run,
            line_no=no,
            item_id=_uuid(ln["item_id"], f"/lines/{i}/item_id") if ln.get("item_id") else None,
            description=str(ln.get("description") or "")[:255],
            quantity=qty,
            uom=str(qty_raw.get("uom") or "UND")[:20],
            unit_price=price,
            amount=amount,
            tax_code=ln.get("tax_code"),
            account_code=account.strip()[:20],
            dimensions=ln.get("dimensions") or {},
        )
    if total != ZERO:
        raise invalid("manual adjustments must balance (Σ debit − Σ credit = 0)", "/lines", "unbalanced_entry")


def visible_runs(ctx: TenantContext, action: str = "consolidation.run.read") -> list[ConsolidationRun]:
    groups = {g.id for g in visible_groups(ctx, action)}
    return list(
        ConsolidationRun.objects.filter(tenant_id=ctx.tenant_id, group_id__in=groups).order_by("-period", "-number")
    )


def get_run(ctx: TenantContext, pk: uuid.UUID, action: str, lock: bool = False) -> ConsolidationRun:
    qs = ConsolidationRun.objects.filter(tenant_id=ctx.tenant_id, id=pk).select_related("group")
    if lock:
        qs = qs.select_for_update()
    run = qs.first()
    if run is None:
        raise NotFound("run not found")
    get_group(ctx, run.group_id, action)
    return run


def create_run(uow: UnitOfWork, data: dict[str, Any]) -> ConsolidationRun:
    ctx = uow.ctx
    for key in ("document_date", "group_id", "period"):
        if data.get(key) in (None, ""):
            raise invalid(f"{key} is required", f"/{key}", "required")
    group = get_group(ctx, _uuid(data["group_id"], "/group_id"), "consolidation.run.create", lock=True)
    access.require_feature(ctx, company_ids(group))
    if group.status != MasterStatus.ACTIVE:
        raise InvalidStateTransition("the group is archived")
    period = _period(data["period"])
    currency = _currency(data.get("currency") or group.presentation_currency, "/currency")
    if currency != group.presentation_currency:
        raise invalid("the run currency is the group's presentation currency", "/currency", "currency")
    seq = ConsolidationRun.objects.filter(tenant_id=ctx.tenant_id, group=group, period=period).count() + 1
    run = ConsolidationRun.objects.create(
        tenant_id=ctx.tenant_id,
        group=group,
        number=f"CONS-{group.code}-{period}-{seq:03d}"[:60],
        period=period,
        document_date=_date(data["document_date"], "/document_date"),
        posting_date=_date(data["posting_date"], "/posting_date") if data.get("posting_date") else None,
        currency=currency,
        notes=str(data.get("notes") or "")[:10000],
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        channel=ctx.channel.value,
        on_behalf_of=ctx.on_behalf_of,
    )
    if data.get("lines"):
        _lines(run, data["lines"])
    access.audit(
        uow, "create", "consolidation.run", run.id, group.parent_company_id, {"number": run.number, "period": period}
    )
    return run


def update_run(uow: UnitOfWork, run: ConsolidationRun, data: dict[str, Any]) -> ConsolidationRun:
    if run.status != RunStatus.DRAFT:
        raise DocumentImmutable("only draft runs can be changed (posted runs are reversed)")
    fields: list[str] = []
    if "group_id" in data and _uuid(data["group_id"], "/group_id") != run.group_id:
        raise invalid("the group of a run cannot change; create a new run", "/group_id", "immutable")
    if "period" in data:
        run.period = _period(data["period"])
        fields.append("period")
    if "document_date" in data:
        run.document_date = _date(data["document_date"], "/document_date")
        fields.append("document_date")
    if "posting_date" in data:
        run.posting_date = _date(data["posting_date"], "/posting_date") if data["posting_date"] else None
        fields.append("posting_date")
    if "currency" in data and _currency(data["currency"], "/currency") != run.group.presentation_currency:
        raise invalid("the run currency is the group's presentation currency", "/currency", "currency")
    if "notes" in data:
        run.notes = str(data["notes"] or "")[:10000]
        fields.append("notes")
    if "lines" in data:
        _lines(run, data["lines"] or [])
        fields.append("notes")
    if fields:
        run.save_versioned(uow.ctx.principal_id, fields)
        access.audit(uow, "update", "consolidation.run", run.id, run.group.parent_company_id, {"fields": fields})
    return run


def cancel_run(uow: UnitOfWork, run: ConsolidationRun) -> None:
    if run.status in (RunStatus.POSTED, RunStatus.REVERSED):
        raise DocumentImmutable("a posted run is never deleted; reverse it")
    if run.status == RunStatus.CANCELLED:
        return
    run.status = RunStatus.CANCELLED
    run.save_versioned(uow.ctx.principal_id, ["status"])
    access.audit(uow, "cancel", "consolidation.run", run.id, run.group.parent_company_id)


# --------------------------------------------------------------------------- computation


def _rates(
    ctx: TenantContext, group: ConsolidationGroup, member: GroupMember, period: str
) -> tuple[engine.Rates, dict[str, str]]:
    cur = group.presentation_currency
    if cur == "COP":
        return engine.Rates(), {"closing": "1", "average": "1", "historical": "1"}
    _fy, start, end = ledgerdata.period_bounds(period)
    closing, average = ledgerdata.cop_per_unit(ctx, group.parent_company_id, cur, start, end)
    if member.equity_historical_rate is None:
        raise FxRateMissing(f"historical equity rate missing for member {member.company_id}", currency=cur)
    hist = Decimal(member.equity_historical_rate)
    snap = {"closing": str(closing), "average": str(average), "historical": str(hist)}
    return engine.Rates(closing=ONE / closing, average=ONE / average, historical=ONE / hist), snap


def compute(ctx: TenantContext, run: ConsolidationRun) -> tuple[engine.Result, dict[str, Any]]:
    """Read the members' ledgers and run the engine (writes nothing)."""
    group = run.group
    mapper = AccountMapper(group)
    mems = members(group)
    _fy, _start, end = ledgerdata.period_bounds(run.period)
    fy_start = _fy
    rates_snapshot: dict[str, Any] = {"presentation_currency": group.presentation_currency, "members": {}}
    engine_members = []
    for m in mems:
        rates, snap = _rates(ctx, group, m, run.period)
        rates_snapshot["members"][str(m.company_id)] = snap
        rows = []
        for bal in ledgerdata.trial_balance(ctx, m.company_id, run.period):
            g_code, g_name = mapper.map(m.company_id, bal.code, bal.name)
            rows.append(engine.TbRow(bal.code, g_code, g_name, bal.type, bal.balance))
        engine_members.append(
            engine.Member(
                m.company_id,
                m.method,
                Decimal(m.ownership_pct),
                m.company_id == group.parent_company_id,
                tuple(rows),
                rates,
                m.investment_account_code,
            )
        )
    comps = set(company_ids(group))
    names = {c: ledgerdata.account_names(ctx, c) for c in comps}

    def gmap(company: uuid.UUID, code: str) -> str:
        return mapper.map(company, code, names[company].get(code, (code, ""))[0])[0]

    ic_balances: list[engine.IcBalanceRow] = []
    ic_sales: list[engine.IcSale] = []
    for ag in IntercompanyAgreement.objects.filter(
        tenant_id=ctx.tenant_id, company_a_id__in=comps, company_b_id__in=comps
    ):
        for holder, other in ((ag.company_a_id, ag.company_b_id), (ag.company_b_id, ag.company_a_id)):
            accounts = intercompany.ic_accounts(ctx, ag, holder)
            for code, amount in ledgerdata.party_balances(ctx, holder, other, accounts, end).items():
                ic_balances.append(engine.IcBalanceRow(holder, other, gmap(holder, code), amount))
        revenue = unrealized = ZERO
        for link in IntercompanyLink.objects.filter(agreement=ag, kind=LinkKind.SALE, document_date__lte=end):
            if link.document_date >= fy_start:
                revenue += link.amount
            if link.goods_amount > 0:
                remaining = link.goods_amount - link.consumed_amount
                unrealized += remaining * (link.goods_amount - link.cost_amount) / link.goods_amount
        if revenue or unrealized:
            a, b = ag.company_a_id, ag.company_b_id
            ic_sales.append(
                engine.IcSale(
                    a,
                    b,
                    gmap(a, ledgerdata.role_account(ctx, a, ag.role("revenue"))),
                    gmap(a, ledgerdata.role_account(ctx, a, ag.role("cogs"))),
                    gmap(b, ledgerdata.role_account(ctx, b, ag.role("inventory"))),
                    revenue,
                    engine.q4(unrealized),
                )
            )
    adjustments = [
        engine.Adjustment(ln.account_code or "", Decimal(ln.amount), ln.description)
        for ln in run.lines.order_by("line_no")
    ]
    result = engine.consolidate(
        engine_members,
        ic_balances=ic_balances,
        ic_sales=ic_sales,
        adjustments=adjustments,
        settings=group.settings,
    )
    return result, rates_snapshot


def _summary(result: engine.Result, rates: dict[str, Any]) -> dict[str, Any]:
    by_company: dict[str, dict[str, str]] = {}
    for (company, account), amount in sorted(result.contributions.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        by_company.setdefault(str(company), {})[account] = str(amount)
    return {
        "accounts": {k: [v[0], v[1]] for k, v in sorted(result.accounts.items())},
        "by_company": by_company,
        "ctas": {str(k): str(v) for k, v in result.ctas.items()},
        "ic_differences": [
            {
                "company_a_id": str(d["company_a_id"]),
                "company_b_id": str(d["company_b_id"]),
                "difference": str(d["difference"]),
            }
            for d in result.ic_differences
        ],
        "rates": rates,
    }


def simulation(run: ConsolidationRun, result: engine.Result, rates: dict[str, Any]) -> dict[str, Any]:
    day = (run.posting_date or run.document_date).isoformat()
    journal_entries = [
        {
            "journal": f"CONSOLIDACION/{e.kind}",
            "posting_date": day,
            "memo": e.memo,
            "lines": [
                {
                    "account": ln.group_account,
                    "company_id": str(ln.company_id) if ln.company_id else None,
                    "debit": {"amount": str(ln.debit), "currency": run.currency},
                    "credit": {"amount": str(ln.credit), "currency": run.currency},
                }
                for ln in e.lines
            ],
        }
        for e in result.entries
    ]
    explain = [
        f"Moneda de presentación {run.currency}; libros de las subsidiarias no se modifican (libro de consolidación).",
        f"{len(result.entries)} asientos de eliminación/ajuste; montos con datos SINTÉTICOS en dev.",
    ]
    warnings = [
        {"code": "intercompany_mismatch", "message": f"Diferencia intercompañía {d['difference']}"}
        for d in result.ic_differences
    ]
    return {
        "persisted": False,
        "documents": [
            {"type": RUN_SOURCE, "id": str(run.id), "number": run.number, "summary": _summary(result, rates)}
        ],
        "journal_entries": journal_entries,
        "stock_moves": [],
        "explain": explain,
        "warnings": warnings,
    }


def preview(uow: UnitOfWork, run: ConsolidationRun) -> dict[str, Any]:
    access.require_feature(uow.ctx, company_ids(run.group))
    if run.status == RunStatus.CANCELLED:
        raise InvalidStateTransition("a cancelled run cannot be previewed")
    result, rates = compute(uow.ctx, run)
    return simulation(run, result, rates)


def post_run(uow: UnitOfWork, run: ConsolidationRun) -> ConsolidationRun:
    ctx = uow.ctx
    group = ConsolidationGroup.objects.select_for_update().get(pk=run.group_id)
    access.require_feature(ctx, company_ids(group))
    if run.status != RunStatus.DRAFT:
        raise InvalidStateTransition(f"a {run.status} run cannot be posted")
    if run.created_by == ctx.principal_id or (ctx.on_behalf_of is not None and run.created_by == ctx.on_behalf_of):
        raise SodViolation("the preparer of a consolidation run cannot post it (D-023)")
    if group.closed_through and run.period <= group.closed_through:
        raise PeriodClosed(f"consolidation period {run.period} is closed")
    if ConsolidationRun.objects.filter(group=group, period=run.period, status=RunStatus.POSTED).exists():
        raise InvalidStateTransition("a run is already posted for this period; reverse it first")
    result, rates = compute(ctx, run)
    day = run.posting_date or run.document_date
    total = ZERO
    for no, e in enumerate(result.entries, start=1):
        entry = ConsolidationEntry.objects.create(
            tenant_id=ctx.tenant_id,
            run=run,
            group_id=group.id,
            entry_no=no,
            kind=e.kind,
            period=run.period,
            posting_date=day,
            memo=e.memo[:500],
            currency=run.currency,
            total_debit=e.total_debit,
            total_credit=e.total_credit,
            created_by=ctx.principal_id,
            created_at=clock.now(),
        )
        ConsolidationEntryLine.objects.bulk_create(
            [
                ConsolidationEntryLine(
                    tenant_id=ctx.tenant_id,
                    entry=entry,
                    line_no=i,
                    company_id=ln.company_id,
                    group_account=ln.group_account,
                    debit=ln.debit,
                    credit=ln.credit,
                    memo=ln.memo[:500],
                )
                for i, ln in enumerate(e.lines, start=1)
            ]
        )
        total += e.total_debit
    run.status = RunStatus.POSTED
    run.total = total
    run.rates = rates
    run.summary = _summary(result, rates)
    run.posting_date = day
    run.posted_by = ctx.principal_id
    run.posted_at = clock.now()
    run.save_versioned(
        ctx.principal_id, ["status", "total", "rates", "summary", "posting_date", "posted_by", "posted_at"]
    )
    uow.emit(RUN_POSTED_EVENT, {"run_id": str(run.id), "group_id": str(group.id)}, company_id=group.parent_company_id)
    for d in result.ic_differences:
        ag = IntercompanyAgreement.objects.filter(
            tenant_id=ctx.tenant_id,
            company_a_id__in=[d["company_a_id"], d["company_b_id"]],
            company_b_id__in=[d["company_a_id"], d["company_b_id"]],
        ).first()
        if ag is not None:
            uow.emit(
                MISMATCH_EVENT,
                {"agreement_id": str(ag.id), "difference": {"amount": str(d["difference"]), "currency": run.currency}},
                company_id=ag.company_a_id,
            )
    access.audit(
        uow,
        "post",
        "consolidation.run",
        run.id,
        group.parent_company_id,
        {"number": run.number, "entries": len(result.entries), "total": str(total)},
    )
    return run


def reverse_run(uow: UnitOfWork, run: ConsolidationRun, reason: str, on: dt.date | None = None) -> ConsolidationRun:
    """Reversal entries in the consolidation book (never deletes, C1-5)."""
    ctx = uow.ctx
    access.require(ctx, "consolidation.run.void", company_ids(run.group))
    if not reason or not reason.strip():
        raise ReasonRequired("a reason is required to reverse a consolidation run")
    if run.status != RunStatus.POSTED:
        raise InvalidStateTransition("only posted runs can be reversed")
    day = on or run.posting_date or run.document_date
    base = run.entries.count()
    for i, entry in enumerate(run.entries.filter(reversal_of__isnull=True).order_by("entry_no"), start=1):
        rev = ConsolidationEntry.objects.create(
            tenant_id=ctx.tenant_id,
            run=run,
            group_id=run.group_id,
            entry_no=base + i,
            kind=EntryKind.REVERSAL,
            period=run.period,
            posting_date=day,
            memo=f"Reverso de {entry.entry_no}: {reason}"[:500],
            currency=entry.currency,
            total_debit=entry.total_credit,
            total_credit=entry.total_debit,
            reversal_of=entry.id,
            created_by=ctx.principal_id,
            created_at=clock.now(),
        )
        ConsolidationEntryLine.objects.bulk_create(
            [
                ConsolidationEntryLine(
                    tenant_id=ctx.tenant_id,
                    entry=rev,
                    line_no=ln.line_no,
                    company_id=ln.company_id,
                    group_account=ln.group_account,
                    debit=ln.credit,
                    credit=ln.debit,
                    memo=ln.memo,
                )
                for ln in entry.lines.order_by("line_no")
            ]
        )
    run.status = RunStatus.REVERSED
    run.reversed_by = ctx.principal_id
    run.reversal_reason = reason[:2000]
    run.save_versioned(ctx.principal_id, ["status", "reversed_by", "reversal_reason"])
    access.audit(
        uow, "reverse", "consolidation.run", run.id, run.group.parent_company_id, {"number": run.number}, reason
    )
    return run


def close_period(uow: UnitOfWork, group: ConsolidationGroup, period: str) -> ConsolidationGroup:
    access.require(uow.ctx, "consolidation.group.update", company_ids(group))
    group.closed_through = _period(period)
    group.save_versioned(uow.ctx.principal_id, ["closed_through"])
    access.audit(
        uow, "close_period", "consolidation.group", group.id, group.parent_company_id, {"closed_through": period}
    )
    return group
