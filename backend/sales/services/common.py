"""Shared helpers of the sales services: policy, numbering, settings, audit, FX, input parsing."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.common import DimensionValues
from kernel.interfaces.policy import PolicyService
from kernel.models import Sequence
from kernel.money import MoneyError, to_decimal, validate_currency
from kernel.sequences import allocate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import NumberingRangeMissing
from sales.models import SalesSettings

#: Non-fiscal kernel.sequences codes (gap-free per company), created on first use.
SEQUENCES = {
    "quote": ("SAL-QUOTE", "COT-"),
    "order": ("SAL-ORDER", "PV-"),
    "delivery": ("SAL-DELIVERY", "REM-"),
    "receipt": ("SAL-RECEIPT", "RCA-"),
    "write_off": ("SAL-WRITEOFF", "CAS-"),
    "return": ("SAL-RETURN", "DEV-"),
}
#: Fiscal sequences (DIAN numbering resolution = external tenant data, never created here).
FISCAL_SEQUENCES = {"invoice": "FV", "credit_note": "NC", "debit_note": "ND"}

#: Account roles of the sales postings (shared/07 §1); role → account determination is tenant data.
ROLE_AR = "AR"
ROLE_REVENUE = "REVENUE"
ROLE_VAT_PAYABLE = "VAT_PAYABLE"
ROLE_COGS = "COGS"
ROLE_INVENTORY = "INVENTORY"
ROLE_SALES_DISCOUNTS = "SALES_DISCOUNTS"
ROLE_WHT_RECEIVABLE = "WHT_RECEIVABLE"
ROLE_BANK = "BANK"
ROLE_BAD_DEBT = "BAD_DEBT_EXPENSE"
ROLE_FX_GAIN = "FX_REALIZED_GAIN"
ROLE_FX_LOSS = "FX_REALIZED_LOSS"


def policy() -> PolicyService:
    return services.get(PolicyService)


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    """Deny by default through P04's PolicyService (same check for UI, API, export, AI and MCP)."""
    policy().require(ctx, action, resource)


def allowed(ctx: TenantContext, action: str, resource: Any = None) -> bool:
    return bool(policy().authorize(ctx, action, resource).allowed)


def scoped(ctx: TenantContext, action: str, queryset: QuerySet[Any]) -> QuerySet[Any]:
    require(ctx, action)
    qs: QuerySet[Any] = policy().scope_queryset(ctx, action, queryset)
    return qs.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))


def get_scoped(ctx: TenantContext, action: str, queryset: QuerySet[Any], pk: Any) -> Any:
    try:
        key = uuid.UUID(str(pk))
    except (TypeError, ValueError) as exc:
        raise NotFound("not found") from exc
    obj = scoped(ctx, action, queryset).filter(pk=key).first()
    if obj is None:
        raise NotFound("not found")
    return obj


def lock(queryset: QuerySet[Any], pk: uuid.UUID) -> Any:
    obj = queryset.select_for_update().filter(pk=pk).first()
    if obj is None:
        raise NotFound("not found")
    return obj


def next_number(uow: UnitOfWork, company_id: uuid.UUID, kind: str, on: dt.date) -> str:
    """Gap-free number from ``kernel.sequences``; non-fiscal sequences are created on first use."""
    code, prefix = SEQUENCES[kind]
    ctx = uow.ctx
    Sequence.objects.get_or_create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        sequence_code=code,
        prefix=prefix,
        defaults={"padding": 6},
    )
    return allocate(company_id, code, prefix, on_date=on).formatted


def fiscal_number(uow: UnitOfWork, company_id: uuid.UUID, kind: str, on: dt.date) -> str:
    """Number from the company's DIAN-authorized range (``kernel.sequences`` fiscal row, any prefix).

    The range is external data entered by the tenant (P15 numbering ranges); without an active fiscal
    sequence valid on ``on`` the posting fails closed with ``numbering_range_missing``.
    """
    code = FISCAL_SEQUENCES[kind]
    ctx = uow.ctx
    rows = list(
        Sequence.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, sequence_code=code, is_fiscal=True, active=True
        ).order_by("prefix")
    )
    valid = [
        s for s in rows if (s.valid_from is None or s.valid_from <= on) and (s.valid_to is None or on <= s.valid_to)
    ]
    if not valid:
        raise NumberingRangeMissing(f"no active DIAN numbering range for {code} on {on.isoformat()}")
    return allocate(company_id, code, valid[0].prefix, on_date=on).formatted


def rate_on(uow: UnitOfWork, company_id: uuid.UUID, currency: str, day: dt.date) -> Decimal | None:
    """Ledger exchange rate (TRM) of ``currency`` on/before ``day`` (P05 ``ledger_exchange_rate``)."""
    from django.apps import apps

    if currency == "COP":
        return Decimal(1)
    if not apps.is_installed("ledger"):
        return None
    model = apps.get_model("ledger", "ExchangeRate")
    rate = (
        model.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, currency=currency, date__lte=day)
        .order_by("-date")
        .values_list("rate", flat=True)
        .first()
    )
    return None if rate is None else Decimal(rate)


def require_rate(uow: UnitOfWork, company_id: uuid.UUID, currency: str, day: dt.date) -> Decimal:
    rate = rate_on(uow, company_id, currency, day)
    if rate is None or rate <= 0:
        raise ValidationFailed(f"no exchange rate (TRM) for {currency} on or before {day.isoformat()}", field="fx_rate")
    return rate


