"""Persisting format packs (create/import) — always schema-validated first."""

from __future__ import annotations

import uuid
from typing import Any

from kernel.uow import UnitOfWork
from tax.exogena.loader import load_pack, synthetic_pack_data
from tax.exogena.models import ExogenaFormatPack


def store_pack(uow: UnitOfWork, pack_data: dict[str, Any], *, company_id: uuid.UUID | None = None) -> ExogenaFormatPack:
    """Validate (``exogena_pack_invalid``) and store a pack. Imported official packs start PENDING_VERIFICATION."""
    pack = load_pack(pack_data)
    existing = ExogenaFormatPack.objects.filter(
        tenant_id=uow.ctx.tenant_id, code=pack.pack_code, year=pack.year, pack_version=pack.version
    ).first()
    if existing is not None:
        return existing
    row = ExogenaFormatPack.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=pack.pack_code,
        format_code=pack.format_code,
        year=pack.year,
        pack_version=pack.version,
        source=pack.source,
        legal_status=pack.legal_status,
        pack=pack.raw,
        checksum=pack.checksum,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    return row


def ensure_synthetic_pack(uow: UnitOfWork) -> ExogenaFormatPack:
    return store_pack(uow, synthetic_pack_data())
