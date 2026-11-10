"""Fixed-asset domain services (P14). Every function runs inside the caller's unit of work.

GL effects follow shared/07 §5 and are posted **in-line** (same transaction as the document, the register rows,
the audit record and the outbox event). Amounts per book differ (NIIF vs fiscal), so every book gets its own
entry (``assets.posting.post_to_book``), except the acquisition, which has the same amount in every book.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from django.conf import settings
from django.db import IntegrityError, transaction

from assets import engine
from assets.errors import (
    AssetAlreadyDisposed,
    DepreciationRunExists,
    DocumentImmutable,
    InvalidStateTransition,
    PeriodClosed,
    ReasonRequired,
    SodViolation,
    invalid,
)
from assets.models import (
    SLOTS,
    Asset,
    AssetBook,
    AssetBookValue,
    AssetCategory,
    BookValueStatus,
    CategoryBookDefault,
    DepreciationLine,
    DepreciationRun,
    Disposal,
    DisposalKind,
    DocStatus,
    Framework,
    Impairment,
    Lifecycle,
    MasterStatus,
    Method,
    PhysicalCount,
    RegisterEntry,
    RegisterKind,
    Revaluation,
    StartConvention,
    Transfer,
)
from assets.posting import SimulatedLine, entry_ids, post_all_books, post_to_book, simulate_to_book
from kernel import clock, sequences, services
from kernel.errors import NotFound, SequenceNotFound
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.common import DimensionValues, DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingResult
from kernel.models import Sequence
from kernel.money import q_amount, q_qty
from kernel.parameters import ParameterProvider, enforce
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
HUNDRED = Decimal(100)
JOURNAL = "ASSETS"

# Account roles (shared/07 §5). Requested for ledger.setup.ROLES + P21 determinations in CCR P14-2.
FIXED_ASSET_COST = "FIXED_ASSET_COST"
ACCUM_DEPRECIATION = "ACCUM_DEPRECIATION"
DEPRECIATION_EXPENSE = "DEPRECIATION_EXPENSE"
REVALUATION_SURPLUS = "REVALUATION_SURPLUS"
REVALUATION_LOSS = "REVALUATION_LOSS"
IMPAIRMENT_EXPENSE = "IMPAIRMENT_EXPENSE"
ACCUM_IMPAIRMENT = "ACCUM_IMPAIRMENT"
GAIN_ON_DISPOSAL = "GAIN_ON_DISPOSAL"
LOSS_ON_DISPOSAL = "LOSS_ON_DISPOSAL"
ASSET_DISPOSAL_PROCEEDS = "ASSET_DISPOSAL_PROCEEDS"
RETAINED_EARNINGS = "RETAINED_EARNINGS"
ACCOUNT_ROLES = (
    (FIXED_ASSET_COST, "Activos fijos — costo"),
    (ACCUM_DEPRECIATION, "Depreciación acumulada"),
    (DEPRECIATION_EXPENSE, "Gasto depreciación"),
    (REVALUATION_SURPLUS, "Superávit por revaluación (ORI)"),
    (REVALUATION_LOSS, "Pérdida por revaluación"),
    (IMPAIRMENT_EXPENSE, "Gasto por deterioro"),
    (ACCUM_IMPAIRMENT, "Deterioro acumulado"),
    (GAIN_ON_DISPOSAL, "Utilidad en venta de activos"),
    (LOSS_ON_DISPOSAL, "Pérdida en baja de activos"),
    (ASSET_DISPOSAL_PROCEEDS, "Puente — producto de venta de activos"),
    ("CIP", "Construcciones en curso"),
    ("OPENING_BALANCE_EQUITY", "Saldos iniciales"),
)
ROLE_KEYS = (
    FIXED_ASSET_COST,
    ACCUM_DEPRECIATION,
    DEPRECIATION_EXPENSE,
    REVALUATION_SURPLUS,
    REVALUATION_LOSS,
    IMPAIRMENT_EXPENSE,
    ACCUM_IMPAIRMENT,
    GAIN_ON_DISPOSAL,
    LOSS_ON_DISPOSAL,
    ASSET_DISPOSAL_PROCEEDS,
)
DEFAULT_BOOKS = (("NIIF", "Libro NIIF", Framework.NIIF), ("FISCAL", "Libro fiscal", Framework.FISCAL))
RUN_EVENT = "assets.depreciation_run.posted.v1"
DISPOSED_EVENT = "assets.asset.disposed.v1"


# =========================================================================== helpers


def _dec(value: Any, pointer: str, *, allow_negative: bool = False) -> Decimal:
    if isinstance(value, dict):
        value = value.get("amount", value.get("value"))
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise invalid("a decimal amount is required", pointer, "decimal_required") from None
    if not result.is_finite() or (result < ZERO and not allow_negative):
        raise invalid("the amount must be a finite non-negative decimal", pointer, "decimal_invalid")
    return result


def _date(value: Any, pointer: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise invalid("an ISO date is required", pointer, "date_required") from None


def _uuid(value: Any, pointer: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise invalid("a UUID is required", pointer, "uuid_required") from None


def dims_of(obj: Any) -> DimensionValues:
    return DimensionValues(**{s: getattr(obj, s, None) for s in SLOTS})


def dims_from(data: dict[str, Any] | None, pointer: str = "/dimensions") -> dict[str, uuid.UUID | None]:
    data = data or {}
    out: dict[str, uuid.UUID | None] = {}
    for slot in SLOTS:
        raw = data.get(slot)
        out[slot] = None if raw in (None, "") else _uuid(raw, f"{pointer}/{slot}")
    return out


def _audit(
    uow: UnitOfWork,
    action: str,
    resource: str,
    rid: uuid.UUID,
    company: uuid.UUID,
    changes: dict[str, Any],
    reason: str = "",
) -> None:
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(uow, AuditEntry(action, resource, rid, company, changes, reason[:2000]))


def next_number(uow: UnitOfWork, company_id: uuid.UUID, code: str, day: dt.date) -> str:
    try:
        allocated = sequences.allocate(company_id, code, "", on_date=day)
    except SequenceNotFound:
        try:
            with transaction.atomic(using=uow.using):
                Sequence.objects.create(
                    tenant_id=uow.ctx.tenant_id, company_id=company_id, sequence_code=code, prefix="", padding=6
                )
        except IntegrityError:
            pass  # created concurrently
        allocated = sequences.allocate(company_id, code, "", on_date=day)
    return f"{code}-{allocated.formatted}"


def role(category: AssetCategory, key: str) -> str:
    if key == FIXED_ASSET_COST and category.account_role:
        return category.account_role
    override = (category.role_overrides or {}).get(key)
    return str(override) if override else key


def line(
    role_code: str, side: Literal["DEBIT", "CREDIT"], amount: Decimal, dims: DimensionValues, memo: str = "", **kw: Any
) -> PostingLine:
    return PostingLine(
        account_role=role_code, side=side, amount=q_amount(amount), dimensions=dims, memo=memo[:200], **kw
    )


def _nonzero(lines: Iterable[PostingLine]) -> tuple[PostingLine, ...]:
    return tuple(ln for ln in lines if ln.amount != ZERO)


def period_row(
    uow: UnitOfWork, company_id: uuid.UUID, *, period_id: uuid.UUID | None = None, day: dt.date | None = None
) -> Any:
    """Ledger period (read-only) by id or by date; ``period_closed`` when undefined or CLOSED."""
    from ledger.models import Period

    qs = Period.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id)
    row = (
        qs.filter(id=period_id).first()
        if period_id
        else qs.filter(start__lte=day, end__gte=day, number__lte=12).first()
    )
    if row is None:
        if period_id:
            raise NotFound("accounting period not found")
        raise PeriodClosed(f"no accounting period is defined for {day}")
    if row.status == "CLOSED":
        raise PeriodClosed(f"period {row.code} is closed")
    return row


def is_production() -> bool:
    return str(getattr(settings, "ERP_ENVIRONMENT", "dev")).lower() in {"prod", "production"}


def _book(uow: UnitOfWork, company_id: uuid.UUID, ref: Any) -> AssetBook:
    qs = AssetBook.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id)
    try:
        found = qs.filter(id=uuid.UUID(str(ref))).first()
    except ValueError:
        found = qs.filter(code=str(ref)).first()
    if found is None:
        raise NotFound("asset book not found")
    return found


def get_asset(uow: UnitOfWork, company_id: uuid.UUID, asset_id: Any, *, lock: bool = False) -> Asset:
    qs = Asset.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id)
    if lock:
        qs = qs.select_for_update()
    found = qs.select_related("category").filter(id=_uuid(asset_id, "/id")).first()
    if found is None:
        raise NotFound("asset not found")
    return found


def state_of(bv: AssetBookValue, currency: str = "COP") -> engine.BookState:
    return engine.BookState(
        method=bv.method,
        cost=bv.cost,
        salvage=bv.salvage_value,
        accumulated=bv.accumulated,
        impairment=bv.impairment,
        anchor_base=bv.anchor_base,
        anchor_periods=bv.anchor_periods,
        periods_since_anchor=bv.periods_since_anchor,
        anchor_units=bv.anchor_units,
        units_since_anchor=bv.units_since_anchor,
        declining_factor=bv.declining_factor,
        currency=currency,
    )


def _store_anchor(bv: AssetBookValue, st: engine.BookState) -> None:
    bv.anchor_base = st.anchor_base
    bv.anchor_periods = st.anchor_periods
    bv.periods_since_anchor = st.periods_since_anchor
    bv.anchor_units = st.anchor_units
    bv.units_since_anchor = st.units_since_anchor


def reanchor(bv: AssetBookValue, currency: str = "COP") -> None:
    """Prospective re-estimation from the current carrying amounts (remaining life / units unchanged)."""
    st = state_of(bv, currency)
    if bv.method == Method.UNITS:
        remaining_units = (bv.units_total or ZERO) - bv.units_used
        if remaining_units <= ZERO or st.remaining_base == ZERO:
            bv.status = BookValueStatus.FULLY_DEPRECIATED if st.remaining_base == ZERO else bv.status
            return
        st = engine.anchor(st, max(bv.useful_life_months - bv.periods_depreciated, 1), remaining_units)
    else:
        left = bv.useful_life_months - bv.periods_depreciated
        if left < 1 or st.remaining_base == ZERO:
            if st.remaining_base == ZERO:
                bv.status = BookValueStatus.FULLY_DEPRECIATED
            return
        st = engine.anchor(st, left)
    if bv.status == BookValueStatus.FULLY_DEPRECIATED:
        bv.status = BookValueStatus.ACTIVE
    _store_anchor(bv, st)


def register(
    uow: UnitOfWork,
    asset: Asset,
    bv: AssetBookValue,
    kind: str,
    day: dt.date,
    source: DocumentRef,
    *,
    cost: Decimal = ZERO,
    accumulated: Decimal = ZERO,
    impairment: Decimal = ZERO,
    surplus: Decimal = ZERO,
    units: Decimal | None = None,
    entry: PostingResult | None = None,
    memo: str = "",
    slots: dict[str, Any] | None = None,
) -> RegisterEntry:
    return RegisterEntry.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=asset.company_id,
        asset=asset,
        book=bv.book,
        kind=kind,
        date=day,
        cost_delta=q_amount(cost),
        accumulated_delta=q_amount(accumulated),
        impairment_delta=q_amount(impairment),
        surplus_delta=q_amount(surplus),
        units=units,
        category_id=asset.category_id,
        source_type=source.type,
        source_id=source.id,
        journal_entry_id=entry.entry_id if entry is not None and entry.entry_id.int else None,
        memo=memo[:500],
        created_by=uow.ctx.principal_id,
        **(slots if slots is not None else asset.slots()),
    )


def _save(obj: Any, uow: UnitOfWork, fields: list[str]) -> None:
    obj.save_versioned(uow.ctx.principal_id, fields)


# =========================================================================== setup / masters


def setup_company(uow: UnitOfWork, company_id: uuid.UUID) -> list[AssetBook]:
    """Idempotent: the NIIF and FISCAL depreciation books mapped to the ledger books of the same code."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    books = []
    for code, name, framework in DEFAULT_BOOKS:
        book, _ = AssetBook.objects.get_or_create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            code=code,
            defaults={"name": name, "framework": framework, "ledger_book_code": code, "created_by": ctx.principal_id},
        )
        books.append(book)
    return books


