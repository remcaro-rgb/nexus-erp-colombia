"""Asset reports (ReportResult), GL = register reconciliation and NIIF/fiscal temporary differences.

All reads run on querysets already scoped by the caller (``assets.api`` applies ``PolicyService.scope_queryset``)
and under forced RLS. Amounts are strings (NUMERIC 20,4); ``synthetic`` is true in DEV (synthetic data).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet, Sum

from assets import engine
from assets.errors import FilterInvalid
from assets.models import (
    Asset,
    AssetBook,
    AssetBookValue,
    BookValueStatus,
    DepreciationLine,
    RegisterEntry,
    RegisterKind,
)
from assets.service import (
    ACCUM_DEPRECIATION,
    ACCUM_IMPAIRMENT,
    FIXED_ASSET_COST,
    REVALUATION_SURPLUS,
    role,
    state_of,
)
from kernel.tenancy import TenantContext

ZERO = Decimal(0)


def money(value: Decimal) -> str:
    return f"{value:.4f}"


def parse_month(value: str | None, pointer: str, *, end: bool = False, default: dt.date | None = None) -> dt.date:
    """``YYYY-MM`` (or an ISO date) → first (or last) day of that month."""
    if not value:
        if default is None:
            raise FilterInvalid("a period is required", pointer=pointer, reason="required")
        return default
    try:
        day = dt.date.fromisoformat(value) if len(value) == 10 else dt.date.fromisoformat(f"{value}-01")
    except ValueError:
        raise FilterInvalid("use YYYY-MM", pointer=pointer, reason="format") from None
    return engine.month_end(day) if end else engine.month_start(day)


def _columns(*cols: tuple[str, str, str]) -> list[dict[str, str]]:
    return [{"key": k, "label": label, "type": t} for k, label, t in cols]


def _sums(entries: QuerySet[RegisterEntry], *group: str) -> dict[tuple[Any, ...], dict[str, Decimal]]:
    out: dict[tuple[Any, ...], dict[str, Decimal]] = {}
    for row in entries.values(*group).annotate(
        cost=Sum("cost_delta"), acc=Sum("accumulated_delta"), imp=Sum("impairment_delta"), sur=Sum("surplus_delta")
    ):
        key = tuple(row[g] for g in group)
        out[key] = {k: row[k] or ZERO for k in ("cost", "acc", "imp", "sur")}
    return out


# --------------------------------------------------------------------------- register


def register_report(
    assets: QuerySet[Asset],
    entries: QuerySet[RegisterEntry],
    books: QuerySet[AssetBook],
    as_of: dt.date,
    book_id: uuid.UUID | None,
) -> dict[str, Any]:
    entries = entries.filter(date__lte=as_of)
    if book_id is not None:
        entries = entries.filter(book_id=book_id)
    sums = _sums(entries, "asset_id", "book_id")
    book_codes = {b.id: b.code for b in books}
    by_asset = {a.id: a for a in assets.select_related("category")}
    rows: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    nbv_by_book: dict[tuple[uuid.UUID, str], Decimal] = {}
    for (asset_id, bid), s in sorted(
        sums.items(), key=lambda kv: (by_asset[kv[0][0]].code if kv[0][0] in by_asset else "", str(kv[0][1]))
    ):
        asset = by_asset.get(asset_id)
        if asset is None or bid not in book_codes:
            continue  # not readable for this principal
        nbv = s["cost"] - s["acc"] - s["imp"]
        code = book_codes[bid]
        nbv_by_book[(asset_id, code)] = nbv
        rows.append(
            {
                "asset_id": str(asset.id),
                "code": asset.code,
                "name": asset.name,
                "category": asset.category.code,
                "status": asset.lifecycle,
                "in_service_date": asset.in_service_date.isoformat() if asset.in_service_date else None,
                "location": asset.location,
                "book": code,
                "cost": money(s["cost"]),
                "accumulated_depreciation": money(s["acc"]),
                "accumulated_impairment": money(s["imp"]),
                "revaluation_surplus": money(s["sur"]),
                "net_book_value": money(nbv),
            }
        )
        for k, v in (
            ("cost", s["cost"]),
            ("accumulated_depreciation", s["acc"]),
            ("accumulated_impairment", s["imp"]),
            ("net_book_value", nbv),
        ):
            totals[f"{code}.{k}"] += v
    for asset_id in {a for a, _ in nbv_by_book}:
        if (asset_id, "NIIF") in nbv_by_book and (asset_id, "FISCAL") in nbv_by_book:
            totals["temporary_difference"] += nbv_by_book[(asset_id, "FISCAL")] - nbv_by_book[(asset_id, "NIIF")]
    for row in rows:
        aid = uuid.UUID(row["asset_id"])
        if (aid, "NIIF") in nbv_by_book and (aid, "FISCAL") in nbv_by_book:
            row["temporary_difference"] = money(nbv_by_book[(aid, "FISCAL")] - nbv_by_book[(aid, "NIIF")])
    return {
        "report": "assets.register",
        "parameters": {"as_of": as_of.isoformat(), "book_id": str(book_id) if book_id else None},
        "columns": _columns(
            ("code", "Placa", "string"),
            ("name", "Descripción", "string"),
            ("category", "Categoría", "string"),
            ("book", "Libro", "string"),
            ("status", "Estado", "string"),
            ("cost", "Costo", "COP"),
            ("accumulated_depreciation", "Depreciación acumulada", "COP"),
            ("accumulated_impairment", "Deterioro acumulado", "COP"),
            ("net_book_value", "Valor neto en libros", "COP"),
        ),
        "rows": rows,
        "totals": {k: money(v) for k, v in sorted(totals.items())},
        "warnings": [],
        "synthetic": True,
    }


def temporary_differences(entries: QuerySet[RegisterEntry], as_of: dt.date) -> dict[uuid.UUID, Decimal]:
    """Per asset: fiscal NBV − NIIF NBV (feeds the ledger TaxDifference register, CCR P14-5)."""
    sums = _sums(entries.filter(date__lte=as_of), "asset_id", "book__framework")
    nbv: dict[tuple[uuid.UUID, str], Decimal] = {k: v["cost"] - v["acc"] - v["imp"] for k, v in sums.items()}
    out: dict[uuid.UUID, Decimal] = {}
    for asset_id, framework in nbv:
        if framework == "NIIF" and (asset_id, "FISCAL") in nbv:
            out[asset_id] = nbv[(asset_id, "FISCAL")] - nbv[(asset_id, "NIIF")]
    return out


# --------------------------------------------------------------------------- depreciation schedule


def schedule_report(bv: AssetBookValue, lines: QuerySet[DepreciationLine], horizon: int = 600) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for dl in (
        lines.filter(asset_id=bv.asset_id, run__book_id=bv.book_id, run__status="POSTED")
        .select_related("run")
        .order_by("run__period_start")
    ):
        rows.append(
            {
                "period": f"{dl.run.period_start:%Y-%m}",
                "amount": money(dl.amount),
                "accumulated": money(dl.accumulated_after),
                "net_book_value": money(dl.nbv_after),
                "posted": True,
            }
        )
    warnings: list[dict[str, str]] = []
    if bv.status == BookValueStatus.ACTIVE and bv.method != "UNITS" and bv.depreciation_start is not None:
        st = state_of(bv)
        start = engine.add_months(bv.last_period_end, 1) if bv.last_period_end else bv.depreciation_start
        for i, amount in enumerate(engine.schedule(st, min(st.periods_left, horizon))):
            st = engine.apply(st, amount)
            rows.append(
                {
                    "period": f"{engine.add_months(start, i):%Y-%m}",
                    "amount": money(amount),
                    "accumulated": money(st.accumulated),
                    "net_book_value": money(st.nbv),
                    "posted": False,
                }
            )
    elif bv.method == "UNITS":
        warnings.append(
            {
                "code": "units_projection_unavailable",
                "message": "Unidades de producción: la proyección depende de las unidades reales.",
            }
        )
    return {
        "report": "assets.depreciation_schedule",
        "parameters": {
            "asset_id": str(bv.asset_id),
            "book_id": str(bv.book_id),
            "method": bv.method,
            "useful_life_months": bv.useful_life_months,
            "salvage_value": money(bv.salvage_value),
        },
        "columns": _columns(
            ("period", "Periodo", "string"),
            ("amount", "Depreciación", "COP"),
            ("accumulated", "Acumulada", "COP"),
            ("net_book_value", "Valor neto", "COP"),
            ("posted", "Contabilizada", "boolean"),
        ),
        "rows": rows,
        "totals": {
            "posted": money(sum((Decimal(r["amount"]) for r in rows if r["posted"]), ZERO)),
            "projected": money(sum((Decimal(r["amount"]) for r in rows if not r["posted"]), ZERO)),
        },
        "warnings": warnings,
        "synthetic": True,
    }


# --------------------------------------------------------------------------- movement (NIIF disclosure helper)

MOVEMENT_COLUMNS = (
    "opening",
    "additions",
    "revaluations",
    "impairments",
    "disposals",
    "depreciation",
    "transfers",
    "closing",
)


def movement_report(
    entries: QuerySet[RegisterEntry],
    categories: dict[uuid.UUID, str],
    start: dt.date,
    end: dt.date,
    book_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Per category, carrying amount (NBV) roll-forward: opening + movements = closing."""
    if start > end:
        raise FilterInvalid("period_from is after period_to", pointer="/period_from", reason="range")
    if book_id is not None:
        entries = entries.filter(book_id=book_id)
    acc: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: {c: ZERO for c in MOVEMENT_COLUMNS})
    for row in (
        entries.filter(date__lt=start)
        .values("category_id")
        .annotate(cost=Sum("cost_delta"), a=Sum("accumulated_delta"), i=Sum("impairment_delta"))
    ):
        acc[row["category_id"]]["opening"] += (row["cost"] or ZERO) - (row["a"] or ZERO) - (row["i"] or ZERO)
    kind_col = {
        RegisterKind.ACQUISITION: "additions",
        RegisterKind.OPENING: "additions",
        RegisterKind.REVALUATION: "revaluations",
        RegisterKind.IMPAIRMENT: "impairments",
        RegisterKind.DISPOSAL: "disposals",
        RegisterKind.DEPRECIATION: "depreciation",
        RegisterKind.TRANSFER_IN: "transfers",
        RegisterKind.TRANSFER_OUT: "transfers",
        RegisterKind.ESTIMATE_CHANGE: "transfers",
    }
    for row in (
        entries.filter(date__gte=start, date__lte=end)
        .values("category_id", "kind")
        .annotate(cost=Sum("cost_delta"), a=Sum("accumulated_delta"), i=Sum("impairment_delta"))
    ):
        delta = (row["cost"] or ZERO) - (row["a"] or ZERO) - (row["i"] or ZERO)
        acc[row["category_id"]][kind_col[row["kind"]]] += delta
    rows = []
    totals = {c: ZERO for c in MOVEMENT_COLUMNS}
    for cat_id, cols in sorted(acc.items(), key=lambda kv: categories.get(kv[0], "")):
        if cat_id not in categories:
            continue
        cols["closing"] = sum((cols[c] for c in MOVEMENT_COLUMNS if c != "closing"), ZERO)
        rows.append({"category": categories[cat_id], **{c: money(cols[c]) for c in MOVEMENT_COLUMNS}})
        for c in MOVEMENT_COLUMNS:
            totals[c] += cols[c]
    return {
        "report": "assets.movement",
        "parameters": {
            "period_from": f"{start:%Y-%m}",
            "period_to": f"{end:%Y-%m}",
            "book_id": str(book_id) if book_id else None,
            "basis": "net_book_value",
        },
        "columns": _columns(
            ("category", "Categoría", "string"),
            ("opening", "Saldo inicial", "COP"),
            ("additions", "Adiciones", "COP"),
            ("revaluations", "Revaluaciones", "COP"),
            ("impairments", "Deterioro", "COP"),
            ("disposals", "Bajas", "COP"),
            ("depreciation", "Depreciación", "COP"),
            ("transfers", "Traslados", "COP"),
            ("closing", "Saldo final", "COP"),
        ),
        "rows": rows,
        "totals": {c: money(v) for c, v in totals.items()},
        "warnings": [],
        "synthetic": True,
    }


