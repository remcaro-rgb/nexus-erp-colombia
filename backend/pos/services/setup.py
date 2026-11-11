"""POS configuration and terminals (F-ACC-BRANCH contributor: ``PosConfig.branch_id`` required, same company).

The branch is checked three times: the P04 service (``validate_branch_link`` → ``branch_cross_company`` /
``BRANCH_ARCHIVED``), the composite FK ``(tenant_id, company_id, branch_id) → iam_branch`` (migration 0002) and,
at posting, ``assert_postable_branch``. The warehouse must belong to the same company and its own branch must be
the POS branch (stock and GL of a POS sale carry one branch).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from iam.branches.services import validate_branch_link
from kernel import clock
from kernel.errors import ValidationFailed
from kernel.models import Sequence
from kernel.uow import UnitOfWork
from pos.models import TENDER_TYPES, PosConfig, Terminal
from sales.services.common import audit, decimal_in, lock, require, text_in, unknown_keys, uuid_in, uuid_req

CONFIG_FIELDS = (
    "name",
    "active",
    "branch_id",
    "warehouse_id",
    "location_id",
    "price_list_id",
    "numbering_range_id",
    "allowed_tenders",
    "receipt_profile",
    "cash_limit",
    "allow_returns",
    "blind_count",
    "default_party_id",
)


def _warehouse(uow: UnitOfWork, company_id: uuid.UUID, warehouse_id: uuid.UUID, branch_id: uuid.UUID) -> None:
    from django.apps import apps

    if not apps.is_installed("inventory"):  # pragma: no cover - inventory is always installed in this edition
        return
    wh = apps.get_model("inventory", "Warehouse").objects.filter(company_id=company_id, id=warehouse_id).first()
    if wh is None:
        raise ValidationFailed("warehouse_id is not a warehouse of the company", field="warehouse_id")
    if wh.branch_id != branch_id:
        raise ValidationFailed("the warehouse belongs to another branch than the POS", field="warehouse_id")


def _location(company_id: uuid.UUID, warehouse_id: uuid.UUID, location_id: uuid.UUID | None) -> None:
    if location_id is None:
        return
    from django.apps import apps

    loc = apps.get_model("inventory", "Location").objects.filter(company_id=company_id, id=location_id).first()
    if loc is None or loc.warehouse_id != warehouse_id:
        raise ValidationFailed("location_id must be a bin of the POS warehouse", field="location_id")
    if loc.location_type != "INTERNAL":
        raise ValidationFailed("location_id must be an INTERNAL bin", field="location_id")


def _range(uow: UnitOfWork, company_id: uuid.UUID, range_id: uuid.UUID | None) -> None:
    if range_id is None:
        return
    ok = Sequence.objects.filter(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, id=range_id, sequence_code="POS", is_fiscal=True
    ).exists()
    if not ok:
        raise ValidationFailed(
            "numbering_range_id must be a fiscal DEE POS range of the company", field="numbering_range_id"
        )


def _tenders(raw: Any) -> list[str]:
    if raw in (None, ""):
        return list(TENDER_TYPES)
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("allowed_tenders must be a non-empty list", field="allowed_tenders")
    out: list[str] = []
    for t in raw:
        if t not in TENDER_TYPES:
            raise ValidationFailed(
                f"allowed_tenders: {t!r} is not one of {', '.join(TENDER_TYPES)}", field="allowed_tenders"
            )
        if t not in out:
            out.append(str(t))
    return out


def _apply(uow: UnitOfWork, cfg: PosConfig, data: Mapping[str, Any]) -> None:
    ctx, company = uow.ctx, cfg.company_id
    if "name" in data:
        name = text_in(data["name"], "name", 200).strip()
        if not name:
            raise ValidationFailed("name is required", field="name")
        cfg.name = name
    if "branch_id" in data or cfg._state.adding:
        branch = uuid_req(data.get("branch_id", cfg.branch_id if not cfg._state.adding else None), "branch_id")
        validate_branch_link(ctx, company, branch)
        cfg.branch_id = branch
    if "warehouse_id" in data or cfg._state.adding:
        cfg.warehouse_id = uuid_req(
            data.get("warehouse_id", cfg.warehouse_id if not cfg._state.adding else None), "warehouse_id"
        )
    if any(k in data for k in ("branch_id", "warehouse_id")) or cfg._state.adding:
        _warehouse(uow, company, cfg.warehouse_id, cfg.branch_id)
    if "location_id" in data:
        cfg.location_id = uuid_in(data["location_id"], "location_id", required=False)
    if any(k in data for k in ("location_id", "warehouse_id")):
        _location(company, cfg.warehouse_id, cfg.location_id)
    for key in ("price_list_id", "numbering_range_id", "default_party_id"):
        if key in data:
            setattr(cfg, key, uuid_in(data[key], key, required=False))
    if "numbering_range_id" in data:
        _range(uow, company, cfg.numbering_range_id)
    if "allowed_tenders" in data or cfg._state.adding:
        cfg.allowed_tenders = _tenders(data.get("allowed_tenders"))
    if "receipt_profile" in data:
        if not isinstance(data["receipt_profile"], Mapping):
            raise ValidationFailed("receipt_profile must be an object", field="receipt_profile")
        cfg.receipt_profile = dict(data["receipt_profile"])
    if "cash_limit" in data:
        raw = data["cash_limit"]
        cfg.cash_limit = None if raw in (None, "") else decimal_in(raw, "cash_limit", minimum=Decimal(0))
    for key in ("active", "allow_returns", "blind_count"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ValidationFailed(f"{key} must be a boolean", field=key)
            setattr(cfg, key, data[key])


def create_config(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PosConfig:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.config.create", {"company_id": company_id})
    unknown_keys(data, CONFIG_FIELDS)
    cfg = PosConfig(
        tenant_id=ctx.tenant_id, company_id=company_id, created_by=ctx.principal_id, updated_by=ctx.principal_id
    )
    _apply(uow, cfg, data)
    if PosConfig.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, name=cfg.name).exists():
        raise ValidationFailed("a POS configuration with this name already exists", field="name")
    cfg.save()
    audit(uow, "pos.config.create", "pos.config", cfg.id, company_id, {"branch_id": str(cfg.branch_id)})
    return cfg


def update_config(uow: UnitOfWork, cfg: PosConfig, data: Mapping[str, Any]) -> PosConfig:
    ctx = uow.ctx
    require(ctx, "pos.config.update", cfg)
    unknown_keys(data, CONFIG_FIELDS)
    if not data:
        raise ValidationFailed("empty patch")
    cfg = lock(PosConfig.objects.filter(tenant_id=ctx.tenant_id), cfg.id)
    _apply(uow, cfg, data)
    cfg.version += 1
    cfg.updated_by, cfg.updated_at = ctx.principal_id, clock.now()
    cfg.save()
    audit(uow, "pos.config.update", "pos.config", cfg.id, cfg.company_id, {"fields": sorted(data)})
    return cfg


def delete_config(uow: UnitOfWork, cfg: PosConfig) -> None:
    require(uow.ctx, "pos.config.delete", cfg)
    if cfg.terminals.exists() or cfg.sessions.exists():
        raise ValidationFailed("a POS configuration in use cannot be deleted (deactivate it)")
    audit(uow, "pos.config.delete", "pos.config", cfg.id, cfg.company_id, {})
    cfg.delete()


# --------------------------------------------------------------------------- terminals

TERMINAL_FIELDS = ("code", "name", "config_id")


def _config_of(uow: UnitOfWork, company_id: uuid.UUID, raw: Any) -> PosConfig:
    cfg = PosConfig.objects.filter(
        tenant_id=uow.ctx.tenant_id, company_id=company_id, id=uuid_req(raw, "config_id")
    ).first()
    if cfg is None:
        raise ValidationFailed("config_id is not a POS configuration of the company", field="config_id")
    return cfg


def create_terminal(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Terminal:
    ctx = uow.ctx
    ctx.require_company(company_id)
    require(ctx, "pos.terminal.create", {"company_id": company_id})
    unknown_keys(data, TERMINAL_FIELDS)
    code = text_in(data.get("code"), "code", 40).strip()
    name = text_in(data.get("name"), "name", 200).strip()
    if not code or not name:
        raise ValidationFailed("code and name are required", fields=["code", "name"])
    if Terminal.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).exists():
        raise ValidationFailed("a terminal with this code already exists", field="code")
    term = Terminal.objects.create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=name,
        config=_config_of(uow, company_id, data.get("config_id")),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    audit(uow, "pos.terminal.create", "pos.terminal", term.id, company_id, {"code": code})
    return term


def update_terminal(uow: UnitOfWork, term: Terminal, data: Mapping[str, Any]) -> Terminal:
    ctx = uow.ctx
    require(ctx, "pos.terminal.update", term)
    unknown_keys(data, TERMINAL_FIELDS)
    term = lock(Terminal.objects.filter(tenant_id=ctx.tenant_id), term.id)
    if "code" in data:
        term.code = text_in(data["code"], "code", 40).strip()
    if "name" in data:
        term.name = text_in(data["name"], "name", 200).strip()
    if "config_id" in data:
        if term.sessions.exclude(status="CLOSED").exists():
            raise ValidationFailed("close the open session before moving the terminal", field="config_id")
        term.config = _config_of(uow, term.company_id, data["config_id"])
    term.version += 1
    term.updated_by, term.updated_at = ctx.principal_id, clock.now()
    term.save()
    audit(uow, "pos.terminal.update", "pos.terminal", term.id, term.company_id, {"fields": sorted(data)})
    return term


def delete_terminal(uow: UnitOfWork, term: Terminal) -> None:
    require(uow.ctx, "pos.terminal.delete", term)
    if term.sessions.exists():
        raise ValidationFailed("a terminal with sessions cannot be deleted")
    term.delete()


def register_terminal(uow: UnitOfWork, term: Terminal, params: Mapping[str, Any]) -> Terminal:
    """``:register``: bind the device key (only its SHA-256 fingerprint is stored; the key itself never is)."""
    ctx = uow.ctx
    require(ctx, "pos.terminal.update", term)
    key = params.get("device_key")
    if not isinstance(key, str) or not 16 <= len(key) <= 512:
        raise ValidationFailed("parameters.device_key must be a text of 16–512 characters", field="device_key")
    term = lock(Terminal.objects.filter(tenant_id=ctx.tenant_id), term.id)
    term.device_fingerprint = hashlib.sha256(key.encode()).hexdigest()
    term.registered_at = clock.now()
    term.version += 1
    term.save()
    audit(uow, "pos.terminal.register", "pos.terminal", term.id, term.company_id, {})
    return term
