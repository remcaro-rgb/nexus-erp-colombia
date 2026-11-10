"""Shared helpers of the purchasing services: policy, numbering, settings, audit, money parsing."""

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
from purchasing import scoring
from purchasing.models import PurchasingSettings

#: kernel.sequences codes (gap-free per company; D-017 style prefixes).
SEQUENCES = {
    "rfq": ("PUR-RFQ", "SC-"),
    "quote": ("PUR-QUOTE", "CT-"),
    "order": ("PUR-PO", "OC-"),
    "receipt": ("PUR-GR", "EA-"),
    "vendor_invoice": ("PUR-VI", "FP-"),
}

#: Account roles used by purchasing postings (shared/07 §2); role → account determinations are tenant data.
ROLE_INVENTORY = "INVENTORY"
ROLE_GRNI = "GRNI"
ROLE_EXPENSE = "EXPENSE"
ROLE_AP = "AP"
ROLE_VAT_RECOVERABLE = "VAT_RECOVERABLE"
ROLE_WHT_PAYABLE = "WHT_PAYABLE"
ROLE_PRICE_VARIANCE = "PURCHASE_PRICE_VARIANCE"


def policy() -> PolicyService:
    return services.get(PolicyService)


def require(ctx: TenantContext, action: str, resource: Any = None) -> None:
    """Deny by default through P04's PolicyService (same check for UI, API, export, AI and MCP)."""
    policy().require(ctx, action, resource)


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
    """Gap-free number from ``kernel.sequences``; the purchasing sequences are created on first use."""
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


def rate_on(uow: UnitOfWork, company_id: uuid.UUID, currency: str, day: dt.date) -> Decimal | None:
    """Ledger exchange rate of ``currency`` on/before ``day`` (P05 ``ledger_exchange_rate``), ``None`` if absent."""
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


def dimension_values(raw: Mapping[str, Any] | None) -> DimensionValues:
    data = raw or {}
    return DimensionValues(**{k: uuid.UUID(v) for k, v in data.items() if v})


def settings_for(ctx: TenantContext, company_id: uuid.UUID) -> PurchasingSettings:
    row = PurchasingSettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).first()
    if row is None:
        return PurchasingSettings(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            scoring_weights={k: str(v) for k, v in scoring.DEFAULT_WEIGHTS.items()},
        )
    return row


def configure(uow: UnitOfWork, company_id: uuid.UUID, **values: Any) -> PurchasingSettings:
    """Create/update the company's purchasing settings (setup/seed; needs ``purchasing.settings.update``)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.settings.update", {"company_id": company_id})
    allowed = {
        "scoring_weights",
        "receipt_tolerance_pct",
        "invoice_qty_tolerance_pct",
        "invoice_price_tolerance_pct",
        "po_auto_approve_limit",
        "rfq_deadline_business_days",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown settings: {', '.join(unknown)}")
    if "scoring_weights" in values:
        values["scoring_weights"] = {k: str(v) for k, v in scoring.validate_weights(values["scoring_weights"]).items()}
    for key in ("receipt_tolerance_pct", "invoice_qty_tolerance_pct", "invoice_price_tolerance_pct"):
        if key in values:
            values[key] = decimal_in(values[key], key, minimum=Decimal(0), maximum=Decimal(100))
    if "po_auto_approve_limit" in values:
        values["po_auto_approve_limit"] = decimal_in(
            values["po_auto_approve_limit"], "po_auto_approve_limit", minimum=Decimal(0)
        )
    row, _ = PurchasingSettings.objects.update_or_create(
        tenant_id=ctx.tenant_id, company_id=company_id, defaults={**values, "updated_by": ctx.principal_id}
    )
    audit(uow, "purchasing.settings.update", "purchasing.settings", row.id, company_id, {"fields": sorted(values)})
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