def active_books(uow: UnitOfWork, company_id: uuid.UUID) -> list[AssetBook]:
    return list(
        AssetBook.objects.filter(
            tenant_id=uow.ctx.tenant_id, company_id=company_id, status=MasterStatus.ACTIVE
        ).order_by("framework", "code")
    )


def validate_category(data: dict[str, Any]) -> None:
    life = data.get("useful_life_months")
    if life is not None and (not isinstance(life, int) or isinstance(life, bool) or life < 1 or life > 1200):
        raise invalid("useful_life_months must be an integer between 1 and 1200", "/useful_life_months", "range")
    method = data.get("method")
    if method is not None and method not in {Method.STRAIGHT_LINE, Method.DECLINING, Method.UNITS}:
        raise invalid("method must be STRAIGHT_LINE, DECLINING or UNITS", "/method", "enum")


def set_book_default(
    uow: UnitOfWork,
    category: AssetCategory,
    book: AssetBook,
    *,
    method: str,
    life: int,
    salvage_pct: Decimal = ZERO,
    rate_parameter_code: str = "",
) -> CategoryBookDefault:
    if method not in Method.values:
        raise invalid("unknown method", "/method", "enum")
    obj, _ = CategoryBookDefault.objects.update_or_create(
        category=category,
        book=book,
        defaults={
            "tenant_id": uow.ctx.tenant_id,
            "company_id": category.company_id,
            "method": method,
            "useful_life_months": life,
            "salvage_pct": salvage_pct,
            "rate_parameter_code": rate_parameter_code,
        },
    )
    return obj


# =========================================================================== assets


def _book_settings(uow: UnitOfWork, asset: Asset, book: AssetBook) -> dict[str, Any]:
    default = CategoryBookDefault.objects.filter(category=asset.category, book=book).first()
    cat = asset.category
    if default is not None:
        salvage = q_amount(asset.acquisition_cost * default.salvage_pct / HUNDRED)
        return {"method": default.method, "useful_life_months": default.useful_life_months, "salvage_value": salvage}
    return {"method": cat.method, "useful_life_months": cat.useful_life_months, "salvage_value": ZERO}


def create_asset(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> Asset:
    ctx = uow.ctx
    ctx.require_company(company_id)
    category = AssetCategory.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, id=_uuid(data.get("category_id"), "/category_id")
    ).first()
    if category is None or category.status != MasterStatus.ACTIVE:
        raise invalid("category not found or archived", "/category_id", "not_found")
    parent = None
    if data.get("parent_id"):
        parent = get_asset(uow, company_id, data["parent_id"])
        if parent.lifecycle == Lifecycle.DISPOSED:
            raise AssetAlreadyDisposed("the parent asset is disposed")
    cost = q_amount(_dec(data.get("acquisition_cost"), "/acquisition_cost"))
    code = str(data.get("code") or "").strip()
    if not code:
        code = next_number(uow, company_id, "AF", _date(data.get("acquisition_date"), "/acquisition_date"))
    if Asset.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).exists():
        raise invalid("an asset with this code already exists", "/code", "duplicate")
    raw_cost = data.get("acquisition_cost")
    currency = str(raw_cost.get("currency") or "COP") if isinstance(raw_cost, dict) else "COP"
    if currency != "COP":
        raise invalid("assets are carried in the functional currency (COP)", "/acquisition_cost/currency", "currency")
    slots = dims_from(data.get("dimensions"))
    branch = (data.get("dimensions") or {}).get("branch_id") or data.get("branch_id")
    asset = Asset.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=str(data.get("name") or "")[:200] or code,
        category=category,
        parent=parent,
        acquisition_cost=cost,
        currency=currency,
        acquisition_date=_date(data.get("acquisition_date"), "/acquisition_date"),
        location=data.get("location"),
        custodian_employee_id=_uuid(data["custodian_employee_id"], "/custodian_employee_id")
        if data.get("custodian_employee_id")
        else None,
        branch_id=_uuid(branch, "/dimensions/branch_id") if branch else None,
        serial=str(data.get("serial") or "")[:120],
        supplier_id=_uuid(data["supplier_id"], "/supplier_id") if data.get("supplier_id") else None,
        source_type=str(data.get("source_type") or "")[:80],
        source_id=_uuid(data["source_id"], "/source_id") if data.get("source_id") else None,
        source_number=str(data.get("source_number") or "")[:60],
        source_line_no=data.get("source_line_no"),
        cost_components=data.get("cost_components") or [],
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        **slots,
    )
    for book in active_books(uow, company_id):
        cfg = _book_settings(uow, asset, book)
        AssetBookValue.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, asset=asset, book=book, created_by=ctx.principal_id, **cfg
        )
    _audit(uow, "assets.asset.create", "assets.asset", asset.id, company_id, {"code": code, "cost": str(cost)})
    return asset


MUTABLE_ALWAYS = ("name", "location", "custodian_employee_id")
MUTABLE_DRAFT = ("code", "category_id", "acquisition_cost", "acquisition_date", "dimensions")


