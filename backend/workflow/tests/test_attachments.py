"""T-P07-11 signed URL expiry and tenant isolation of files."""

from __future__ import annotations

import datetime as dt

import pytest

from kernel import clock, ids
from kernel.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from kernel.testing.tenancy import make_context
from workflow.attachments.service import EICAR, AttachmentService
from workflow.attachments.store import LocalFileStore, safe_name

pytestmark = pytest.mark.django_db
PERMS = ("workflow.attachment.create", "workflow.attachment.read")


@pytest.fixture
def store(tmp_path):  # type: ignore[no-untyped-def]
    return LocalFileStore(tmp_path, b"k" * 32)


def test_T_P07_11_signed_url_expires(user_factory, company, store) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(*PERMS)
    svc = AttachmentService(store)
    att = svc.upload(
        ctx, company, "purchasing.purchase_order", ids.new_id(), "cotización 1.pdf", b"%PDF-1.4 x", "application/pdf"
    )
    assert att.scan_status == "CLEAN" and att.object_key.startswith(f"t/{ctx.tenant_id}/")
    signed = svc.signed_url(ctx, att.id, ttl_seconds=60)
    content, media, _ = svc.download(ctx, att.id, signed.token)
    assert content == b"%PDF-1.4 x" and media == "application/pdf"
    with clock.frozen(clock.now() + dt.timedelta(seconds=61)), pytest.raises(PermissionDenied) as exc:
        svc.download(ctx, att.id, signed.token)
    assert exc.value.code == "signed_url_expired"
    with pytest.raises(PermissionDenied):
        # forged: last two characters changed (never equal to the original, which "00" could be 1/256 of the time)
        svc.download(ctx, att.id, signed.token[:-2] + ("11" if signed.token.endswith("00") else "00"))
    with pytest.raises(ValidationFailed):
        svc.signed_url(ctx, att.id, ttl_seconds=86400)


def test_T_P07_11_tenant_isolation(user_factory, company, store, grants) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(*PERMS)
    svc = AttachmentService(store)
    att = svc.upload(ctx, company, "x.doc", ids.new_id(), "a.txt", b"hola", "text/plain")
    other = make_context(company_ids=[company])  # another tenant, same company id value
    grants.grant(other.principal_id, *PERMS)
    with pytest.raises(NotFound):
        svc.signed_url(other, att.id)
    token = svc.signed_url(ctx, att.id).token
    with pytest.raises(NotFound):
        svc.download(other, att.id, token)
    with pytest.raises(NotFound):
        store.get(other.tenant_id, att.object_key)
    with pytest.raises(NotFound):
        store.get(ctx.tenant_id, f"t/{ctx.tenant_id}/../{other.tenant_id}/x")
    with pytest.raises(PermissionDenied):
        store.resolve_token(other.tenant_id, token)


def test_T_P07_11_scan_legal_hold_and_retention(user_factory, company, store) -> None:  # type: ignore[no-untyped-def]
    ctx = user_factory(*PERMS)
    svc = AttachmentService(store)
    with pytest.raises(ValidationFailed) as exc:
        svc.upload(ctx, company, "x.doc", ids.new_id(), "virus.txt", EICAR, "text/plain")
    assert exc.value.code == "attachment_infected"
    with pytest.raises(ValidationFailed):
        svc.upload(ctx, company, "x.doc", ids.new_id(), "a.exe", b"MZ", "application/x-msdownload")
    att = svc.upload(ctx, company, "x.doc", ids.new_id(), "a.txt", b"hola", "text/plain", retention_years=10)
    with pytest.raises(Conflict):
        svc.delete(ctx, att.id, "limpieza")  # retained 10 years
    svc.set_legal_hold(ctx, att.id, True, "proceso judicial sintético")
    with pytest.raises(Conflict):
        store.delete(ctx.tenant_id, att.object_key)
    assert safe_name("../../etc/passwd") == "passwd" and safe_name("a b?.pdf") == "a_b_.pdf"


def test_T_P07_11_permission_required(user_factory, company, store) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PermissionDenied):
        AttachmentService(store).upload(user_factory(), company, "x", ids.new_id(), "a.txt", b"x", "text/plain")
