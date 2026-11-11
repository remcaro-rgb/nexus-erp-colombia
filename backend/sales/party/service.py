"""Party master (owned by P10, shared with purchasing/HR/treasury through :class:`PartyService`).

NIT + DV validated by P06 (``localization_co.nit``: ``nit_invalid``); duplicates by NIT are refused
(``party_duplicate``); duplicate *candidates* (same NIT, e-mail or phone) are reported by :func:`duplicates`;
``:merge`` folds a duplicate into a survivor (the duplicate becomes MERGED, never deleted, C1-5).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Q

from kernel.errors import NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import PartyDuplicate
from sales.models import Party, PriceList
from sales.services.common import audit, decimal_in, lock, require, text_in, unknown_keys, uuid_in, uuid_req

ROLES = ("customer", "vendor", "employee", "other")
FIELDS = (
    "code",
    "legal_name",
    "name",
    "trade_name",
    "nit",
    "dv",
    "person_type",
    "roles",
    "email",
    "einvoice_email",
    "phone",
    "address",
    "municipality_code",
    "tax_profile_id",
    "credit_limit",
    "payment_terms_days",
    "price_list_id",
    "customer_group",
    "salesperson_id",
    "territory",
    "consent_ref",
    "status",
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _nit(raw: Any, dv: Any) -> tuple[str, str]:
    from localization_co import nit as nitlib

    if raw in (None, ""):
        raise ValidationFailed("nit is required", field="nit")
    result = nitlib.require_valid(str(raw), None if dv in (None, "") else str(dv))
    return result.number, str(result.check_digit)


def _phone(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not 7 <= len(digits) <= 15:
        raise ValidationFailed("phone must have 7–15 digits", field="phone")
    return digits


def _email(raw: Any, field: str) -> str | None:
    if raw in (None, ""):
        return None
    value = str(raw).strip().lower()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise ValidationFailed(f"{field} must be an e-mail address", field=field)
    return value


def _roles(raw: Any) -> list[str]:
    if raw in (None, ""):
        return ["customer"]
    if not isinstance(raw, list) or any(r not in ROLES for r in raw):
        raise ValidationFailed(f"roles must be a list of {', '.join(ROLES)}", field="roles")
    return sorted(set(raw))


def _values(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "legal_name" in data or "name" in data or not partial:
        name = text_in(data.get("legal_name", data.get("name")), "legal_name", 250).strip()
        if not name:
            raise ValidationFailed("legal_name is required", field="legal_name")
        out["legal_name"] = name
    if "nit" in data or not partial:
        out["nit"], out["dv"] = _nit(data.get("nit"), data.get("dv"))
    if "person_type" in data or not partial:
        pt = data.get("person_type", "JURIDICA")
        if pt not in Party.PersonType.values:
            raise ValidationFailed("person_type must be NATURAL or JURIDICA", field="person_type")
        out["person_type"] = pt
    if "roles" in data or not partial:
        out["roles"] = _roles(data.get("roles"))
    for key in ("email", "einvoice_email"):
        if key in data:
            out[key] = _email(data[key], key)
    if "phone" in data:
        out["phone"] = _phone(data["phone"])
    for key, size in (("trade_name", 250), ("address", 255), ("customer_group", 40), ("territory", 60)):
        if key in data:
            out[key] = text_in(data[key], key, size) or ("" if key in ("customer_group", "territory") else None)
    if "municipality_code" in data:
        code = data["municipality_code"]
        if code not in (None, "") and not re.fullmatch(r"\d{5}", str(code)):
            raise ValidationFailed("municipality_code must be a 5-digit DANE code", field="municipality_code")
        out["municipality_code"] = code or None
    if "credit_limit" in data:
        raw = data["credit_limit"]
        if isinstance(raw, Mapping):
            raw = raw.get("amount")
        out["credit_limit"] = None if raw in (None, "") else decimal_in(raw, "credit_limit", minimum=Decimal(0))
    if "payment_terms_days" in data:
        terms = data["payment_terms_days"]
        if terms is not None and (not isinstance(terms, int) or isinstance(terms, bool) or not 0 <= terms <= 3650):
            raise ValidationFailed("payment_terms_days must be an integer 0–3650", field="payment_terms_days")
        out["payment_terms_days"] = terms
    for key in ("tax_profile_id", "salesperson_id", "consent_ref"):
        if key in data:
            out[key] = uuid_in(data[key], key, required=False)
    if "price_list_id" in data:
        pl = uuid_in(data["price_list_id"], "price_list_id", required=False)
        if (
            pl is not None
            and not PriceList.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=pl).exists()
        ):
            raise ValidationFailed("price_list_id is not a price list of the company", field="price_list_id")
        out["price_list_id"] = pl
    if "status" in data:
        if data["status"] not in (Party.Status.ACTIVE, Party.Status.INACTIVE):
            raise ValidationFailed("status must be ACTIVE or INACTIVE", field="status")
        out["status"] = data["status"]
    return out


def _check_duplicate(ctx: TenantContext, company_id: uuid.UUID, nit: str, exclude: uuid.UUID | None = None) -> None:
    qs = Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, nit=nit).exclude(
        status=Party.Status.MERGED
    )
    if exclude is not None:
        qs = qs.exclude(id=exclude)
    existing = qs.first()
    if existing is not None:
        raise PartyDuplicate(f"a party with NIT {nit} already exists", existing_id=str(existing.id))


def create_party(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, permission: str = "sales.party.create"
) -> Party:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, permission, {"company_id": company_id})
    unknown_keys(data, FIELDS)
    values = _values(uow, company_id, data, partial=False)
    _check_duplicate(ctx, company_id, values["nit"])
    code = text_in(data.get("code"), "code", 40).strip() or f"T-{values['nit']}"
    if Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).exists():
        raise PartyDuplicate(f"party code {code} already exists")
    party = Party.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        **values,
    )
    audit(uow, "sales.party.create", "sales.party", party.id, company_id, {"code": code, "roles": party.roles})
    return party


def update_party(uow: UnitOfWork, party: Party, data: Mapping[str, Any]) -> Party:
    ctx = uow.ctx
    require(ctx, "sales.party.update", party)
    unknown_keys(data, FIELDS)
    if "code" in data:
        raise ValidationFailed("code cannot be changed", field="code")
    party = lock(Party.objects.filter(tenant_id=ctx.tenant_id), party.id)
    if party.status == Party.Status.MERGED:
        raise ValidationFailed("a merged party cannot be changed", field="status")
    values = _values(uow, party.company_id, data, partial=True)
    if "nit" in values:
        _check_duplicate(ctx, party.company_id, values["nit"], exclude=party.id)
    for key, value in values.items():
        setattr(party, key, value)
    party.version += 1
    party.updated_by = ctx.principal_id
    party.save()
    audit(uow, "sales.party.update", "sales.party", party.id, party.company_id, {"fields": sorted(values)})
    return party


def duplicates(
    ctx: TenantContext,
    company_id: uuid.UUID,
    *,
    nit: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> list[Party]:
    """Duplicate candidates by NIT (normalized), e-mail or phone (T-P10-14)."""
    from localization_co import nit as nitlib

    cond = Q()
    if nit:
        cond |= Q(nit=nitlib.normalize(nit.split("-")[0]))
    if email:
        cond |= Q(email=email.strip().lower())
    if phone:
        cond |= Q(phone=re.sub(r"\D", "", phone))
    if not cond:
        return []
    return list(
        Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
        .exclude(status=Party.Status.MERGED)
        .filter(cond)
        .order_by("code")
    )


def merge(uow: UnitOfWork, survivor: Party, duplicate_id: Any, reason: str) -> Party:
    """Fold ``duplicate`` into ``survivor`` (roles union; the duplicate becomes MERGED with ``merged_into``)."""
    ctx = uow.ctx
    require(ctx, "sales.party.update", survivor)
    if not reason.strip():
        raise ValidationFailed("a merge needs a reason", field="reason", code="reason_required")
    other_id = uuid_req(duplicate_id, "duplicate_id")
    ids_sorted = sorted([survivor.id, other_id])  # fixed lock order
    rows = {
        p.id: p
        for p in Party.objects.select_for_update()
        .filter(tenant_id=ctx.tenant_id, company_id=survivor.company_id, id__in=ids_sorted)
        .order_by("id")
    }
    if other_id not in rows or other_id == survivor.id:
        raise NotFound("not found")
    keep, gone = rows[survivor.id], rows[other_id]
    if gone.status == Party.Status.MERGED or keep.status == Party.Status.MERGED:
        raise ValidationFailed("merged parties cannot be merged again", field="duplicate_id")
    keep.roles = sorted(set(keep.roles) | set(gone.roles))
    for key in ("email", "einvoice_email", "phone", "address", "municipality_code", "tax_profile_id", "consent_ref"):
        if getattr(keep, key) in (None, "") and getattr(gone, key) not in (None, ""):
            setattr(keep, key, getattr(gone, key))
    keep.version += 1
    keep.updated_by = ctx.principal_id
    keep.save()
    gone.status, gone.merged_into = Party.Status.MERGED, keep.id
    gone.version += 1
    gone.updated_by = ctx.principal_id
    gone.save()
    audit(uow, "sales.party.merge", "sales.party", keep.id, keep.company_id, {"merged": str(gone.id)}, reason)
    return keep


def get_customer(uow: UnitOfWork, company_id: uuid.UUID, raw: Any, field: str = "party") -> Party:
    pid = uuid_req(raw, field)
    party = Party.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=pid).first()
    if party is None or party.status != Party.Status.ACTIVE or "customer" not in (party.roles or []):
        raise ValidationFailed(f"{field} is not an active customer of the company", field=field)
    return party


@dataclass(frozen=True, slots=True)
class PartyInfo:
    """Read model of a party for other modules (no confidential credit data)."""

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    legal_name: str
    nit: str
    dv: str
    person_type: str
    roles: tuple[str, ...]
    email: str | None
    einvoice_email: str | None
    municipality_code: str | None
    tax_profile_id: uuid.UUID | None
    status: str


def info(p: Party) -> PartyInfo:
    return PartyInfo(
        p.id,
        p.company_id,
        p.code,
        p.legal_name,
        p.nit,
        p.dv,
        p.person_type,
        tuple(p.roles or ()),
        p.email,
        p.einvoice_email,
        p.municipality_code,
        p.tax_profile_id,
        p.status,
    )


class PartyService:
    """Shared party master for P11/P13/P16 (``backend/sales/party``, PolicyService on every call)."""

    def get(self, ctx: TenantContext, company_id: uuid.UUID, party_id: uuid.UUID) -> PartyInfo:
        require(ctx, "sales.party.read", {"company_id": company_id})
        ctx.require_company(company_id)
        p = Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=party_id).first()
        if p is None:
            raise NotFound("not found")
        return info(p)

    def find_by_nit(self, ctx: TenantContext, company_id: uuid.UUID, nit: str) -> PartyInfo | None:
        require(ctx, "sales.party.read", {"company_id": company_id})
        found = duplicates(ctx, company_id, nit=nit)
        return info(found[0]) if found else None

    def ensure(self, uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], role: str) -> PartyInfo:
        """Existing party with the NIT (role added) or a new one (``sales.party.create``)."""
        existing = duplicates(uow.ctx, company_id, nit=str(data.get("nit") or ""))
        if existing:
            p = existing[0]
            if role not in (p.roles or []):
                p = update_party(uow, p, {"roles": sorted({*p.roles, role})})
            return info(p)
        return info(create_party(uow, company_id, {**data, "roles": [role]}))