def update_asset(uow: UnitOfWork, asset: Asset, data: dict[str, Any]) -> Asset:
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("a disposed asset cannot be changed")
    changed: list[str] = []
    for key in data:
        if key in MUTABLE_DRAFT and asset.lifecycle != Lifecycle.DRAFT:
            raise InvalidStateTransition(f"{key} can only change while the asset is a draft (use :transfer)")
    if "name" in data:
        asset.name = str(data["name"])[:200]
        changed.append("name")
    if "location" in data:
        asset.location = data["location"]
        changed.append("location")
    if "custodian_employee_id" in data:
        raw = data["custodian_employee_id"]
        asset.custodian_employee_id = _uuid(raw, "/custodian_employee_id") if raw else None
        changed.append("custodian_employee_id")
    if "code" in data:
        asset.code = str(data["code"])[:40]
        changed.append("code")
    if "acquisition_date" in data:
        asset.acquisition_date = _date(data["acquisition_date"], "/acquisition_date")
        changed.append("acquisition_date")
    if "dimensions" in data:
        for slot, value in dims_from(data["dimensions"]).items():
            setattr(asset, slot, value)
            changed.append(slot)
    reset_books = False
    if "category_id" in data:
        category = AssetCategory.objects.filter(
            tenant_id=uow.ctx.tenant_id, company_id=asset.company_id, id=_uuid(data["category_id"], "/category_id")
        ).first()
        if category is None:
            raise invalid("category not found", "/category_id", "not_found")
        asset.category = category
        changed.append("category")
        reset_books = True
    if "acquisition_cost" in data:
        asset.acquisition_cost = q_amount(_dec(data["acquisition_cost"], "/acquisition_cost"))
        changed.append("acquisition_cost")
        reset_books = True
    if changed:
        _save(asset, uow, changed)
    if reset_books:
        for bv in AssetBookValue.objects.filter(asset=asset).select_related("book"):
            for k, v in _book_settings(uow, asset, bv.book).items():
                setattr(bv, k, v)
            _save(bv, uow, ["method", "useful_life_months", "salvage_value"])
    _audit(uow, "assets.asset.update", "assets.asset", asset.id, asset.company_id, {"fields": sorted(data)})
    return asset


def delete_asset(uow: UnitOfWork, asset: Asset) -> None:
    if asset.lifecycle != Lifecycle.DRAFT or RegisterEntry.objects.filter(asset=asset).exists():
        raise InvalidStateTransition("only a draft asset without movements can be deleted")
    if asset.components.exists():
        raise InvalidStateTransition("the asset has components")
    AssetBookValue.objects.filter(asset=asset).delete()
    _audit(uow, "assets.asset.delete", "assets.asset", asset.id, asset.company_id, {"code": asset.code})
    asset.delete()


def _check_fiscal_life(uow: UnitOfWork, asset: Asset, bv: AssetBookValue, on_date: dt.date) -> None:
    """FISCAL books: annual rate implied by the life ≤ the ET maximum rate parameter (legal review)."""
    cat = asset.category
    if cat.min_life_months and bv.useful_life_months < cat.min_life_months:
        raise invalid("useful life below the category range", "/parameters/books/useful_life_months", "range")
    if cat.max_life_months and bv.useful_life_months > cat.max_life_months:
        raise invalid("useful life above the category range", "/parameters/books/useful_life_months", "range")
    default = CategoryBookDefault.objects.filter(category=cat, book=bv.book).first()
    if bv.book.framework != Framework.FISCAL or default is None or not default.rate_parameter_code:
        return
    provider = services.get(ParameterProvider)
    value = enforce(
        provider.get(default.rate_parameter_code, on_date, asset.company_id),
        default.rate_parameter_code,
        production=is_production(),
    )
    max_rate = _dec(value.value, "/parameters/rate")
    implied = Decimal(12) * HUNDRED / Decimal(bv.useful_life_months)
    if bv.method in (Method.STRAIGHT_LINE, Method.SUM_OF_YEARS) and implied > max_rate:
        raise invalid(
            f"fiscal annual rate {implied:.4f}% exceeds the maximum {max_rate}% ({default.rate_parameter_code})",
            "/parameters/books/useful_life_months",
            "fiscal_rate_exceeded",
        )
    bv.parameter_snapshot = value.snapshot()


def activate(uow: UnitOfWork, asset: Asset, params: dict[str, Any], effective_date: dt.date | None) -> Asset:
    """DRAFT → ACTIVE: in-service date, per-book overrides, acquisition (and opening) GL, register rows."""
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is disposed")
    if asset.lifecycle != Lifecycle.DRAFT:
        raise InvalidStateTransition("only a draft asset can be activated")
    in_service = _date(params.get("in_service_date") or effective_date or asset.acquisition_date, "/effective_date")
    if in_service < asset.acquisition_date:
        raise invalid("the in-service date precedes the acquisition date", "/parameters/in_service_date", "range")
    acquired = RegisterEntry.objects.filter(asset=asset, kind=RegisterKind.ACQUISITION).exists()
    counterpart = str(params.get("counterpart_role") or "")
    if not acquired and not counterpart:
        raise invalid(
            "the acquisition is not in the GL yet: give parameters.counterpart_role (e.g. AP, BANK, CIP, "
            "OPENING_BALANCE_EQUITY)",
            "/parameters/counterpart_role",
            "required",
        )
    overrides = {str(o.get("book_id") or o.get("book_code")): o for o in (params.get("books") or [])}
    source = DocumentRef("assets.asset", asset.id, asset.code)
    dims = dims_of(asset)
    bvs = list(AssetBookValue.objects.select_for_update().filter(asset=asset).select_related("book").order_by("id"))
    if not bvs:
        raise invalid("no depreciation book is configured", "/books", "not_found")
    result: PostingResult | None = None
    if counterpart:
        result = post_all_books(
            uow,
            PostingRequest(
                company_id=asset.company_id,
                posting_date=in_service,
                source=source,
                purpose="acquisition",
                journal_code=JOURNAL,
                branch_id=asset.branch_id,
                lines=(
                    line(role(asset.category, FIXED_ASSET_COST), "DEBIT", asset.acquisition_cost, dims, asset.code),
                    line(counterpart, "CREDIT", asset.acquisition_cost, dims, asset.code),
                ),
            ),
        )
    for bv in bvs:
        o = overrides.get(str(bv.book_id)) or overrides.get(bv.book.code) or {}
        if "method" in o:
            if o["method"] not in Method.values:
                raise invalid("unknown method", "/parameters/books/method", "enum")
            bv.method = o["method"]
        if "useful_life_months" in o:
            bv.useful_life_months = int(o["useful_life_months"])
        if "salvage_value" in o:
            bv.salvage_value = q_amount(_dec(o["salvage_value"], "/parameters/books/salvage_value"))
        if "declining_factor" in o:
            bv.declining_factor = _dec(o["declining_factor"], "/parameters/books/declining_factor")
        if "units_total" in o:
            bv.units_total = q_qty(_dec(o["units_total"], "/parameters/books/units_total"))
        if bv.useful_life_months < 1:
            raise invalid("useful life must be at least one month", "/parameters/books/useful_life_months", "range")
        if bv.salvage_value > asset.acquisition_cost:
            raise invalid("salvage value exceeds the cost", "/parameters/books/salvage_value", "range")
        if bv.method == Method.UNITS and not bv.units_total:
            raise invalid("units of production need units_total", "/parameters/books/units_total", "required")
        _check_fiscal_life(uow, asset, bv, in_service)
        convention = o.get("start_convention") or bv.book.start_convention
        if convention not in StartConvention.values:
            raise invalid("unknown start convention", "/parameters/books/start_convention", "enum")
        bv.depreciation_start = engine.depreciation_start(in_service, convention)
        bv.cost = asset.acquisition_cost
        if counterpart:
            register(uow, asset, bv, RegisterKind.ACQUISITION, in_service, source, cost=bv.cost, entry=result)
        opening = q_amount(_dec(o.get("opening_accumulated", 0), "/parameters/books/opening_accumulated"))
        if opening > ZERO:
            if not counterpart:
                raise invalid("opening balances need a counterpart_role", "/parameters/counterpart_role", "required")
            if opening > bv.cost - bv.salvage_value:
                raise invalid("opening accumulated exceeds the depreciable base", "/parameters/books", "range")
            bv.periods_depreciated = int(o.get("opening_periods", 0))
            bv.units_used = q_qty(_dec(o.get("opening_units", 0), "/parameters/books/opening_units"))
            opening_entry = post_to_book(
                uow,
                PostingRequest(
                    company_id=asset.company_id,
                    posting_date=in_service,
                    source=source,
                    purpose=f"opening:{bv.book.code}",
                    journal_code=JOURNAL,
                    branch_id=asset.branch_id,
                    lines=(
                        line(counterpart, "DEBIT", opening, dims, asset.code),
                        line(role(asset.category, ACCUM_DEPRECIATION), "CREDIT", opening, dims, asset.code),
                    ),
                ),
                bv.book.ledger_book_code,
            )
            bv.accumulated = opening
            register(uow, asset, bv, RegisterKind.OPENING, in_service, source, accumulated=opening, entry=opening_entry)
        bv.status = BookValueStatus.ACTIVE
        reanchor(bv, asset.currency)
        _save(
            bv,
            uow,
            [
                "method",
                "useful_life_months",
                "salvage_value",
                "declining_factor",
                "units_total",
                "depreciation_start",
                "cost",
                "accumulated",
                "periods_depreciated",
                "units_used",
                "status",
                "anchor_base",
                "anchor_periods",
                "anchor_units",
                "periods_since_anchor",
                "units_since_anchor",
                "parameter_snapshot",
            ],
        )
    asset.in_service_date = in_service
    asset.lifecycle = Lifecycle.ACTIVE
    _save(asset, uow, ["in_service_date", "lifecycle"])
    _audit(
        uow,
        "assets.asset.activate",
        "assets.asset",
        asset.id,
        asset.company_id,
        {"in_service_date": in_service.isoformat(), "counterpart_role": counterpart},
    )
    return asset


