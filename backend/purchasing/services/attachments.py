"""Quote attachments (F-PUR-CYCLE, T-PUR-CYCLE-06): scanned, stored through ``FileStore``, buyers only.

Upload order (no external I/O inside a transaction): policy check → media type → antivirus scan
(EICAR test signature in DEV; ClamAV plugs in behind :class:`Scanner`) → ``FileStore.put`` → the row
in a unit of work. Infected files are refused and never stored. A failed or replayed command deletes
the object it stored. Downloads require ``purchasing.quote.read`` (buyers) on a quote of the
principal's company; other tenants / companies get ``not_found``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Protocol

from kernel import services, tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.idempotency import StoredResponse
from kernel.interfaces.files import FileStore
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from purchasing.models import QuoteAttachment, VendorQuote
from purchasing.services.common import audit, require

ALLOWED_MEDIA = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
MAX_BYTES = 10 * 1024 * 1024
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class Scanner(Protocol):
    def scan(self, content: bytes) -> str:
        """CLEAN | INFECTED"""
        ...


class SignatureScanner:
    """DEV scanner: the EICAR test signature counts as infected."""

    def scan(self, content: bytes) -> str:
        return "INFECTED" if EICAR in content else "CLEAN"


_scanner: Scanner = SignatureScanner()


def set_scanner(scanner: Scanner) -> None:
    global _scanner
    _scanner = scanner


def _quote(ctx: TenantContext, company_id: uuid.UUID, rfq_id: uuid.UUID, quote_id: uuid.UUID) -> VendorQuote:
    quote = VendorQuote.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, rfq_id=rfq_id, id=quote_id
    ).first()
    if quote is None or company_id not in ctx.company_ids:
        raise NotFound("not found")
    return quote


def add_attachment(
    ctx: TenantContext,
    company_id: uuid.UUID,
    rfq_id: uuid.UUID,
    quote_id: uuid.UUID,
    name: str,
    content: bytes,
    media_type: str,
    *,
    idempotency_key: str,
) -> tuple[QuoteAttachment | dict[str, Any], bool]:
    """Returns ``(attachment, replayed)``; on a replay the stored response body is returned."""
    require(ctx, "purchasing.quote.update", {"company_id": company_id})
    with tenancy.scope(ctx):
        _quote(ctx, company_id, rfq_id, quote_id)
    if media_type not in ALLOWED_MEDIA:
        raise ValidationFailed(f"media type {media_type!r} is not allowed", field="file")
    if not content or len(content) > MAX_BYTES:
        raise ValidationFailed(f"the file must have 1 byte to {MAX_BYTES // (1024 * 1024)} MB", field="file")
    status = _scanner.scan(content)
    if status != "CLEAN":
        raise ValidationFailed("the file failed the antivirus scan", code="attachment_infected", field="file")
    store = services.get(FileStore)
    stored = store.put(ctx.tenant_id, name or "adjunto", content, media_type)

    def work(uow: UnitOfWork) -> QuoteAttachment:
        quote = _quote(ctx, company_id, rfq_id, quote_id)
        att = QuoteAttachment.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            quote=quote,
            name=(name or "adjunto")[:200],
            object_key=stored.key,
            media_type=media_type,
            size=stored.size,
            sha256=stored.sha256,
            scan_status=status,
            created_by=ctx.principal_id,
        )
        audit(
            uow,
            "purchasing.quote.attachment",
            "purchasing.quote",
            quote.id,
            company_id,
            {"attachment": str(att.id), "sha256": att.sha256, "scan": status},
        )
        uow.set_response(to_json(att), 201)
        return att

    body = {"quote": str(quote_id), "sha256": hashlib.sha256(content).hexdigest(), "name": name}
    try:
        result = run_in_unit_of_work(
            work, ctx, idempotency_key=idempotency_key, request=body, scope="purchasing.quote.attachment"
        )
    except Exception:
        store.delete(ctx.tenant_id, stored.key)
        raise
    if isinstance(result, StoredResponse):
        store.delete(ctx.tenant_id, stored.key)
        return dict(result.body or {}), True
    return result, False


def list_attachments(
    ctx: TenantContext, company_id: uuid.UUID, rfq_id: uuid.UUID, quote_id: uuid.UUID
) -> list[QuoteAttachment]:
    require(ctx, "purchasing.quote.read", {"company_id": company_id})
    with tenancy.scope(ctx):
        quote = _quote(ctx, company_id, rfq_id, quote_id)
        return list(QuoteAttachment.objects.filter(quote=quote).order_by("created_at", "id"))


def download(
    ctx: TenantContext, company_id: uuid.UUID, rfq_id: uuid.UUID, quote_id: uuid.UUID, attachment_id: uuid.UUID
) -> tuple[bytes, str, str]:
    """Bytes, media type and name of a clean attachment, for buyers (``purchasing.quote.read``) only."""
    require(ctx, "purchasing.quote.read", {"company_id": company_id})
    with tenancy.scope(ctx):
        quote = _quote(ctx, company_id, rfq_id, quote_id)
        att = QuoteAttachment.objects.filter(quote=quote, id=attachment_id, scan_status="CLEAN").first()
    if att is None:
        raise NotFound("not found")
    content = services.get(FileStore).get(ctx.tenant_id, att.object_key)
    if hashlib.sha256(content).hexdigest() != att.sha256:
        raise NotFound("not found")
    return content, att.media_type, att.name


def to_json(att: QuoteAttachment) -> dict[str, Any]:
    return {
        "id": str(att.id),
        "quote_id": str(att.quote_id),
        "name": att.name,
        "media_type": att.media_type,
        "size": att.size,
        "sha256": att.sha256,
        "scan_status": att.scan_status,
        "created_at": att.created_at.isoformat(),
        "created_by": str(att.created_by),
    }
