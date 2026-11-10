"""JSON shapes of ``contracts/openapi/modules/einvoice.yaml`` (``additionalProperties: false``).

Secret-classified fields (``credentials_ref``, ``technical_key_ref``, ``software_pin_ref``) are write-only:
no serializer here reads them, so they cannot appear in any response (T-PT-OPEN-02).
"""

from __future__ import annotations

from typing import Any

from einvoice import lifecycle
from einvoice.api.base import audit
from einvoice.models import (
    EinvoiceSettings,
    FiscalDocument,
    HabilitationTestSet,
    NumberingRange,
    RadianEvent,
    SigningCertificate,
    TechnologyProvider,
)


def _ids(obj: Any) -> dict[str, Any]:
    return {"id": str(obj.id), "tenant_id": str(obj.tenant_id), "company_id": str(obj.company_id)}


def fiscal_document(doc: FiscalDocument) -> dict[str, Any]:
    return {
        **_ids(doc),
        "status": doc.status,
        "source": {"type": doc.source_type, "id": str(doc.source_id), "number": doc.number[:60]},
        "document_type": doc.family,
        "fiscal_status": lifecycle.FISCAL_STATUS[doc.status],
        "cufe": doc.unique_code or None,
        "synthetic": doc.synthetic,
        "version": doc.version,
        "audit": audit(doc),
    }


def numbering_range(rng: NumberingRange) -> dict[str, Any]:
    return {
        **_ids(rng),
        "code": rng.code,
        "status": rng.status,
        "prefix": rng.prefix,
        "from_number": rng.from_number,
        "to_number": rng.to_number,
        "resolution": rng.resolution,
        "valid_to": rng.valid_to.isoformat(),
        "version": rng.version,
        "audit": audit(rng),
    }


def radian_event(ev: RadianEvent) -> dict[str, Any]:
    return {
        **_ids(ev),
        "status": ev.status,
        "document": {"type": ev.document_type, "id": str(ev.document_id), "number": ev.document_number[:60] or None},
        "event_code": ev.event_code,
        "version": ev.version,
        "audit": audit(ev),
    }


def test_set(ts: HabilitationTestSet) -> dict[str, Any]:
    return {
        **_ids(ts),
        "status": ts.status,
        "test_set_id": ts.test_set_id,
        "results": ts.results,
        "version": ts.version,
        "audit": audit(ts),
    }


def certificate(cert: SigningCertificate) -> dict[str, Any]:
    return {
        **_ids(cert),
        "status": cert.status,
        "subject": cert.subject,
        "valid_to": cert.valid_to.isoformat(),
        "thumbprint": cert.thumbprint,
        "version": cert.version,
        "audit": audit(cert),
    }


def settings(row: EinvoiceSettings) -> dict[str, Any]:
    return {
        **_ids(row),
        "mode": row.mode,
        "adapter": row.adapter,
        "provider_id": str(row.provider_id) if row.provider_id else None,
        "software_id": row.software_id or None,
        "version": row.version,
        "audit": audit(row),
    }


def provider(p: TechnologyProvider) -> dict[str, Any]:
    return {
        **_ids(p),
        "code": p.code,
        "name": p.name,
        "status": p.status,
        "adapter": p.adapter,
        "base_url": p.base_url,
        "environment": p.environment,
        "contract_version": p.contract_version,
        "version": p.version,
        "audit": audit(p),
    }


#: Export columns of ``GET /providers:export`` (configuration without secrets).
PROVIDER_EXPORT_COLUMNS = (
    "code",
    "name",
    "status",
    "config_state",
    "adapter",
    "base_url",
    "environment",
    "contract_version",
    "credentials",
    "last_tested_at",
    "last_test_passed",
)


def provider_export_row(p: TechnologyProvider) -> list[str]:
    return [
        p.code,
        p.name,
        p.status,
        p.config_state,
        p.adapter,
        p.base_url,
        p.environment,
        p.contract_version,
        "configurada (referencia oculta)" if p.credentials_ref else "no configurada",
        p.last_tested_at.isoformat() if p.last_tested_at else "",
        "" if not p.last_test else str(bool(p.last_test.get("passed"))),
    ]