def change_estimate(
    uow: UnitOfWork,
    asset: Asset,
    book_ref: Any,
    *,
    useful_life_months: int | None = None,
    salvage_value: Decimal | None = None,
    method: str | None = None,
    units_total: Decimal | None = None,
    reason: str,
    effective_date: dt.date,
) -> AssetBookValue:
    """Change in accounting estimate (NIIF: prospective): past charges stay, the remaining base is re-spread."""
    if not str(reason or "").strip():
        raise ReasonRequired("a change of estimate needs a reason")
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is disposed")
    book = _book(uow, asset.company_id, book_ref)
    bv = AssetBookValue.objects.select_for_update().select_related("book").get(asset=asset, book=book)
    before = {"method": bv.method, "useful_life_months": bv.useful_life_months, "salvage": str(bv.salvage_value)}
    if useful_life_months is not None:
        if useful_life_months <= bv.periods_depreciated:
            raise invalid("the new life must exceed the periods already depreciated", "/useful_life_months", "range")
        bv.useful_life_months = useful_life_months
    if salvage_value is not None:
        if salvage_value > bv.nbv:
            raise invalid("salvage value exceeds the carrying amount", "/salvage_value", "range")
        bv.salvage_value = q_amount(salvage_value)
    if method is not None:
        if method not in Method.values:
            raise invalid("unknown method", "/method", "enum")
        bv.method = method
    if units_total is not None:
        bv.units_total = q_qty(units_total)
    if asset.lifecycle != Lifecycle.DRAFT:
        _check_fiscal_life(uow, asset, bv, effective_date)
        reanchor(bv, asset.currency)
    _save(
        bv,
        uow,
        [
            "useful_life_months",
            "salvage_value",
            "method",
            "units_total",
            "anchor_base",
            "anchor_periods",
            "anchor_units",
            "periods_since_anchor",
            "units_since_anchor",
            "status",
            "parameter_snapshot",
        ],
    )
    after = {"method": bv.method, "useful_life_months": bv.useful_life_months, "salvage": str(bv.salvage_value)}
    if asset.lifecycle != Lifecycle.DRAFT:
        register(
            uow,
            asset,
            bv,
            RegisterKind.ESTIMATE_CHANGE,
            effective_date,
            DocumentRef("assets.asset", asset.id, asset.code),
            memo=f"{before} -> {after}",
        )
    _audit(
        uow,
        "assets.asset.change_estimate",
        "assets.asset_book",
        bv.id,
        asset.company_id,
        {"book": book.code, "before": before, "after": after},
        reason,
    )
    return bv


def transfer(
    uow: UnitOfWork, asset: Asset, params: dict[str, Any], effective_date: dt.date | None, reason: str
) -> Asset:
    """Custodian / location / branch / dimension change; a dimension change reclassifies the GL per book."""
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is disposed")
    day = _date(params.get("effective_date") or effective_date or clock.business_date(), "/effective_date")
    before = {
        **{s: str(v) if v else None for s, v in asset.slots().items()},
        "location": asset.location,
        "branch_id": str(asset.branch_id) if asset.branch_id else None,
        "custodian_employee_id": str(asset.custodian_employee_id) if asset.custodian_employee_id else None,
    }
    new_slots = asset.slots()
    if "dimensions" in params:
        new_slots.update(dims_from(params["dimensions"], "/parameters/dimensions"))
    changed: list[str] = []
    old_dims, new_dims = dims_of(asset), DimensionValues(**new_slots)
    entries: list[PostingResult] = []
    record = Transfer(
        tenant_id=uow.ctx.tenant_id,
        company_id=asset.company_id,
        asset=asset,
        effective_date=day,
        reason=str(reason or "")[:2000],
        created_by=uow.ctx.principal_id,
    )
    record.save()
    if new_dims != old_dims and asset.lifecycle != Lifecycle.DRAFT:
        source = DocumentRef("assets.transfer", record.id, asset.code)
        cat = asset.category
        for bv in AssetBookValue.objects.select_for_update().filter(asset=asset).select_related("book").order_by("id"):
            lines = _nonzero(
                [
                    line(role(cat, FIXED_ASSET_COST), "DEBIT", bv.cost, new_dims, asset.code),
                    line(role(cat, FIXED_ASSET_COST), "CREDIT", bv.cost, old_dims, asset.code),
                    line(role(cat, ACCUM_DEPRECIATION), "DEBIT", bv.accumulated, old_dims, asset.code),
                    line(role(cat, ACCUM_DEPRECIATION), "CREDIT", bv.accumulated, new_dims, asset.code),
                    line(role(cat, ACCUM_IMPAIRMENT), "DEBIT", bv.impairment, old_dims, asset.code),
                    line(role(cat, ACCUM_IMPAIRMENT), "CREDIT", bv.impairment, new_dims, asset.code),
                ]
            )
            result = None
            if lines:
                result = post_to_book(
                    uow,
                    PostingRequest(
                        asset.company_id, day, source, f"transfer:{bv.book.code}", lines, JOURNAL, asset.branch_id
                    ),
                    bv.book.ledger_book_code,
                )
                entries.append(result)
            register(
                uow,
                asset,
                bv,
                RegisterKind.TRANSFER_OUT,
                day,
                source,
                cost=-bv.cost,
                accumulated=-bv.accumulated,
                impairment=-bv.impairment,
                entry=result,
                slots=asset.slots(),
            )
            register(
                uow,
                asset,
                bv,
                RegisterKind.TRANSFER_IN,
                day,
                source,
                cost=bv.cost,
                accumulated=bv.accumulated,
                impairment=bv.impairment,
                entry=result,
                slots=new_slots,
            )
    for slot, value in new_slots.items():
        if getattr(asset, slot) != value:
            setattr(asset, slot, value)
            changed.append(slot)
    for key in ("location", "custodian_employee_id", "branch_id"):
        if key in params:
            raw = params[key]
            value = raw if key == "location" else (_uuid(raw, f"/parameters/{key}") if raw else None)
            setattr(asset, key, value)
            changed.append(key)
    if not changed:
        raise invalid("the transfer changes nothing", "/parameters", "no_change")
    _save(asset, uow, changed)
    after = {
        **{s: str(v) if v else None for s, v in asset.slots().items()},
        "location": asset.location,
        "branch_id": str(asset.branch_id) if asset.branch_id else None,
        "custodian_employee_id": str(asset.custodian_employee_id) if asset.custodian_employee_id else None,
    }
    Transfer.objects.filter(id=record.id).update(before=before, after=after, journal_entry_ids=entry_ids(*entries))
    _audit(
        uow,
        "assets.asset.transfer",
        "assets.asset",
        asset.id,
        asset.company_id,
        {"fields": changed, "journal_entry_ids": entry_ids(*entries)},
        reason,
    )
    return asset


