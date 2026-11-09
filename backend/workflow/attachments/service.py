"""Attachments: upload (scan hook), list, signed download, legal hold and retention."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Protocol

from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import run_in_unit_of_work
from workflow.attachments.store import DEFAULT_TTL_SECONDS, LocalFileStore, SignedUrl
from workflow.audit.service import record_change
from workflow.policy import policy

ALLOWED_MEDIA = {
    "application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain", "text/csv", "application/xml",
    "text/xml", "application/json", "application/zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}  # fmt: skip
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class Scanner(Protocol):
    def scan(self, content: bytes) -> str:
        """CLEAN | INFECTED | SKIPPED"""
        ...


class SignatureScanner:
    """DEV scanner: detects the EICAR test signature; ClamAV ``[A]`` plugs in behind the same hook."""

    def scan(self, content: bytes) -> str:
        return "INFECTED" if EICAR in content else "CLEAN"


class AttachmentService:
    def __init__(self, store: LocalFileStore, scanner: Scanner | None = None) -> None:
        self.store = store
        self.scanner = scanner or SignatureScanner()

    def upload(
        self,
        ctx: TenantContext,
        company_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        name: str,
        content: bytes,
        media_type: str,
        *,
        retention_years: int = 10,
        idempotency_key: str | None = None,
    ) -> Any:
        from workflow.models import Attachment

        ctx.require_company(company_id)
        policy().require(ctx, "workflow.attachment.create", {"resource_type": resource_type, "company_id": company_id})
        if media_type not in ALLOWED_MEDIA:
            raise ValidationFailed(f"media type {media_type!r} is not allowed")
        status = self.scanner.scan(content)
        if status == "INFECTED":
            raise ValidationFailed("the file failed the antivirus scan", code="attachment_infected")
        stored = self.store.put(ctx.tenant_id, name, content, media_type)  # I/O before the transaction

        def work(uow: Any) -> Attachment:
            att = Attachment.objects.create(
                tenant_id=ctx.tenant_id, company_id=company_id, resource_type=resource_type, resource_id=resource_id,
                name=name[:200], object_key=stored.key, media_type=media_type, size=stored.size, sha256=stored.sha256,
                scan_status=status, created_by=ctx.principal_id,
                retention_until=clock.business_date().replace(year=clock.business_date().year + retention_years),
            )  # fmt: skip
            record_change(
                "workflow.attachment.create", "workflow.attachment", att.id, company_id=company_id,
                after={"name": att.name, "resource_type": resource_type, "resource_id": resource_id, "sha256": att.sha256},
            )  # fmt: skip
            return att

        try:
            return run_in_unit_of_work(work, ctx, idempotency_key=idempotency_key)
        except Exception:
            self.store.delete(ctx.tenant_id, stored.key)
            raise

    def _get(self, ctx: TenantContext, attachment_id: uuid.UUID) -> Any:
        from workflow.models import Attachment

        att = Attachment.objects.filter(
            tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=attachment_id, deleted_at__isnull=True
        ).first()
        if att is None:
            raise NotFound("attachment not found")
        return att

    def list(self, ctx: TenantContext, resource_type: str, resource_id: uuid.UUID) -> list[Any]:
        from workflow.models import Attachment

        qs = Attachment.objects.filter(
            tenant_id=ctx.tenant_id, resource_type=resource_type, resource_id=resource_id, deleted_at__isnull=True
        )
        return list(policy().scope_queryset(ctx, "workflow.attachment.read", qs))

    def signed_url(self, ctx: TenantContext, attachment_id: uuid.UUID, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> SignedUrl:
        att = self._get(ctx, attachment_id)
        policy().require(ctx, "workflow.attachment.read", att)
        return self.store.sign(
            ctx.tenant_id, att.object_key, ttl_seconds=ttl_seconds,
            base_url=f"/api/v1/workflow/attachments/{att.id}/content",
        )  # fmt: skip

    def download(self, ctx: TenantContext, attachment_id: uuid.UUID, token: str) -> tuple[bytes, str, str]:
        att = self._get(ctx, attachment_id)
        key = self.store.resolve_token(ctx.tenant_id, token)
        if key != att.object_key:
            raise NotFound("attachment not found")
        return self.store.get(ctx.tenant_id, key), att.media_type, att.name

    def set_legal_hold(self, ctx: TenantContext, attachment_id: uuid.UUID, hold: bool, reason: str) -> Any:
        att = self._get(ctx, attachment_id)
        policy().require(ctx, "workflow.attachment.create", att)

        def work(uow: Any) -> Any:
            before = att.legal_hold
            att.legal_hold = hold
            att.save(update_fields=["legal_hold"])
            record_change("workflow.attachment.legal_hold", "workflow.attachment", att.id, company_id=att.company_id,
                          before={"legal_hold": before}, after={"legal_hold": hold}, reason=reason)
            return att

        result = run_in_unit_of_work(work, ctx)
        self.store.set_legal_hold(ctx.tenant_id, att.object_key, hold)
        return result

    def delete(self, ctx: TenantContext, attachment_id: uuid.UUID, reason: str) -> None:
        att = self._get(ctx, attachment_id)
        policy().require(ctx, "workflow.attachment.create", att)
        if att.legal_hold:
            raise Conflict("the attachment is under legal hold", code="retention_locked")
        if att.retention_until and att.retention_until >= clock.business_date():
            raise Conflict(f"retained until {att.retention_until.isoformat()}", code="retention_locked")

        def work(uow: Any) -> None:
            att.deleted_at = clock.now()
            att.save(update_fields=["deleted_at"])
            record_change("workflow.attachment.delete", "workflow.attachment", att.id, company_id=att.company_id,
                          before={"name": att.name}, after=None, reason=reason)

        run_in_unit_of_work(work, ctx)
        self.store.delete(ctx.tenant_id, att.object_key)


def retention_date(years: int, on: dt.date | None = None) -> dt.date:
    day = on or clock.business_date()
    return day.replace(year=day.year + years)
