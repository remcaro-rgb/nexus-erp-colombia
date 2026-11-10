"""T-PUR-CYCLE-06 (integration): quote attachments are stored, scanned and downloadable by buyers only;
plus the purchasing leak / RLS / field-policy tests for the new read paths."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from kernel.errors import NotFound, PermissionDenied
from kernel.testing.tenancy import bound, make_context
from purchasing.models import QuoteAttachment, Rfq, VendorQuote
from purchasing.services import attachments
from purchasing.testing.support import ApiClient, World
from purchasing.tests.test_rfq_cycle import create_rfq, quote_body, suppliers

PDF = b"%PDF-1.4\n% cotizacion SINTETICA\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def rfq_with_quote(api: ApiClient, world: World) -> tuple[str, str]:
    vendors = suppliers(world)
    rid = create_rfq(api, vendors)["id"]
    api.as_("buyer").post(f"rfqs/{rid}:send")
    qid = api.post(f"rfqs/{rid}/quotes", quote_body(vendors[0], "3000", "4000", lead=5)).json()["id"]
    return rid, qid


def upload(
    api: ApiClient,
    rid: str,
    qid: str,
    content: bytes,
    name: str = "cotizacion.pdf",
    media: str = "application/pdf",
    key: str | None = None,
) -> Any:
    return api.send(
        "post",
        f"rfqs/{rid}/quotes/{qid}/attachments",
        {"file": SimpleUploadedFile(name, content, content_type=media)},
        fmt="multipart",
        key=key,
    )


def test_T_PUR_CYCLE_06_attachments_stored_scanned_and_buyers_only(api: ApiClient, world: World) -> None:
    rid, qid = rfq_with_quote(api, world)
    files = world.fakes.files

    stored = upload(api.as_("buyer"), rid, qid, PDF, key="attach-key-0001")
    assert stored.status_code == 201, stored.content
    att = stored.json()
    assert att["scan_status"] == "CLEAN" and att["size"] == len(PDF) and att["media_type"] == "application/pdf"
    assert len(files.objects) == 1
    # Replaying the same Idempotency-Key returns the stored row; no duplicate object or row is left behind.
    again = upload(api, rid, qid, PDF, key="attach-key-0001")
    assert again.status_code == 201 and again["Idempotent-Replayed"] == "true" and again.json()["id"] == att["id"]
    assert len(files.objects) == 1

    # The EICAR test signature is refused and never stored.
    infected = upload(api, rid, qid, b"prefix " + attachments.EICAR + b" suffix", name="virus.txt", media="text/plain")
    assert infected.status_code == 422 and infected.json()["code"] == "attachment_infected"
    bad_type = upload(api, rid, qid, b"MZ...", name="x.exe", media="application/x-msdownload")
    assert bad_type.status_code == 422
    assert len(files.objects) == 1
    with bound(world.ctx("buyer")):
        assert QuoteAttachment.objects.filter(quote_id=qid).count() == 1

    # Buyers list and download it.
    listed = api.as_("buyer").get(f"rfqs/{rid}/quotes/{qid}/attachments").json()
    assert [a["id"] for a in listed["items"]] == [att["id"]]
    content, media, name = attachments.download(
        world.ctx("buyer"), world.company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"])
    )
    assert content == PDF and media == "application/pdf" and name == "cotizacion.pdf"

    # Non-buyers (warehouse user, outsider) are denied; buyers of another company / tenant get not_found.
    for who in ("warehouse", "outsider"):
        assert api.as_(who).get(f"rfqs/{rid}/quotes/{qid}/attachments").status_code == 403
        with pytest.raises(PermissionDenied):
            attachments.download(world.ctx(who), world.company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"]))
        assert upload(api.as_(who), rid, qid, PDF).status_code == 403
    other = world.ctx("other_buyer")
    with pytest.raises(Exception) as exc:
        attachments.download(other, world.company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"]))
    assert getattr(exc.value, "code", "") in ("not_found", "validation_failed", "policy_denied")
    with pytest.raises(NotFound):
        attachments.download(other, world.other_company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"]))
    foreign = make_context(company_ids=[world.company])  # another tenant with the same company id
    world.fakes.policy.grant(foreign.principal_id, "purchasing.quote.read")
    with pytest.raises(NotFound):
        attachments.download(foreign, world.company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"]))


def test_T_PUR_CYCLE_06_tampered_object_is_not_served(api: ApiClient, world: World) -> None:
    rid, qid = rfq_with_quote(api, world)
    att = upload(api.as_("buyer"), rid, qid, PDF).json()
    key = next(iter(world.fakes.files.objects))
    world.fakes.files.objects[key] = (b"tampered", "application/pdf")
    with pytest.raises(NotFound):
        attachments.download(world.ctx("buyer"), world.company, uuid.UUID(rid), uuid.UUID(qid), uuid.UUID(att["id"]))


# --------------------------------------------------------------------------- leak / RLS / field policy


def test_purchasing_reads_never_leak_across_company_or_tenant(api: ApiClient, world: World) -> None:
    rid, qid = rfq_with_quote(api, world)
    # Other company of the same tenant: list is empty, detail is 404 (no enumeration oracle).
    other = api.as_("other_buyer")
    assert other.get("rfqs").json()["items"] == []
    assert other.get(f"rfqs/{rid}").status_code in (404, 422)
    assert other.get(f"rfqs/{rid}/quotes/{qid}").status_code in (404, 422)
    # A principal without grants gets 403 on lists (deny by default).
    assert api.as_("outsider").get("rfqs").status_code == 403
    assert api.as_("outsider").get("orders").status_code == 403
    # Forced RLS: a session bound to another tenant sees no purchasing rows at all; every table is covered.
    from iam.rls import tables_without_rls

    assert tables_without_rls("purchasing_") == []
    with bound(make_context(company_ids=[world.company])):
        assert Rfq.objects.count() == 0 and VendorQuote.objects.count() == 0
    with bound(world.ctx("buyer")):
        assert Rfq.objects.count() == 1


def test_confidential_quote_fields_follow_the_field_policy(api: ApiClient, world: World) -> None:
    rid, qid = rfq_with_quote(api, world)
    full = api.as_("buyer").get(f"rfqs/{rid}/quotes/{qid}").json()
    assert "landed_total" in full and full["party"]["kind"] == "vendor"
    limited = api.as_("buyer_no_confidential").get(f"rfqs/{rid}/quotes/{qid}").json()
    assert "landed_total" not in limited and "party" not in limited
    listed = api.get(f"rfqs/{rid}/quotes").json()["items"]
    assert all("landed_total" not in q and "party" not in q for q in listed)


def test_writes_need_an_idempotency_key_and_if_match(api: ApiClient, world: World) -> None:
    vendors = suppliers(world)
    body = {
        "document_date": "2026-11-09",
        "deadline": (dt.datetime(2026, 11, 20, tzinfo=dt.UTC)).isoformat(),
        "invited_vendors": [str(vendors[0].id)],
        "lines": [{"line_no": 1, "quantity": {"value": "1", "uom": "UND"}}],
    }
    no_key = api.as_("buyer").client.post("/api/v1/purchasing/rfqs", body, format="json")
    assert no_key.status_code == 400 and no_key.json()["code"] == "idempotency_key_required"
    created = api.post("rfqs", body, key="rfq-create-0001")
    replay = api.post("rfqs", body, key="rfq-create-0001")
    assert created.status_code == replay.status_code == 201 and replay["Idempotent-Replayed"] == "true"
    assert created.json()["id"] == replay.json()["id"]
    rid = created.json()["id"]
    assert api.send("patch", f"rfqs/{rid}", {"notes": "x"}).status_code == 428
    stale = api.send("patch", f"rfqs/{rid}", {"notes": "x"}, if_match='W/"stale"')
    assert stale.status_code == 412
    etag = api.get(f"rfqs/{rid}")["ETag"]
    ok = api.send("patch", f"rfqs/{rid}", {"notes": "Notas (SINTÉTICO)"}, if_match=etag)
    assert ok.status_code == 200 and ok.json()["notes"] == "Notas (SINTÉTICO)" and ok.json()["version"] == 2
    floats = api.send(
        "patch",
        f"rfqs/{rid}",
        {"scoring_weights": {"price": 60.0, "lead_time": 20, "rating": 10, "payment_terms": 10}},
        if_match=ok["ETag"],
    )
    assert floats.status_code == 422


def test_comparison_snapshots_and_posted_documents_are_immutable_in_the_database(api: ApiClient, world: World) -> None:
    from django.db import DatabaseError, transaction

    from purchasing.models import QuoteComparison
    from purchasing.testing.support import advance
    from purchasing.tests.test_rfq_cycle import DEADLINE

    rid, _qid = rfq_with_quote(api, world)
    advance(DEADLINE + dt.timedelta(minutes=1))
    api.as_("buyer").post(f"rfqs/{rid}:close")
    assert api.post(f"rfqs/{rid}:evaluate").status_code == 200
    with bound(world.ctx("buyer")):
        snap = QuoteComparison.objects.get(rfq_id=rid)
        with pytest.raises(DatabaseError), transaction.atomic():
            QuoteComparison.objects.filter(id=snap.id).update(explanation="alterada")
        with pytest.raises(DatabaseError), transaction.atomic():
            QuoteComparison.objects.filter(id=snap.id).delete()