def dispose(
    uow: UnitOfWork, asset: Asset, params: dict[str, Any], effective_date: dt.date | None, reason: str
) -> Disposal:
    """Sale / scrap / donation: derecognition per book with gain or loss (shared/07 §5)."""
    if not str(reason or "").strip():
        raise ReasonRequired("a disposal needs a reason")
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is already disposed")
    if asset.lifecycle == Lifecycle.DRAFT:
        raise InvalidStateTransition("a draft asset is deleted, not disposed")
    if asset.components.exclude(lifecycle=Lifecycle.DISPOSED).exists():
        raise invalid("dispose the active components first (partial disposal)", "/parameters", "components_active")
    kind = str(params.get("kind") or DisposalKind.SCRAP).upper()
    if kind not in DisposalKind.values:
        raise invalid("kind must be SALE, SCRAP or DONATION", "/parameters/kind", "enum")
    day = _date(params.get("date") or effective_date or clock.business_date(), "/effective_date")
    proceeds = q_amount(_dec(params.get("proceeds", 0), "/parameters/proceeds")) if kind == DisposalKind.SALE else ZERO
    vat = q_amount(_dec(params.get("vat", 0), "/parameters/vat")) if kind == DisposalKind.SALE else ZERO
    customer = params.get("customer_id")
    if kind == DisposalKind.SALE and not customer:
        raise invalid("a sale needs the customer", "/parameters/customer_id", "required")
    disposal = Disposal.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=asset.company_id,
        asset=asset,
        kind=kind,
        disposal_date=day,
        proceeds=proceeds,
        vat=vat,
        customer_id=_uuid(customer, "/parameters/customer_id") if customer else None,
        reason=str(reason)[:2000],
        created_by=uow.ctx.principal_id,
    )
    source = DocumentRef("assets.disposal", disposal.id, asset.code)
    cat, dims = asset.category, dims_of(asset)
    results: dict[str, Any] = {}
    for bv in AssetBookValue.objects.select_for_update().filter(asset=asset).select_related("book").order_by("id"):
        nbv = bv.nbv
        gain = proceeds - nbv
        lines = [
            line(role(cat, ACCUM_DEPRECIATION), "DEBIT", bv.accumulated, dims, asset.code),
            line(role(cat, ACCUM_IMPAIRMENT), "DEBIT", bv.impairment, dims, asset.code),
            line(role(cat, ASSET_DISPOSAL_PROCEEDS), "DEBIT", proceeds, dims, asset.code),
            line(role(cat, FIXED_ASSET_COST), "CREDIT", bv.cost, dims, asset.code),
        ]
        if gain > ZERO:
            lines.append(line(role(cat, GAIN_ON_DISPOSAL), "CREDIT", gain, dims, asset.code))
        elif gain < ZERO:
            lines.append(line(role(cat, LOSS_ON_DISPOSAL), "DEBIT", -gain, dims, asset.code))
        if bv.surplus > ZERO:  # NIIF: surplus transferred directly to retained earnings on derecognition
            lines.append(line(role(cat, REVALUATION_SURPLUS), "DEBIT", bv.surplus, dims, asset.code))
            lines.append(line(RETAINED_EARNINGS, "CREDIT", bv.surplus, dims, asset.code))
        posted = _nonzero(lines)
        result = None
        if posted:
            result = post_to_book(
                uow,
                PostingRequest(
                    asset.company_id, day, source, f"disposal:{bv.book.code}", posted, JOURNAL, asset.branch_id
                ),
                bv.book.ledger_book_code,
            )
        register(
            uow,
            asset,
            bv,
            RegisterKind.DISPOSAL,
            day,
            source,
            cost=-bv.cost,
            accumulated=-bv.accumulated,
            impairment=-bv.impairment,
            surplus=-bv.surplus,
            entry=result,
            memo=kind,
        )
        results[bv.book.code] = {
            "nbv": str(q_amount(nbv)),
            "gain": str(q_amount(gain)),
            "journal_entry_id": str(result.entry_id) if result is not None else None,
        }
        bv.status = BookValueStatus.DISPOSED
        _save(bv, uow, ["status"])
    invoicing = _invoicing()
    if kind == DisposalKind.SALE and invoicing is not None and customer:
        from assets.interface import DisposalInvoiceRequest

        ref = invoicing.create_disposal_invoice(
            uow,
            DisposalInvoiceRequest(
                company_id=asset.company_id,
                customer=PartyRef(_uuid(customer, "/parameters/customer_id"), "customer"),
                date=day,
                description=f"Venta de activo {asset.code} — {asset.name}",
                net=proceeds,
                vat=vat,
                vat_tax_code=params.get("vat_tax_code"),
                source=source,
            ),
        )
        disposal.invoice_type, disposal.invoice_id, disposal.invoice_number = ref.type, ref.id, ref.number or ""
    disposal.results = results
    disposal.save(update_fields=["results", "invoice_type", "invoice_id", "invoice_number"])
    asset.lifecycle = Lifecycle.DISPOSED
    asset.disposed_on = day
    _save(asset, uow, ["lifecycle", "disposed_on"])
    uow.emit(
        DISPOSED_EVENT,
        {"asset_id": str(asset.id), "proceeds": {"amount": f"{proceeds:.4f}", "currency": "COP"}},
        company_id=asset.company_id,
    )
    _audit(
        uow,
        "assets.asset.dispose",
        "assets.asset",
        asset.id,
        asset.company_id,
        {"kind": kind, "proceeds": str(proceeds), "results": results},
        reason,
    )
    return disposal


def _invoicing() -> Any:
    from assets.interface import DisposalInvoicing

    return services.optional(DisposalInvoicing)


# =========================================================================== capitalization (AssetService)


def threshold(uow: UnitOfWork, category: AssetCategory, on_date: dt.date) -> Decimal | None:
    limits: list[Decimal] = []
    if category.capitalization_threshold is not None:
        limits.append(category.capitalization_threshold)
    if category.capitalization_threshold_uvt is not None:
        provider = services.get(ParameterProvider)
        uvt = enforce(provider.get("uvt", on_date, category.company_id), "uvt", production=is_production())
        limits.append(q_amount(_dec(uvt.value, "/uvt") * category.capitalization_threshold_uvt))
    return max(limits) if limits else None


class DbAssetService:
    """Implements :class:`assets.interface.AssetService` on the register tables."""

    def should_capitalize(
        self, uow: UnitOfWork, company_id: uuid.UUID, category_id: uuid.UUID, cost: Decimal, on_date: dt.date
    ) -> bool:
        category = AssetCategory.objects.get(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=category_id)
        limit = threshold(uow, category, on_date)
        return limit is None or cost >= limit

    def _create(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        category_id: uuid.UUID,
        code: str | None,
        name: str,
        cost: Decimal,
        day: dt.date,
        extra: dict[str, Any],
    ) -> Asset:
        if not self.should_capitalize(uow, company_id, category_id, cost, day):
            raise invalid(
                "the cost is below the category capitalization threshold", "/cost", "below_capitalization_threshold"
            )
        return create_asset(
            uow,
            company_id,
            {
                "code": code,
                "name": name,
                "category_id": str(category_id),
                "acquisition_cost": str(cost),
                "acquisition_date": day.isoformat(),
                **extra,
            },
        )

    def _acquired(
        self,
        uow: UnitOfWork,
        asset: Asset,
        source: DocumentRef,
        day: dt.date,
        entry: PostingResult | None,
        in_service: dt.date | None,
    ) -> None:
        for bv in AssetBookValue.objects.select_for_update().filter(asset=asset).select_related("book"):
            bv.cost = asset.acquisition_cost
            _save(bv, uow, ["cost"])
            register(uow, asset, bv, RegisterKind.ACQUISITION, day, source, cost=bv.cost, entry=entry)
        if in_service is not None:
            activate(uow, asset, {"in_service_date": in_service.isoformat()}, in_service)

    def capitalize_from_invoice_line(self, uow: UnitOfWork, data: Any) -> Any:
        from assets.interface import CapitalizedAsset

        slots = {s: getattr(data.dimensions, s) for s in SLOTS}
        asset = self._create(
            uow,
            data.company_id,
            data.category_id,
            data.code,
            data.description,
            q_amount(data.cost),
            data.acquisition_date,
            {
                "serial": data.serial,
                "location": data.location,
                "branch_id": data.branch_id,
                "supplier_id": str(data.supplier.id) if data.supplier else None,
                "source_type": data.invoice.type,
                "source_id": str(data.invoice.id),
                "source_number": data.invoice.number or "",
                "source_line_no": data.line_no,
                "dimensions": slots,
            },
        )
        self._acquired(uow, asset, data.invoice, data.acquisition_date, None, data.in_service_date)
        return CapitalizedAsset(asset.id, asset.code, role(asset.category, FIXED_ASSET_COST))

    def capitalize_from_inventory(self, uow: UnitOfWork, data: Any) -> Any:
        from assets.interface import CapitalizedAsset

        if data.credit_role not in {"INVENTORY", "CIP"}:
            raise invalid("credit_role must be INVENTORY or CIP", "/credit_role", "enum")
        slots = {s: getattr(data.dimensions, s) for s in SLOTS}
        asset = self._create(
            uow,
            data.company_id,
            data.category_id,
            data.code,
            data.description,
            q_amount(data.cost),
            data.date,
            {
                "location": data.location,
                "branch_id": data.branch_id,
                "source_type": data.source.type,
                "source_id": str(data.source.id),
                "source_number": data.source.number or "",
                "dimensions": slots,
            },
        )
        dims = dims_of(asset)
        entry = post_all_books(
            uow,
            PostingRequest(
                company_id=asset.company_id,
                posting_date=data.date,
                source=data.source,
                purpose=f"capitalization:{asset.id}",
                journal_code=JOURNAL,
                branch_id=asset.branch_id,
                lines=(
                    line(role(asset.category, FIXED_ASSET_COST), "DEBIT", asset.acquisition_cost, dims, asset.code),
                    line(
                        data.credit_role,
                        "CREDIT",
                        asset.acquisition_cost,
                        dims,
                        asset.code,
                        item_id=data.item_id,
                        warehouse_id=data.warehouse_id,
                    ),
                ),
            ),
        )
        self._acquired(uow, asset, data.source, data.date, entry, data.in_service_date)
        return CapitalizedAsset(asset.id, asset.code, role(asset.category, FIXED_ASSET_COST))


# =========================================================================== depreciation runs


@dataclass
class Computed:
    bv: AssetBookValue
    amount: Decimal
    units: Decimal | None
    after: engine.BookState


