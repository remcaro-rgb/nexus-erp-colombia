"""Supplier (vendor profile) master used by RFQs, orders and invoices (P11a)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel.errors import Conflict, ValidationFailed
from kernel.uow import UnitOfWork
from purchasing.models import Supplier
from purchasing.services.common import audit, currency_in, decimal_in, require, text_in, uuid_in, uuid_req

FIELDS = ("code", "name", "nit", "party_id", "rating", "payment_terms_days", "currency", "active")


def create_supplier(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Supplier:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "purchasing.supplier.create", {"company_id": company_id})
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    code = text_in(data.get("code"), "code", 40).strip()
    name = text_in(data.get("name"), "name", 250).strip()
    if not code or not name:
        raise ValidationFailed("code and name are required", fields=["code", "name"])
    if Supplier.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).exists():
        raise Conflict(f"supplier code {code} already exists", code="party_duplicate")
    terms = data.get("payment_terms_days", 30)
    if not isinstance(terms, int) or isinstance(terms, bool) or not 0 <= terms <= 3650:
        raise ValidationFailed("payment_terms_days must be an integer 0–3650", field="payment_terms_days")
    supplier = Supplier.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        nit=text_in(data.get("nit"), "nit", 25),
        party_id=uuid_in(data.get("party_id"), "party_id", required=False),
        rating=decimal_in(data.get("rating", "3"), "rating", minimum=Decimal(0), maximum=Decimal(5)),
        payment_terms_days=terms,
        currency=currency_in(data.get("currency")),
        active=bool(data.get("active", True)),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "purchasing.supplier.create", "purchasing.supplier", supplier.id, company_id, {"code": code})
    return supplier


def get_supplier(uow: UnitOfWork, company_id: uuid.UUID, supplier_id: Any, field: str = "party") -> Supplier:
    sid = uuid_req(supplier_id, field)
    supplier = Supplier.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=sid).first()
    if supplier is None or not supplier.active:
        raise ValidationFailed(f"{field} is not an active supplier of the company", field=field)
    return supplier