def dimension_values(raw: Mapping[str, Any] | None) -> DimensionValues:
    data = raw or {}
    return DimensionValues(**{k: uuid.UUID(v) for k, v in data.items() if v})


def settings_for(ctx: TenantContext, company_id: uuid.UUID) -> SalesSettings:
    row = SalesSettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).first()
    if row is None:
        return SalesSettings(tenant_id=ctx.tenant_id, company_id=company_id)
    return row


def audit(
    uow: UnitOfWork,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    company_id: uuid.UUID,
    changes: Mapping[str, Any] | None = None,
    reason: str = "",
) -> None:
    """Audit row in the same unit of work (P07 hash chain). Only codes/ids/state names, never PII values."""
    service = services.optional(AuditService)
    if service is None:
        return
    service.record(
        uow,
        AuditEntry(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            company_id=company_id,
            changes=dict(changes or {}),
            reason=reason,
        ),
    )


def unknown_keys(data: Mapping[str, Any], allowed_keys: tuple[str, ...], where: str = "") -> None:
    unknown = sorted(set(data) - set(allowed_keys))
    if unknown:
        raise ValidationFailed(f"unknown properties{where}: {', '.join(unknown)}", fields=unknown)


# --------------------------------------------------------------------------- parsing


def decimal_in(
    raw: Any, field: str, *, minimum: Decimal | None = None, maximum: Decimal | None = None, positive: bool = False
) -> Decimal:
    if isinstance(raw, float):
        raise ValidationFailed(f"{field} must be a decimal string (floats are refused)", field=field)
    try:
        value = to_decimal(raw)
    except MoneyError as exc:
        raise ValidationFailed(f"{field} must be a decimal", field=field) from exc
    if positive and value <= 0:
        raise ValidationFailed(f"{field} must be positive", field=field)
    if minimum is not None and value < minimum:
        raise ValidationFailed(f"{field} must be ≥ {minimum}", field=field)
    if maximum is not None and value > maximum:
        raise ValidationFailed(f"{field} must be ≤ {maximum}", field=field)
    return value


def money_in(raw: Any, field: str, currency: str | None = None) -> Decimal:
    """``{"amount": "…", "currency": "COP"}`` (contract Money) or a bare decimal string."""
    if isinstance(raw, Mapping):
        if currency and raw.get("currency") not in (None, currency):
            raise ValidationFailed(f"{field}.currency must be {currency}", field=field)
        raw = raw.get("amount")
    return decimal_in(raw, field, minimum=Decimal(0))


def qty_in(raw: Any, field: str) -> tuple[Decimal, str | None]:
    """``{"value": "…", "uom": "KG"}`` (contract Quantity) or a bare decimal string."""
    uom: str | None = None
    if isinstance(raw, Mapping):
        uom = str(raw.get("uom") or "") or None
        raw = raw.get("value")
    return decimal_in(raw, field, positive=True), uom


def currency_in(raw: Any, default: str = "COP") -> str:
    try:
        return validate_currency(str(raw or default))
    except MoneyError as exc:
        raise ValidationFailed("currency must be an ISO 4217 code", field="currency") from exc


def uuid_in(raw: Any, field: str, *, required: bool = True) -> uuid.UUID | None:
    if raw in (None, ""):
        if required:
            raise ValidationFailed(f"{field} is required", field=field)
        return None
    if isinstance(raw, Mapping):
        raw = raw.get("id")
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{field} must be a UUID", field=field) from exc


def uuid_req(raw: Any, field: str) -> uuid.UUID:
    """Required UUID (``{"id": …}`` references accepted)."""
    value = uuid_in(raw, field)
    if value is None:  # pragma: no cover - uuid_in raises for a missing required value
        raise ValidationFailed(f"{field} is required", field=field)
    return value


def date_in(raw: Any, field: str, default: dt.date | None = None) -> dt.date:
    if raw in (None, ""):
        if default is None:
            raise ValidationFailed(f"{field} is required", field=field)
        return default
    if isinstance(raw, dt.date) and not isinstance(raw, dt.datetime):
        return raw
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a date (YYYY-MM-DD)", field=field) from exc


def datetime_in(raw: Any, field: str) -> dt.datetime:
    if isinstance(raw, dt.datetime):
        value = raw
    else:
        try:
            value = dt.datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValidationFailed(f"{field} must be an ISO 8601 date-time", field=field) from exc
    if value.tzinfo is None:
        raise ValidationFailed(f"{field} needs a time-zone offset", field=field)
    return value


def dims_in(raw: Any) -> dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationFailed("dimensions must be an object", field="dimensions")
    allowed = ("cost_center_id", "business_unit_id", "aux_abierto_id", "item_dimension_id")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValidationFailed(f"unknown dimension slots: {', '.join(unknown)}", field="dimensions")
    out: dict[str, str] = {}
    for key in allowed:
        value = uuid_in(raw.get(key), f"dimensions.{key}", required=False)
        if value is not None:
            out[key] = str(value)
    return out


def text_in(raw: Any, field: str, max_length: int = 10000) -> str:
    value = "" if raw is None else str(raw)
    if len(value) > max_length:
        raise ValidationFailed(f"{field} is longer than {max_length} characters", field=field)
    return value