# --------------------------------------------------------------------------- GL = register (invariant 5)


def gl_vs_register(ctx: TenantContext, company_id: uuid.UUID, book: AssetBook, as_of: dt.date) -> dict[str, Any]:
    """Per account role: Σ GL (ledger lines of the book's accounts for the role) vs Σ register deltas."""
    from ledger.models import AccountDetermination, JournalLine

    entries = RegisterEntry.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, book=book, date__lte=as_of
    ).select_related("asset__category")
    register: dict[str, Decimal] = defaultdict(lambda: ZERO)
    debit_nature: set[str] = set()
    for e in entries:
        cat = e.asset.category
        debit_nature.add(role(cat, FIXED_ASSET_COST))
        register[role(cat, FIXED_ASSET_COST)] += e.cost_delta
        register[role(cat, ACCUM_DEPRECIATION)] += e.accumulated_delta
        register[role(cat, ACCUM_IMPAIRMENT)] += e.impairment_delta
        register[role(cat, REVALUATION_SURPLUS)] += e.surplus_delta
    out: dict[str, Any] = {}
    for role_code, reg in sorted(register.items()):
        accounts = AccountDetermination.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, role_code=role_code, active=True
        ).values_list("account_id", flat=True)
        agg = JournalLine.objects.filter(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            book_id__in=_ledger_book_ids(ctx, company_id, book),
            account_id__in=list(accounts),
            posting_date__lte=as_of,
        ).aggregate(d=Sum("debit"), c=Sum("credit"))
        debit_minus_credit = (agg["d"] or ZERO) - (agg["c"] or ZERO)
        # register signs: cost is a debit balance; accumulated / impairment / surplus are credit balances
        gl = debit_minus_credit if role_code in debit_nature else -debit_minus_credit
        out[role_code] = {"gl": money(gl), "register": money(reg), "difference": money(gl - reg)}
    return out


def _ledger_book_ids(ctx: TenantContext, company_id: uuid.UUID, book: AssetBook) -> list[uuid.UUID]:
    from ledger.models import Book

    return list(
        Book.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=book.ledger_book_code).values_list(
            "id", flat=True
        )
    )