@dataclass
class RunPlan:
    run: DepreciationRun
    items: list[Computed] = field(default_factory=list)
    request: PostingRequest | None = None

    @property
    def total(self) -> Decimal:
        return sum((c.amount for c in self.items), ZERO)


def _units_input(lines: list[dict[str, Any]]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for i, ln in enumerate(lines or []):
        asset_id = ln.get("item_id")
        qty = ln.get("quantity")
        if asset_id and qty is not None:
            out[str(_uuid(asset_id, f"/lines/{i}/item_id"))] = _dec(qty, f"/lines/{i}/quantity")
    return out


def create_run(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> DepreciationRun:
    ctx = uow.ctx
    ctx.require_company(company_id)
    book = _book(uow, company_id, _uuid(data.get("book_id"), "/book_id"))
    period = period_row(uow, company_id, period_id=_uuid(data.get("period_id"), "/period_id"))
    if period.number > 12:
        raise invalid("depreciation runs use monthly periods (1–12)", "/period_id", "period_not_monthly")
    exists = DepreciationRun.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, book=book, period_id=period.id
    ).exclude(status=DocStatus.CANCELLED)
    if exists.exists():
        raise DepreciationRunExists(f"book {book.code} already has a depreciation run for {period.code}")
    lines = list(data.get("lines") or [])
    _units_input(lines)
    doc_date = _date(data.get("document_date") or period.end, "/document_date")
    try:
        with transaction.atomic(using=uow.using):
            run = DepreciationRun.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                book=book,
                period_id=period.id,
                period_start=period.start,
                period_end=period.end,
                document_date=doc_date,
                posting_date=_date(data["posting_date"], "/posting_date") if data.get("posting_date") else None,
                number=next_number(uow, company_id, f"DEP-{book.code}"[:40], doc_date),
                notes=str(data.get("notes") or ""),
                lines=lines,
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
            )
    except IntegrityError:
        raise DepreciationRunExists(f"book {book.code} already has a depreciation run for {period.code}") from None
    _audit(
        uow,
        "assets.depreciation_run.create",
        "assets.depreciation_run",
        run.id,
        company_id,
        {"book": book.code, "period": period.code},
    )
    return run


def update_run(uow: UnitOfWork, run: DepreciationRun, data: dict[str, Any]) -> DepreciationRun:
    if run.status != DocStatus.DRAFT:
        raise DocumentImmutable("a posted depreciation run cannot change")
    fields: list[str] = []
    if "lines" in data:
        _units_input(list(data["lines"] or []))
        run.lines = list(data["lines"] or [])
        fields.append("lines")
    if "notes" in data:
        run.notes = str(data["notes"] or "")
        fields.append("notes")
    if "posting_date" in data:
        run.posting_date = _date(data["posting_date"], "/posting_date") if data["posting_date"] else None
        fields.append("posting_date")
    if "document_date" in data:
        run.document_date = _date(data["document_date"], "/document_date")
        fields.append("document_date")
    if "period_id" in data or "book_id" in data:
        raise invalid("create a new run to change the book or the period", "/period_id", "immutable")
    if fields:
        _save(run, uow, fields)
    return run


def delete_run(uow: UnitOfWork, run: DepreciationRun) -> None:
    if run.status != DocStatus.DRAFT:
        raise DocumentImmutable("a posted depreciation run cannot be deleted")
    run.delete()


def _eligible(uow: UnitOfWork, run: DepreciationRun, lock: bool) -> list[AssetBookValue]:
    qs = AssetBookValue.objects.filter(
        tenant_id=uow.ctx.tenant_id,
        company_id=run.company_id,
        book=run.book,
        status=BookValueStatus.ACTIVE,
        asset__lifecycle__in=[Lifecycle.ACTIVE, Lifecycle.FULLY_DEPRECIATED],
        depreciation_start__lte=run.period_start,
    ).exclude(last_period_end__gte=run.period_start)
    if lock:
        qs = qs.select_for_update(of=("self",))
    return list(qs.select_related("asset", "asset__category", "book").order_by("asset__code", "id"))


def compute_run(uow: UnitOfWork, run: DepreciationRun, *, lock: bool = False) -> RunPlan:
    plan = RunPlan(run=run)
    units = _units_input(run.lines)
    for bv in _eligible(uow, run, lock):
        st = state_of(bv, bv.asset.currency)
        u = units.get(str(bv.asset_id), ZERO) if bv.method == Method.UNITS else None
        try:
            amount = engine.charge(st, u)
        except engine.EngineError as exc:
            raise invalid(f"{bv.asset.code}: {exc}", f"/lines/{bv.asset.code}", exc.reason) from None
        if amount == ZERO:
            continue
        plan.items.append(Computed(bv, amount, u, engine.apply(st, amount, u)))
    groups: dict[tuple[Any, ...], Decimal] = defaultdict(lambda: ZERO)
    for c in plan.items:
        cat = c.bv.asset.category
        key = (role(cat, DEPRECIATION_EXPENSE), role(cat, ACCUM_DEPRECIATION), *(getattr(c.bv.asset, s) for s in SLOTS))
        groups[key] += c.amount
    lines: list[PostingLine] = []
    for key in sorted(groups, key=lambda k: tuple(str(x) for x in k)):
        expense, accum, *slot_values = key
        dims = DimensionValues(**dict(zip(SLOTS, slot_values, strict=True)))
        lines.append(line(expense, "DEBIT", groups[key], dims, run.number))
        lines.append(line(accum, "CREDIT", groups[key], dims, run.number))
    if lines:
        plan.request = PostingRequest(
            company_id=run.company_id,
            posting_date=run.posting_date or run.period_end,
            source=DocumentRef("assets.depreciation_run", run.id, run.number),
            purpose=f"depreciation:{run.book.code}",
            lines=tuple(lines),
            journal_code=JOURNAL,
        )
    return plan


def simulation(
    lines: list[SimulatedLine], request: PostingRequest | None, documents: list[dict[str, Any]], explain: list[str]
) -> dict[str, Any]:
    entries = []
    if request is not None:
        entries.append(
            {
                "journal": request.journal_code,
                "posting_date": request.posting_date.isoformat(),
                "lines": [
                    {
                        "account": ln.account,
                        "debit": {"amount": f"{ln.debit:.4f}", "currency": "COP"},
                        "credit": {"amount": f"{ln.credit:.4f}", "currency": "COP"},
                        "dimensions": {k: str(v) if v else None for k, v in ln.slots.items()},
                    }
                    for ln in lines
                ],
            }
        )
    return {
        "persisted": False,
        "documents": documents,
        "journal_entries": entries,
        "stock_moves": [],
        "explain": explain,
    }


def preview_run(uow: UnitOfWork, run: DepreciationRun) -> dict[str, Any]:
    period_row(uow, run.company_id, period_id=run.period_id)
    plan = compute_run(uow, run)
    lines = simulate_to_book(uow, plan.request, run.book.ledger_book_code) if plan.request else []
    docs = [
        {
            "asset_id": str(c.bv.asset_id),
            "code": c.bv.asset.code,
            "amount": f"{c.amount:.4f}",
            "accumulated_after": f"{c.after.accumulated:.4f}",
            "nbv_after": f"{c.after.nbv:.4f}",
        }
        for c in plan.items
    ]
    explain = [
        f"Libro {run.book.code}, periodo {run.period_start:%Y-%m}: {len(plan.items)} activos, "
        f"total {plan.total:.2f} COP; un asiento resumido por cuenta y dimensión."
    ]
    return simulation(lines, plan.request, docs, explain)


