"""Load parameter seed files (``tax/fixtures/parameters_2026.yaml``) into a tenant.

Seeds enter as APPROVED rows without a legal-review stamp (EG-7 still pending)
and are recorded in the approval log as ``SEED``. Loading is idempotent: an
existing row with the same (code, company, scope_ref, valid_from) is left as is.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from kernel.parameters import ParameterStatus
from kernel.uow import UnitOfWork
from tax.errors import ParameterSourceRequired
from tax.models import Parameter, ParameterApproval, SourceRecord
from tax.parameters import values

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DEFAULT_FILES = ("parameters_2026.yaml",)


def read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return data


def _date(raw: Any) -> dt.date | None:
    if raw is None or raw == "":
        return None
    return raw if isinstance(raw, dt.date) else dt.date.fromisoformat(str(raw))


def _datetime(raw: Any) -> dt.datetime:
    value = raw if isinstance(raw, dt.datetime) else dt.datetime.fromisoformat(str(raw))
    if value.tzinfo is None:
        raise ValueError("accessed_at needs a timezone")
    return value


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


def upsert_sources(uow: UnitOfWork, rows: list[dict[str, Any]]) -> dict[str, SourceRecord]:
    out: dict[str, SourceRecord] = {}
    for row in rows:
        src, _ = SourceRecord.objects.get_or_create(
            tenant_id=uow.ctx.tenant_id,
            register_id=row["register_id"],
            defaults={
                "url": row["url"],
                "title": row["title"][:255],
                "accessed_at": _datetime(row["accessed_at"]),
                "method": row.get("method", ""),
                "excerpt_hash": row.get("excerpt_hash", ""),
                "document_sha256": row.get("document_sha256", ""),
                "created_by": uow.ctx.principal_id,
                "updated_by": uow.ctx.principal_id,
            },
        )
        out[src.register_id] = src
    return out


def load(uow: UnitOfWork, data: dict[str, Any], *, synthetic: bool | None = None) -> int:
    """Load one seed document; returns the number of rows created."""
    is_synthetic = bool(data.get("synthetic", False)) if synthetic is None else synthetic
    sources = upsert_sources(uow, list(data.get("sources", [])))
    sources.update({s.register_id: s for s in SourceRecord.objects.filter(tenant_id=uow.ctx.tenant_id)})
    created = 0
    for row in data.get("parameters", []):
        source = sources.get(str(row.get("source", "")))
        if source is None:
            raise ParameterSourceRequired(f"parameter {row['code']} has no known source", field="source")
        status = ParameterStatus(row["status"])
        value_type = row["value_type"]
        if status is ParameterStatus.UNKNOWN:
            value_type, stored = "unknown", {}
        else:
            stored = values.normalize(value_type, row.get("value"))
        valid_from = _date(row["valid_from"])
        if valid_from is None:
            raise ValueError(f"parameter {row['code']} needs valid_from")
        scope_ref = str(row.get("scope_ref", ""))
        exists = Parameter.objects.filter(
            tenant_id=uow.ctx.tenant_id, code=row["code"], company_id=None, scope_ref=scope_ref, valid_from=valid_from
        ).exists()
        if exists:
            continue
        param = Parameter.objects.create(
            tenant_id=uow.ctx.tenant_id,
            code=row["code"],
            scope=Parameter.Scope.MUNICIPALITY if scope_ref else Parameter.Scope.GLOBAL,
            scope_ref=scope_ref,
            value_type=value_type,
            value=stored,
            valid_from=valid_from,
            valid_to=_date(row.get("valid_to")),
            parameter_status=status.value,
            source=source,
            legal_history=_jsonable(list(row.get("legal_history", []) or [])),
            synthetic=is_synthetic or bool(row.get("synthetic", False)),
            approval=Parameter.Approval.APPROVED,
            proposed_by=uow.ctx.principal_id,
            notes=str(row.get("notes", "")),
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
        ParameterApproval.objects.create(
            tenant_id=uow.ctx.tenant_id,
            parameter=param,
            action="SEED",
            principal_id=uow.ctx.principal_id,
            reason=f"seed {data.get('version', '')}".strip(),
        )
        created += 1
    return created


def load_defaults(uow: UnitOfWork, files: tuple[str, ...] = DEFAULT_FILES) -> int:
    return sum(load(uow, read(FIXTURES / name)) for name in files)
