"""Treasury masters: funds, bank accounts (number encrypted, masked last 4), cash boxes and treasury concepts."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel.errors import Conflict, ValidationFailed
from kernel.uow import UnitOfWork
from treasury import crypto
from treasury.models import DIM_SLOTS, BankAccount, CashBox, Fund, TreasuryConcept, TreasuryVoucher
from treasury.services.common import (
    audit,
    bool_in,
    currency_in,
    decimal_in,
    dims_in,
    ledger_account,
    lock,
    require,
    text_in,
    unknown_keys,
    uuid_in,
    uuid_req,
)

FUND_FIELDS = ("code", "name", "fund_type", "allow_overdraft", "currency")
BANK_FIELDS = ("code", "name", "bank_code", "account_number", "currency", "account_id", "fund_id")
CASH_FIELDS = ("code", "name", "currency", "account_id", "fund_id")
CONCEPT_FIELDS = ("code", "name", "direction", "account_role", "withholding_concept_ids", "requires_dimension")
#: Service-only concept options (not in the frozen contract yet — CCR P16a-1): seed / admin tooling.
CONCEPT_EXTRA = ("requires_party", "default_dimensions", "approval_threshold", "withholding_codes")


def _code(data: Mapping[str, Any], partial: bool) -> str | None:
    if partial and "code" not in data:
        return None
    code = text_in(data.get("code"), "code", 40).strip()
    if not code:
        raise ValidationFailed("code is required", field="code")
    return code


def _name(data: Mapping[str, Any], partial: bool) -> str | None:
    if partial and "name" not in data:
        return None
    name = text_in(data.get("name"), "name", 200).strip()
    if not name:
        raise ValidationFailed("name is required", field="name")
    return name


def _unique(model: Any, uow: UnitOfWork, company_id: uuid.UUID, code: str, exclude: uuid.UUID | None = None) -> None:
    qs = model.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, code__iexact=code)
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    if qs.exists():
        raise Conflict(f"code {code} already exists", code="duplicate_code", field="code")


# --------------------------------------------------------------------------- funds


def create_fund(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Fund:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.fund.create", {"company_id": company_id})
    unknown_keys(data, FUND_FIELDS)
    code, name = _code(data, False), _name(data, False)
    assert code is not None and name is not None  # noqa: S101 - narrowing (non-partial)
    _unique(Fund, uow, company_id, code)
    fund_type = str(data.get("fund_type") or "")
    if fund_type not in Fund.FundType.values:
        raise ValidationFailed("fund_type must be BANK or CASH", field="fund_type")
    fund = Fund.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        fund_type=fund_type,
        currency=currency_in(data.get("currency")),
        allow_overdraft=bool_in(data.get("allow_overdraft"), "allow_overdraft"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "treasury.fund.create", "treasury.fund", fund.id, company_id, {"code": code})
    return fund


def update_fund(uow: UnitOfWork, fund: Fund, data: Mapping[str, Any]) -> Fund:
    ctx = uow.ctx
    require(ctx, "treasury.fund.update", fund)
    unknown_keys(data, FUND_FIELDS)
    fund = lock(Fund.objects.filter(tenant_id=ctx.tenant_id), fund.id)
    if (code := _code(data, True)) is not None:
        _unique(Fund, uow, fund.company_id, code, fund.id)
        fund.code = code
    if (name := _name(data, True)) is not None:
        fund.name = name
    if "allow_overdraft" in data:
        fund.allow_overdraft = bool_in(data.get("allow_overdraft"), "allow_overdraft")
    if "fund_type" in data and data["fund_type"] != fund.fund_type:
        raise ValidationFailed("fund_type cannot change", field="fund_type")
    fund.save_versioned(ctx.principal_id, ["code", "name", "allow_overdraft"])
    audit(uow, "treasury.fund.update", "treasury.fund", fund.id, fund.company_id, {"fields": sorted(data)})
    return fund


def archive(uow: UnitOfWork, obj: Any, permission: str, resource: str) -> Any:
    """DELETE of a master: archive when unused by vouchers, else ``conflict`` (masters are never hard-deleted)."""
    ctx = uow.ctx
    require(ctx, permission, obj)
    obj = lock(type(obj).objects.filter(tenant_id=ctx.tenant_id), obj.id)
    if isinstance(obj, Fund) and TreasuryVoucher.objects.filter(fund=obj).exclude(status="CANCELLED").exists():
        raise Conflict("the fund has vouchers; it cannot be deleted", code="conflict")
    obj.status = "ARCHIVED"
    obj.save_versioned(ctx.principal_id, ["status"])
    audit(uow, permission, resource, obj.id, obj.company_id, {"status": obj.status})
    return obj


def _fund_for(uow: UnitOfWork, company_id: uuid.UUID, raw: Any, fund_type: str, code: str, name: str, cur: str) -> Fund:
    fid = uuid_in(raw, "fund_id", required=False)
    if fid is None:  # a master without fund gets its own fund (same code/name)
        existing = Fund.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, code__iexact=code).first()
        if existing is not None:
            raise Conflict(f"fund code {code} already exists; send fund_id", code="duplicate_code", field="fund_id")
        return Fund.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company_id,
            code=code,
            name=name,
            fund_type=fund_type,
            currency=cur,
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
    fund = Fund.objects.filter(tenant_id=uow.ctx.tenant_id, company_id=company_id, id=fid).first()
    if fund is None:
        raise ValidationFailed("fund_id is not a fund of the company", field="fund_id")
    if fund.fund_type != fund_type or fund.currency != cur:
        raise ValidationFailed(f"fund {fund.code} must be a {fund_type} fund in {cur}", field="fund_id")
    return fund


# --------------------------------------------------------------------------- bank accounts / cash boxes


def create_bank_account(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> BankAccount:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.bank_account.create", {"company_id": company_id})
    unknown_keys(data, BANK_FIELDS)
    code, name = _code(data, False), _name(data, False)
    assert code is not None and name is not None  # noqa: S101 - narrowing (non-partial)
    _unique(BankAccount, uow, company_id, code)
    number = "".join(ch for ch in text_in(data.get("account_number"), "account_number", 40) if ch.isalnum())
    if len(number) < 4:
        raise ValidationFailed("account_number needs at least 4 characters", field="account_number")
    cur = currency_in(data.get("currency"))
    account = ledger_account(ctx, company_id, uuid_req(data.get("account_id"), "account_id"))
    fund = _fund_for(uow, company_id, data.get("fund_id"), Fund.FundType.BANK, code, name, cur)
    row = BankAccount.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        bank_code=text_in(data.get("bank_code"), "bank_code", 40),
        account_number_enc=crypto.encrypt(number),
        account_number_last4=number[-4:],
        currency=cur,
        account_id=account.id,
        fund=fund,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "treasury.bank_account.create", "treasury.bank_account", row.id, company_id, {"code": code})
    return row


def update_bank_account(uow: UnitOfWork, row: BankAccount, data: Mapping[str, Any]) -> BankAccount:
    ctx = uow.ctx
    require(ctx, "treasury.bank_account.update", row)
    unknown_keys(data, BANK_FIELDS)
    row = lock(BankAccount.objects.filter(tenant_id=ctx.tenant_id), row.id)
    if (code := _code(data, True)) is not None:
        _unique(BankAccount, uow, row.company_id, code, row.id)
        row.code = code
    if (name := _name(data, True)) is not None:
        row.name = name
    if "bank_code" in data:
        row.bank_code = text_in(data.get("bank_code"), "bank_code", 40)
    if "account_number" in data:
        number = "".join(ch for ch in text_in(data.get("account_number"), "account_number", 40) if ch.isalnum())
        if len(number) < 4:
            raise ValidationFailed("account_number needs at least 4 characters", field="account_number")
        row.account_number_enc, row.account_number_last4 = crypto.encrypt(number), number[-4:]
    if "account_id" in data:
        row.account_id = ledger_account(ctx, row.company_id, uuid_req(data.get("account_id"), "account_id")).id
    row.save_versioned(
        ctx.principal_id, ["code", "name", "bank_code", "account_number_enc", "account_number_last4", "account_id"]
    )
    audit(
        uow, "treasury.bank_account.update", "treasury.bank_account", row.id, row.company_id, {"fields": sorted(data)}
    )
    return row


def create_cash_box(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> CashBox:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.cash_box.create", {"company_id": company_id})
    unknown_keys(data, CASH_FIELDS)
    code, name = _code(data, False), _name(data, False)
    assert code is not None and name is not None  # noqa: S101 - narrowing (non-partial)
    _unique(CashBox, uow, company_id, code)
    cur = currency_in(data.get("currency"))
    account = ledger_account(ctx, company_id, uuid_req(data.get("account_id"), "account_id"))
    fund = _fund_for(uow, company_id, data.get("fund_id"), Fund.FundType.CASH, code, name, cur)
    row = CashBox.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        currency=cur,
        account_id=account.id,
        fund=fund,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "treasury.cash_box.create", "treasury.cash_box", row.id, company_id, {"code": code})
    return row


def update_cash_box(uow: UnitOfWork, row: CashBox, data: Mapping[str, Any]) -> CashBox:
    ctx = uow.ctx
    require(ctx, "treasury.cash_box.update", row)
    unknown_keys(data, CASH_FIELDS)
    row = lock(CashBox.objects.filter(tenant_id=ctx.tenant_id), row.id)
    if (code := _code(data, True)) is not None:
        _unique(CashBox, uow, row.company_id, code, row.id)
        row.code = code
    if (name := _name(data, True)) is not None:
        row.name = name
    if "account_id" in data:
        row.account_id = ledger_account(ctx, row.company_id, uuid_req(data.get("account_id"), "account_id")).id
    row.save_versioned(ctx.principal_id, ["code", "name", "account_id"])
    audit(uow, "treasury.cash_box.update", "treasury.cash_box", row.id, row.company_id, {"fields": sorted(data)})
    return row


# --------------------------------------------------------------------------- concepts


def _concept_values(data: Mapping[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not partial or "direction" in data:
        direction = str(data.get("direction") or "")
        if direction not in TreasuryConcept.Direction.values:
            raise ValidationFailed("direction must be IN, OUT or BOTH", field="direction")
        out["direction"] = direction
    if not partial or "account_role" in data:
        role = text_in(data.get("account_role"), "account_role", 120).strip()
        if not role:
            raise ValidationFailed("account_role is required", field="account_role")
        out["account_role"] = role
    if "withholding_concept_ids" in data:
        raw = data.get("withholding_concept_ids") or []
        if not isinstance(raw, list):
            raise ValidationFailed("withholding_concept_ids must be a list", field="withholding_concept_ids")
        out["withholding_concept_ids"] = [str(uuid_in(x, "withholding_concept_ids")) for x in raw]
    if "requires_dimension" in data:
        out["requires_dimension"] = bool_in(data.get("requires_dimension"), "requires_dimension")
    if "requires_party" in data:
        out["requires_party"] = bool_in(data.get("requires_party"), "requires_party")
    if "default_dimensions" in data:
        out["default_dimensions"] = {k: str(v) for k, v in dims_in(data.get("default_dimensions")).items()}
    if "approval_threshold" in data:
        raw_t = data.get("approval_threshold")
        out["approval_threshold"] = (
            None if raw_t in (None, "") else decimal_in(raw_t, "approval_threshold", minimum=Decimal(0))
        )
    if "withholding_codes" in data:
        codes = data.get("withholding_codes") or []
        if not isinstance(codes, list):
            raise ValidationFailed("withholding_codes must be a list", field="withholding_codes")
        out["withholding_codes"] = [text_in(c, "withholding_codes", 40) for c in codes]
    return out


def create_concept(
    uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any], *, extended: bool = False
) -> TreasuryConcept:
    """POST /concepts (contract fields); ``extended=True`` (seed / service callers) also accepts CONCEPT_EXTRA."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "treasury.concept.create", {"company_id": company_id})
    unknown_keys(data, CONCEPT_FIELDS + (CONCEPT_EXTRA if extended else ()))
    code, name = _code(data, False), _name(data, False)
    assert code is not None and name is not None  # noqa: S101 - narrowing (non-partial)
    _unique(TreasuryConcept, uow, company_id, code)
    row = TreasuryConcept.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
        **_concept_values(data, False),
    )
    audit(uow, "treasury.concept.create", "treasury.concept", row.id, company_id, {"code": code})
    return row


def update_concept(uow: UnitOfWork, row: TreasuryConcept, data: Mapping[str, Any]) -> TreasuryConcept:
    ctx = uow.ctx
    require(ctx, "treasury.concept.update", row)
    unknown_keys(data, CONCEPT_FIELDS)
    row = lock(TreasuryConcept.objects.filter(tenant_id=ctx.tenant_id), row.id)
    fields: list[str] = []
    if (code := _code(data, True)) is not None:
        _unique(TreasuryConcept, uow, row.company_id, code, row.id)
        row.code = code
        fields.append("code")
    if (name := _name(data, True)) is not None:
        row.name = name
        fields.append("name")
    for key, value in _concept_values(data, True).items():
        setattr(row, key, value)
        fields.append(key)
    row.save_versioned(ctx.principal_id, fields)
    audit(uow, "treasury.concept.update", "treasury.concept", row.id, row.company_id, {"fields": sorted(data)})
    return row


__all__ = [
    "DIM_SLOTS",
    "archive",
    "create_bank_account",
    "create_cash_box",
    "create_concept",
    "create_fund",
    "update_bank_account",
    "update_cash_box",
    "update_concept",
    "update_fund",
]