def post_run(uow: UnitOfWork, run_id: uuid.UUID, company_id: uuid.UUID) -> tuple[DepreciationRun, bool]:
    """Post a run: one summarized entry for its book + per-asset detail. Returns (run, replayed)."""
    run = (
        DepreciationRun.objects.select_for_update()
        .select_related("book")
        .filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=run_id)
        .first()
    )
    if run is None:
        raise NotFound("depreciation run not found")
    if run.status == DocStatus.POSTED:
        return run, True  # idempotent per company + book + period: posting twice keeps one entry
    if run.status != DocStatus.DRAFT:
        raise InvalidStateTransition(f"a {run.status} run cannot be posted")
    period_row(uow, company_id, period_id=run.period_id)
    plan = compute_run(uow, run, lock=True)
    result = post_to_book(uow, plan.request, run.book.ledger_book_code) if plan.request else None
    for i, c in enumerate(plan.items, start=1):
        bv, asset = c.bv, c.bv.asset
        DepreciationLine.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company_id,
            run=run,
            asset=asset,
            line_no=i,
            amount=c.amount,
            units=c.units,
            accumulated_after=c.after.accumulated,
            nbv_after=c.after.nbv,
            expense_role=role(asset.category, DEPRECIATION_EXPENSE),
            accumulated_role=role(asset.category, ACCUM_DEPRECIATION),
            **asset.slots(),
        )
        register(
            uow,
            asset,
            bv,
            RegisterKind.DEPRECIATION,
            run.posting_date or run.period_end,
            DocumentRef("assets.depreciation_run", run.id, run.number),
            accumulated=c.amount,
            units=c.units,
            entry=result,
        )
        bv.accumulated = c.after.accumulated
        bv.periods_depreciated += 1
        bv.periods_since_anchor = c.after.periods_since_anchor
        bv.units_since_anchor = c.after.units_since_anchor
        bv.units_used = bv.units_used + (c.units or ZERO)
        bv.last_period_end = run.period_end
        if c.after.exhausted:
            bv.status = BookValueStatus.FULLY_DEPRECIATED
        _save(
            bv,
            uow,
            [
                "accumulated",
                "periods_depreciated",
                "periods_since_anchor",
                "units_since_anchor",
                "units_used",
                "last_period_end",
                "status",
            ],
        )
        if c.after.exhausted and not AssetBookValue.objects.filter(asset=asset, status=BookValueStatus.ACTIVE).exists():
            asset.lifecycle = Lifecycle.FULLY_DEPRECIATED
            _save(asset, uow, ["lifecycle"])
    run.status = DocStatus.POSTED
    run.total = q_amount(plan.total)
    run.posting_date = run.posting_date or run.period_end
    run.journal_entry_id = result.entry_id if result is not None else None
    run.journal_entry_ids = entry_ids(result) if result is not None else []
    run.posted_by = uow.ctx.principal_id
    run.posted_at = clock.now()
    _save(
        run, uow, ["status", "total", "posting_date", "journal_entry_id", "journal_entry_ids", "posted_by", "posted_at"]
    )
    if result is not None:
        uow.emit(
            RUN_EVENT,
            {
                "run_id": str(run.id),
                "period_id": str(run.period_id),
                "total": {"amount": f"{run.total:.4f}", "currency": "COP"},
                "journal_entry_id": str(result.entry_id),
            },
            company_id=company_id,
        )
    _audit(
        uow,
        "assets.depreciation_run.post",
        "assets.depreciation_run",
        run.id,
        company_id,
        {"book": run.book.code, "total": str(run.total), "assets": len(plan.items)},
    )
    return run, False


# =========================================================================== revaluation / impairment


def _document_amount(lines: list[dict[str, Any]], pointer: str) -> Decimal:
    if not lines:
        raise invalid("at least one line with the amount is required", "/lines", "required")
    total = ZERO
    for i, ln in enumerate(lines):
        total += _dec(ln.get("amount"), f"/lines/{i}/amount")
    return q_amount(total)


def _niif_values(asset: Asset) -> list[AssetBookValue]:
    bvs = list(
        AssetBookValue.objects.select_for_update()
        .filter(asset=asset, book__framework=Framework.NIIF)
        .exclude(status__in=[BookValueStatus.DISPOSED, BookValueStatus.PENDING])
        .select_related("book")
        .order_by("id")
    )
    if not bvs:
        raise invalid("the asset has no active NIIF book value", "/asset_id", "no_niif_book")
    return bvs


def create_valuation(
    uow: UnitOfWork, model: type[Revaluation] | type[Impairment], company_id: uuid.UUID, data: dict[str, Any]
) -> Revaluation | Impairment:
    ctx = uow.ctx
    asset = get_asset(uow, company_id, data.get("asset_id"))
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is disposed")
    lines = list(data.get("lines") or [])
    amount = _document_amount(lines, "/lines")
    doc_date = _date(data.get("document_date"), "/document_date")
    prefix = "REV" if model is Revaluation else "DET"
    extra = {"fair_value": amount} if model is Revaluation else {"recoverable_amount": amount}
    obj = model.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        asset=asset,
        document_date=doc_date,
        posting_date=_date(data["posting_date"], "/posting_date") if data.get("posting_date") else None,
        number=next_number(uow, company_id, prefix, doc_date),
        notes=str(data.get("notes") or ""),
        lines=lines,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        **extra,
    )
    return obj


def update_valuation(uow: UnitOfWork, obj: Revaluation | Impairment, data: dict[str, Any]) -> Revaluation | Impairment:
    if obj.status != DocStatus.DRAFT:
        raise DocumentImmutable("a posted document cannot change")
    fields: list[str] = []
    if "lines" in data:
        amount = _document_amount(list(data["lines"] or []), "/lines")
        obj.lines = list(data["lines"] or [])
        fields.append("lines")
        if isinstance(obj, Revaluation):
            obj.fair_value = amount
            fields.append("fair_value")
        else:
            obj.recoverable_amount = amount
            fields.append("recoverable_amount")
    for key in ("document_date", "posting_date"):
        if key in data:
            setattr(obj, key, _date(data[key], f"/{key}") if data[key] else None)
            fields.append(key)
    if "notes" in data:
        obj.notes = str(data["notes"] or "")
        fields.append("notes")
    if "asset_id" in data and str(data["asset_id"]) != str(obj.asset_id):
        raise invalid("create a new document for another asset", "/asset_id", "immutable")
    if fields:
        _save(obj, uow, fields)
    return obj


def delete_valuation(uow: UnitOfWork, obj: Revaluation | Impairment) -> None:
    if obj.status != DocStatus.DRAFT:
        raise DocumentImmutable("a posted document cannot be deleted")
    obj.delete()


def _revaluation_lines(asset: Asset, bv: AssetBookValue, fair: Decimal) -> tuple[list[PostingLine], dict[str, Decimal]]:
    cat, dims, memo = asset.category, dims_of(asset), asset.code
    nbv = bv.nbv
    diff = fair - nbv
    cost_change = fair - bv.cost  # elimination method: accumulated depreciation/impairment netted against cost
    lines = [
        line(role(cat, ACCUM_DEPRECIATION), "DEBIT", bv.accumulated, dims, memo),
        line(role(cat, ACCUM_IMPAIRMENT), "DEBIT", bv.impairment, dims, memo),
    ]
    if cost_change > ZERO:
        lines.append(line(role(cat, FIXED_ASSET_COST), "DEBIT", cost_change, dims, memo))
    elif cost_change < ZERO:
        lines.append(line(role(cat, FIXED_ASSET_COST), "CREDIT", -cost_change, dims, memo))
    surplus_delta = ZERO
    if diff > ZERO:
        lines.append(line(role(cat, REVALUATION_SURPLUS), "CREDIT", diff, dims, memo))
        surplus_delta = diff
    elif diff < ZERO:
        from_surplus = min(-diff, bv.surplus)
        lines.append(line(role(cat, REVALUATION_SURPLUS), "DEBIT", from_surplus, dims, memo))
        lines.append(line(role(cat, REVALUATION_LOSS), "DEBIT", -diff - from_surplus, dims, memo))
        surplus_delta = -from_surplus
    deltas = {
        "cost": cost_change,
        "accumulated": -bv.accumulated,
        "impairment": -bv.impairment,
        "surplus": surplus_delta,
        "diff": diff,
    }
    return lines, deltas


def _impairment_lines(asset: Asset, bv: AssetBookValue, recoverable: Decimal) -> tuple[list[PostingLine], Decimal]:
    cat, dims, memo = asset.category, dims_of(asset), asset.code
    loss = bv.nbv - recoverable
    if loss > ZERO:
        return [
            line(role(cat, IMPAIRMENT_EXPENSE), "DEBIT", loss, dims, memo),
            line(role(cat, ACCUM_IMPAIRMENT), "CREDIT", loss, dims, memo),
        ], loss
    reversal = min(-loss, bv.impairment)  # reversal capped at the accumulated impairment
    return [
        line(role(cat, ACCUM_IMPAIRMENT), "DEBIT", reversal, dims, memo),
        line(role(cat, IMPAIRMENT_EXPENSE), "CREDIT", reversal, dims, memo),
    ], -reversal


def _valuation_request(
    obj: Revaluation | Impairment, bv: AssetBookValue, lines: list[PostingLine]
) -> PostingRequest | None:
    posted = _nonzero(lines)
    if not posted:
        return None
    kind = "revaluation" if isinstance(obj, Revaluation) else "impairment"
    return PostingRequest(
        obj.company_id,
        obj.posting_date or obj.document_date,
        DocumentRef(f"assets.{kind}", obj.id, obj.number),
        f"{kind}:{bv.book.code}",
        posted,
        JOURNAL,
        obj.asset.branch_id,
    )


def simulate_valuation(uow: UnitOfWork, obj: Revaluation | Impairment) -> dict[str, Any]:
    asset = obj.asset
    entries: list[dict[str, Any]] = []
    explain: list[str] = []
    day = obj.posting_date or obj.document_date
    period_row(uow, obj.company_id, day=day)
    for bv in _niif_values(asset):
        if isinstance(obj, Revaluation):
            lines, deltas = _revaluation_lines(asset, bv, obj.fair_value)
            explain.append(
                f"{bv.book.code}: valor razonable {obj.fair_value:.2f} vs VNL {bv.nbv:.2f} → "
                f"diferencia {deltas['diff']:.2f}"
            )
        else:
            lines, change = _impairment_lines(asset, bv, obj.recoverable_amount)
            explain.append(
                f"{bv.book.code}: importe recuperable {obj.recoverable_amount:.2f} vs VNL {bv.nbv:.2f} "
                f"→ deterioro {change:.2f}"
            )
        request = _valuation_request(obj, bv, lines)
        if request is not None:
            sim = simulate_to_book(uow, request, bv.book.ledger_book_code)
            entries.extend(simulation(sim, request, [], [])["journal_entries"])
    return {"persisted": False, "documents": [], "journal_entries": entries, "stock_moves": [], "explain": explain}


