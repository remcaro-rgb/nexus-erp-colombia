"""Shared helpers of the treasury services: policy, numbering, settings, audit, ledger reads and input parsing."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.db.models import QuerySet

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.common import DimensionValues
from kernel.interfaces.policy import PolicyService
from kernel.models import Sequence
from kernel.money import MoneyError, minor_units, q_amount, round_half_up, to_decimal, validate_currency
from kernel.sequences import allocate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from treasury.models import DIM_SLOTS, TreasurySettings

#: Account roles used by treasury postings (shared/07 §2); role → account determinations are tenant data (P21 seed).
ROLE_AR = "AR"
ROLE_AP = "AP"
ROLE_CUSTOMER_ADVANCE = "CUSTOMER_ADVANCE"
ROLE_VENDOR_ADVANCE = "VENDOR_ADVANCE"
ROLE_WHT_RECEIVABLE = "WHT_RECEIVABLE"
ROLE_WHT_PAYABLE = "WHT_PAYABLE"

#: kernel.sequences codes (D-017: ``RC`` / ``CE`` per company with a configurable prefix; payment batches ``LP``).
SEQ_RC = "RC"
SEQ_CE = "CE"
SEQ_BATCH = ("TES-LP", "LP-")


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


def ensure_sequence(ctx: TenantContext, company_id: uuid.UUID, code: str, prefix: str) -> None:
    Sequence.objects.get_or_create(
        tenant_id=ctx.tenant_id, company_id=company_id, sequence_code=code, prefix=prefix, defaults={"padding": 6}
    )


def next_number(uow: UnitOfWork, company_id: uuid.UUID, code: str, prefix: str, on: dt.date) -> str:
    """Gap-free number from ``kernel.sequences`` (row lock; a rollback returns the number)."""
    ensure_sequence(uow.ctx, company_id, code, prefix)
    return allocate(company_id, code, prefix, on_date=on).formatted


def settings_for(ctx: TenantContext, company_id: uuid.UUID) -> TreasurySettings:
    row = TreasurySettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).first()
    return row or TreasurySettings(tenant_id=ctx.tenant_id, company_id=company_id)


SETTINGS_FIELDS = (
    "rc_prefix",
    "ce_prefix",
    "rc_approval_threshold",
    "ce_approval_threshold",
    "dual_approval_threshold",
)


def configure(uow: UnitOfWork, company_id: uuid.UUID, **values: Any) -> TreasurySettings:
    """Create/update the company's treasury settings (setup/seed; needs ``treasury.fund.update``)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.fund.update", {"company_id": company_id})
    unknown = sorted(set(values) - set(SETTINGS_FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown settings: {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for key, raw in values.items():
        if key.endswith("_prefix"):
            clean[key] = text_in(raw, key, 10)
        elif raw is None and key != "dual_approval_threshold":
            clean[key] = None
        else:
            clean[key] = decimal_in(raw, key, minimum=Decimal(0))
    row, _ = TreasurySettings.objects.update_or_create(
        tenant_id=ctx.tenant_id, company_id=company_id, defaults={**clean, "updated_by": ctx.principal_id}
    )
    for kind, prefix in ((SEQ_RC, row.rc_prefix), (SEQ_CE, row.ce_prefix)):
        ensure_sequence(ctx, company_id, kind, prefix)
    audit(uow, "treasury.settings.update", "treasury.settings", row.id, company_id, {"fields": sorted(clean)})
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


# --------------------------------------------------------------------------- ledger reads (P05 tables, read-only)


def ledger_account(ctx: TenantContext, company_id: uuid.UUID, account_id: uuid.UUID) -> Any:
    """The P05 ``Account`` row (read through ``django.apps``; treasury never writes ledger tables)."""
    if not apps.is_installed("ledger"):
        raise ValidationFailed("the ledger is not installed", field="account_id")
    model = apps.get_model("ledger", "Account")
    account = model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=account_id).first()
    if account is None:
        raise ValidationFailed("account_id is not an account of the company", field="account_id")
    return account


def rate_on(ctx: TenantContext, company_id: uuid.UUID, currency: str, day: dt.date) -> Decimal | None:
    """Ledger exchange rate of ``currency`` on/before ``day`` (P05 ``ledger_exchange_rate``), ``None`` if absent."""
    if currency == "COP":
        return Decimal(1)
    if not apps.is_installed("ledger"):
        return None
    model = apps.get_model("ledger", "ExchangeRate")
    rate = (
        model.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, currency=currency, date__lte=day)
        .order_by("-date")
        .values_list("rate", flat=True)
        .first()
    )
    return None if rate is None else Decimal(rate)


# --------------------------------------------------------------------------- parsing


def unknown_keys(data: Mapping[str, Any], allowed: Iterable[str], where: str = "") -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise ValidationFailed(f"unknown properties{where}: {', '.join(extra)}", fields=extra)


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


def money_in(raw: Any, field: str, currency: str) -> Decimal:
    """``{"amount": "…", "currency": "COP"}`` (contract Money) or a bare decimal string, rounded to the currency."""
    if isinstance(raw, Mapping):
        if raw.get("currency") not in (None, currency):
            raise ValidationFailed(
                f"{field}.currency must be {currency}", field=field, code="treasury_currency_mismatch"
            )
        raw = raw.get("amount")
    return q_amount(round_half_up(decimal_in(raw, field, minimum=Decimal(0)), minor_units(currency)))


def qty_in(raw: Any, field: str) -> Decimal:
    if raw in (None, ""):
        return Decimal(1)
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    return decimal_in(raw, field, positive=True)


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


def dims_in(raw: Any, field: str = "dimensions") -> dict[str, uuid.UUID]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"{field} must be an object", field=field)
    unknown = sorted(set(raw) - set(DIM_SLOTS))
    if unknown:
        raise ValidationFailed(f"unknown dimension slots: {', '.join(unknown)}", field=field)
    out: dict[str, uuid.UUID] = {}
    for key in DIM_SLOTS:
        value = uuid_in(raw.get(key), f"{field}.{key}", required=False)
        if value is not None:
            out[key] = value
    return out


def dimension_values(slots: Mapping[str, Any]) -> DimensionValues:
    return DimensionValues(**{k: uuid.UUID(str(v)) for k, v in slots.items() if v})


def text_in(raw: Any, field: str, max_length: int = 10000) -> str:
    value = "" if raw is None else str(raw)
    if len(value) > max_length:
        raise ValidationFailed(f"{field} is longer than {max_length} characters", field=field)
    return value


def bool_in(raw: Any, field: str, default: bool = False) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ValidationFailed(f"{field} must be a boolean", field=field)
    return raw
