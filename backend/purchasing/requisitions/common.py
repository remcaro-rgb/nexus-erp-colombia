"""Helpers shared by the P11b parts: policy, numbering, audit, settings and strict input parsing.

P11b codes against P11a's frozen contract (``purchasing.interfaces``) and reads P11a models; it never calls
P11a's private service helpers, so the small parsers live here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import QuerySet

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.policy import PolicyService
from kernel.models import Sequence
from kernel.money import QTY, round_half_up
from kernel.sequences import allocate
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

#: kernel.sequences codes of P11b documents (gap-free per company).
SEQUENCES = {
    "internal_requisition": ("PUR-IREQ", "SOL-"),
    "consolidation": ("PUR-CONS", "CON-"),
    "purchase_requisition": ("PUR-PREQ", "SP-"),
    "shipment_notice": ("PUR-ASN", "AE-"),
}
MAX_TEXT = 10000


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


def next_number(uow: UnitOfWork, company_id: uuid.UUID, kind: str, on: dt.date) -> str:
    code, prefix = SEQUENCES[kind]
    Sequence.objects.get_or_create(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, sequence_code=code, prefix=prefix, defaults={"padding": 6}
    )
    return allocate(company_id, code, prefix, on_date=on).formatted


def audit(
    uow: UnitOfWork, action: str, resource: str, obj_id: Any, company_id: uuid.UUID, data: Mapping[str, Any]
) -> None:
    svc = services.optional(AuditService)
    if svc is not None:
        svc.record(uow, AuditEntry(action, resource, obj_id, company_id, dict(data)))


# --------------------------------------------------------------------------- parsing (strict, Decimal only)


def reject_unknown(data: Mapping[str, Any], allowed_keys: tuple[str, ...], where: str = "") -> None:
    unknown = sorted(set(data) - set(allowed_keys))
    if unknown:
        raise ValidationFailed(f"unknown properties{where}: {', '.join(unknown)}", fields=unknown)


def uuid_in(value: Any, field: str, *, required: bool = True) -> uuid.UUID | None:
    if isinstance(value, Mapping):
        value = value.get("id")
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{field} is required", field=field)
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a UUID", field=field) from exc


def uuid_req(value: Any, field: str) -> uuid.UUID:
    out = uuid_in(value, field)
    assert out is not None  # noqa: S101 - required=True never returns None
    return out


def date_in(value: Any, field: str, default: dt.date | None = None) -> dt.date:
    if value in (None, ""):
        if default is None:
            raise ValidationFailed(f"{field} is required", field=field)
        return default
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a date (YYYY-MM-DD)", field=field) from exc


def text_in(value: Any, field: str, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValidationFailed(f"{field} must be a text of at most {limit} characters", field=field)
    return value


def decimal_in(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationFailed(f"{field} must be a decimal string", field=field)
    try:
        out = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValidationFailed(f"{field} must be a decimal string", field=field) from exc
    if not out.is_finite() or (positive and out <= 0) or out < 0:
        raise ValidationFailed(f"{field} must be {'positive' if positive else 'non-negative'}", field=field)
    return out


def qty_in(value: Any, field: str) -> tuple[Decimal, str]:
    """``Quantity`` ``{value, uom}`` (or a bare decimal string) → (positive quantity, uom)."""
    uom = ""
    if isinstance(value, Mapping):
        uom = text_in(value.get("uom"), f"{field}.uom", 20)
        value = value.get("value")
    qty = decimal_in(value, f"{field}.value", positive=True)
    if round_half_up(qty, QTY) != qty:
        raise ValidationFailed(f"{field} has more than 6 decimals", field=field)
    return qty, uom


def money_in(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        value = value.get("amount")
    return decimal_in(value, field)


def dims_in(value: Any, field: str = "dimensions") -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"{field} must be an object", field=field)
    out: dict[str, str] = {}
    for key, raw in value.items():
        if raw in (None, ""):
            continue
        out[str(key)] = str(uuid_req(raw, f"{field}.{key}"))
    return out


def qty_json(value: Decimal, uom: str) -> dict[str, str]:
    return {"value": str(round_half_up(value, QTY)), "uom": uom}