def post_valuation(
    uow: UnitOfWork, model: type[Revaluation] | type[Impairment], company_id: uuid.UUID, obj_id: uuid.UUID
) -> Revaluation | Impairment:
    obj = (
        model.objects.select_for_update().filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=obj_id).first()
    )
    if obj is None:
        raise NotFound("document not found")
    if obj.status == DocStatus.POSTED:
        return obj
    if obj.status != DocStatus.DRAFT:
        raise InvalidStateTransition(f"a {obj.status} document cannot be posted")
    asset = get_asset(uow, company_id, obj.asset_id, lock=True)
    if asset.lifecycle == Lifecycle.DISPOSED:
        raise AssetAlreadyDisposed("the asset is disposed")
    if asset.lifecycle == Lifecycle.DRAFT:
        raise InvalidStateTransition("the asset is not in service")
    day = obj.posting_date or obj.document_date
    period_row(uow, company_id, day=day)
    results: list[PostingResult] = []
    total = ZERO
    source = DocumentRef(
        f"assets.{'revaluation' if isinstance(obj, Revaluation) else 'impairment'}", obj.id, obj.number
    )
    for bv in _niif_values(asset):
        if isinstance(obj, Revaluation):
            lines, deltas = _revaluation_lines(asset, bv, obj.fair_value)
            request = _valuation_request(obj, bv, lines)
            result = post_to_book(uow, request, bv.book.ledger_book_code) if request else None
            register(
                uow,
                asset,
                bv,
                RegisterKind.REVALUATION,
                day,
                source,
                cost=deltas["cost"],
                accumulated=deltas["accumulated"],
                impairment=deltas["impairment"],
                surplus=deltas["surplus"],
                entry=result,
            )
            bv.cost, bv.accumulated, bv.impairment = obj.fair_value, ZERO, ZERO
            bv.surplus = bv.surplus + deltas["surplus"]
            total += deltas["diff"]
        else:
            lines, change = _impairment_lines(asset, bv, obj.recoverable_amount)
            if change == ZERO:
                raise invalid("the recoverable amount does not change the carrying amount", "/lines", "no_change")
            request = _valuation_request(obj, bv, lines)
            result = post_to_book(uow, request, bv.book.ledger_book_code) if request else None
            register(uow, asset, bv, RegisterKind.IMPAIRMENT, day, source, impairment=change, entry=result)
            bv.impairment = bv.impairment + change
            total += change
        if result is not None:
            results.append(result)
        reanchor(bv, asset.currency)
        _save(
            bv,
            uow,
            [
                "cost",
                "accumulated",
                "impairment",
                "surplus",
                "anchor_base",
                "anchor_periods",
                "anchor_units",
                "periods_since_anchor",
                "units_since_anchor",
                "status",
            ],
        )
    if (
        asset.lifecycle == Lifecycle.FULLY_DEPRECIATED
        and AssetBookValue.objects.filter(asset=asset, status=BookValueStatus.ACTIVE).exists()
    ):
        asset.lifecycle = Lifecycle.ACTIVE
        _save(asset, uow, ["lifecycle"])
    obj.status = DocStatus.POSTED
    obj.total = q_amount(total)
    obj.posting_date = day
    obj.journal_entry_ids = entry_ids(*results)
    obj.posted_by = uow.ctx.principal_id
    obj.posted_at = clock.now()
    _save(obj, uow, ["status", "total", "posting_date", "journal_entry_ids", "posted_by", "posted_at"])
    kind = "revaluation" if isinstance(obj, Revaluation) else "impairment"
    _audit(
        uow,
        f"assets.{kind}.post",
        f"assets.{kind}",
        obj.id,
        company_id,
        {"asset": asset.code, "total": str(obj.total), "entries": obj.journal_entry_ids},
    )
    return obj


# =========================================================================== physical counts


def create_count(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> PhysicalCount:
    ctx = uow.ctx
    ctx.require_company(company_id)
    doc_date = _date(data.get("document_date"), "/document_date")
    return PhysicalCount.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        document_date=doc_date,
        number=next_number(uow, company_id, "INVAF", doc_date),
        notes=str(data.get("notes") or ""),
        lines=list(data.get("lines") or []),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )


def update_count(uow: UnitOfWork, count: PhysicalCount, data: dict[str, Any]) -> PhysicalCount:
    if count.status != DocStatus.DRAFT:
        raise DocumentImmutable("a submitted count cannot change")
    fields: list[str] = []
    if "lines" in data:
        count.lines = list(data["lines"] or [])
        fields.append("lines")
    if "notes" in data:
        count.notes = str(data["notes"] or "")
        fields.append("notes")
    if "document_date" in data:
        count.document_date = _date(data["document_date"], "/document_date")
        fields.append("document_date")
    if fields:
        _save(count, uow, fields)
    return count


def delete_count(uow: UnitOfWork, count: PhysicalCount) -> None:
    if count.status != DocStatus.DRAFT:
        raise DocumentImmutable("a submitted count cannot be deleted")
    count.delete()


def submit_count(uow: UnitOfWork, count: PhysicalCount) -> PhysicalCount:
    """Reconcile the scanned tags against the register: found, missing (quantity 0) and unknown tags."""
    if count.status != DocStatus.DRAFT:
        raise InvalidStateTransition("only a draft count can be submitted")
    expected = {
        a.code: a
        for a in Asset.objects.filter(
            tenant_id=uow.ctx.tenant_id,
            company_id=count.company_id,
            lifecycle__in=[Lifecycle.ACTIVE, Lifecycle.FULLY_DEPRECIATED],
        )
    }
    scanned: dict[str, Decimal] = {}
    for i, ln in enumerate(count.lines):
        tag = str(ln.get("description") or "").strip()
        if not tag and ln.get("item_id"):
            hit = Asset.objects.filter(
                tenant_id=uow.ctx.tenant_id, company_id=count.company_id, id=_uuid(ln["item_id"], f"/lines/{i}/item_id")
            ).first()
            tag = hit.code if hit else ""
        if not tag:
            raise invalid("each line needs the scanned tag in description", f"/lines/{i}/description", "required")
        qty = (
            _dec((ln.get("quantity") or {}).get("value", "1"), f"/lines/{i}/quantity")
            if isinstance(ln.get("quantity"), dict)
            else Decimal(1)
        )
        scanned[tag] = scanned.get(tag, ZERO) + qty
    lines: list[dict[str, Any]] = []
    found, unknown, duplicates = [], [], []
    for n, (tag, qty) in enumerate(sorted(scanned.items()), start=1):
        asset = expected.get(tag)
        if asset is None:
            unknown.append(tag)
        else:
            found.append(tag)
            if qty > 1:
                duplicates.append(tag)
        lines.append(
            {
                "line_no": n,
                "item_id": str(asset.id) if asset else None,
                "description": tag,
                "quantity": {"value": f"{qty:.6f}", "uom": "UND"},
            }
        )
    missing = sorted(set(expected) - set(scanned))
    for tag in missing:
        lines.append(
            {
                "line_no": len(lines) + 1,
                "item_id": str(expected[tag].id),
                "description": tag,
                "quantity": {"value": "0.000000", "uom": "UND"},
            }
        )
    count.lines = lines
    count.discrepancies = {"found": found, "missing": missing, "unknown": unknown, "duplicates": duplicates}
    count.status = DocStatus.SUBMITTED
    count.submitted_by = uow.ctx.principal_id
    _save(count, uow, ["lines", "discrepancies", "status", "submitted_by"])
    _audit(
        uow,
        "assets.physical_count.submit",
        "assets.physical_count",
        count.id,
        count.company_id,
        {"missing": len(missing), "unknown": len(unknown)},
    )
    return count


def approve_count(uow: UnitOfWork, count: PhysicalCount, reason: str = "") -> PhysicalCount:
    if count.status != DocStatus.SUBMITTED:
        raise InvalidStateTransition("only a submitted count can be approved")
    if count.submitted_by == uow.ctx.principal_id:
        raise SodViolation("the person who submitted the count cannot approve it")
    count.status = DocStatus.APPROVED
    count.approved_by = uow.ctx.principal_id
    _save(count, uow, ["status", "approved_by"])
    _audit(
        uow,
        "assets.physical_count.approve",
        "assets.physical_count",
        count.id,
        count.company_id,
        dict(count.discrepancies),
        reason,
    )
    return count
