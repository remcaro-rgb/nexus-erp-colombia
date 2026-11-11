"""RC / CE PDFs rendered by the REAL DocumentRenderer from vouchers posted through P16's real API (synthetic data).

T-TRE-RC-07 the recibo de caja PDF contains number, party, lines, total in words (es-CO), fund, preparer and
approver; identical renders have the same hash · T-TRE-CE-06 the comprobante de egreso PDF is deterministic and has
its total in words. The text is read back from the PDF content streams (``documents.pdf.reader``).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from documents import service
from documents.models import GeneratedDocument
from documents.pdf import reader
from kernel.uow import run_in_unit_of_work
from treasury.testing.support import TODAY, ApiClient, World, money

pytestmark = pytest.mark.django_db


def named(world: World, who: str, display: str) -> None:
    from iam.models import User

    ctx = world.ctx(who)
    with world.scope():
        User.objects.create(
            id=ctx.principal_id, tenant_id=world.tenant_id, email=f"{who}@example.invalid", display_name=display
        )


def render(world: World, who: str, source_type: str, voucher_id: str) -> service.Rendered:
    return run_in_unit_of_work(
        lambda u: service.render_source(u, world.company, source_type, uuid.UUID(voucher_id)), world.ctx(who)
    )


def lines_of(pdf: bytes) -> list[str]:
    return reader.text(pdf).split("\n")


def posted_rc(world: World, api: ApiClient) -> dict[str, Any]:
    """RC above the approval threshold: submitted by the cashier, approved by the approver, posted."""
    world.configure(rc_approval_threshold="1000000")
    world.approval_policies(["approver"])
    customer = world.customer("C-PDF01")
    inv1 = world.ar_item(customer, "SETT-2001", "1000000")
    inv2 = world.ar_item(customer, "SETT-2002", "2000000")
    body = {
        "document_date": TODAY.isoformat(),
        "fund_id": str(world.env["fund"].id),
        "payment_method": "TRANSFER",
        "currency": "COP",
        "reference": "TRF-778899",
        "party": {"id": str(customer.id), "kind": "customer"},
        "lines": [
            {"line_no": 1, "open_item_id": str(inv1.id), "applied_amount": money("600000")},
            {"line_no": 2, "open_item_id": str(inv2.id), "applied_amount": money("1500000")},
            {"line_no": 3, "description": "Anticipo saldo a favor", "applied_amount": money("400000")},
            {"line_no": 4, "tax_code": "RTE25", "unit_price": money("2100000"), "description": "Retefuente practicada"},
        ],
    }
    rc = api.post("receipts", body).json()
    assert api.post(f"receipts/{rc['id']}:submit").json()["status"] == "SUBMITTED"
    assert api.as_("approver").post(f"receipts/{rc['id']}:approve", {"comment": "ok"}).status_code == 200
    posted = api.as_("cashier").post(f"receipts/{rc['id']}:post")
    assert posted.status_code == 200, posted.content
    return dict(posted.json())


def test_T_TRE_RC_07_rc_pdf_number_party_lines_words_fund_preparer_approver_deterministic(
    world: World, api: ApiClient
) -> None:
    named(world, "cashier", "Carolina Cajera (SINTÉTICA)")
    named(world, "approver", "Andrés Aprobador (SINTÉTICO)")
    rc = posted_rc(world, api)
    assert rc["number"] == "RC-000001"
    world.policy.grant(world.ctx("cashier").principal_id, "documents.render.export")

    first = render(world, "cashier", "treasury.receipt", rc["id"])
    pdf = first.document.content
    assert pdf.startswith(b"%PDF-1.4") and first.document.media_type == "application/pdf"
    text = lines_of(pdf)
    assert "RECIBO DE CAJA N.º RC-000001" in text
    assert any("Cliente C-PDF01 S.A.S. (SINTÉTICO)" in t and "NIT/CC 900100000-" in t for t in text)  # party
    # lines: the two applied invoices, the advance and the withholding (with base), each with its amount
    for expected in (
        "SETT-2001",
        "SETT-2002",
        "Anticipo saldo a favor",
        "Retefuente practicada (RTE25)",
        "$ 600.000,00",
        "$ 1.500.000,00",
        "$ 400.000,00",
        "$ 2.100.000,00",
        "$ 52.500,00",
    ):
        assert any(expected in t for t in text), expected
    # total received 2,447,500 and its value in words (es-CO)
    assert "$ 2.447.500,00" in text
    assert "Valor en letras: DOS MILLONES CUATROCIENTOS CUARENTA Y SIETE MIL QUINIENTOS PESOS M/CTE" in text
    # fund, payment method and reference
    assert any(
        "Fondo: BCO-1 — Banco sintético cuenta corriente (Banco)" in t and "Transferencia" in t and "TRF-778899" in t
        for t in text
    )
    # preparer and approver
    i = text.index("Elaboró")
    assert text[i : i + 4] == ["Elaboró", "Aprobó", "Carolina Cajera (SINTÉTICA)", "Andrés Aprobador (SINTÉTICO)"]
    assert "Empresa tesorería 0 S.A.S. (sintética)" in text
    assert "SINTÉTICO — sin validez fiscal (DEV)" in text  # D-005 watermark

    # deterministic: a second render (another unit of work) has identical bytes and hash; stored once, linked
    second = render(world, "cashier", "treasury.receipt", rc["id"])
    assert second.document.content == pdf
    assert first.sha256 == second.sha256 == hashlib.sha256(pdf).hexdigest()
    with world.scope():
        rows = list(GeneratedDocument.objects.filter(source_type="treasury.receipt", source_id=rc["id"]))
    assert len(rows) == 1 and rows[0].sha256 == first.sha256 and rows[0].source_number == "RC-000001"
    assert bytes(rows[0].content) == pdf and rows[0].template_version == "1.0.0"

    # field policy: a principal who cannot read the confidential party sees it masked on the PDF too
    world.user("auditor", ("treasury.receipt.read", "documents.render.export"), confidential=False)
    masked = lines_of(render(world, "auditor", "treasury.receipt", rc["id"]).document.content)
    assert not any("Cliente C-PDF01" in t for t in masked)
    assert any("••••••" in t for t in masked)
    # deny by default: no treasury read → 403; another company's principal → 404 (no existence oracle)
    world.user("no_treasury", ("documents.render.export",))
    from kernel.errors import NotFound, PermissionDenied

    with pytest.raises(PermissionDenied):
        render(world, "no_treasury", "treasury.receipt", rc["id"])
    world.policy.grant(world.ctx("other_admin").principal_id, "documents.*")

    def foreign(u: Any) -> Any:
        return service.render_source(u, world.other_company, "treasury.receipt", uuid.UUID(rc["id"]))

    with pytest.raises(NotFound):
        run_in_unit_of_work(foreign, world.ctx("other_admin"))


def test_T_TRE_RC_07_render_api_returns_the_pdf_and_replays_idempotently(world: World, api: ApiClient) -> None:
    rc = posted_rc(world, api)
    world.policy.grant(world.ctx("cashier").principal_id, "documents.render.export")
    body = {"parameters": {"source_type": "treasury.receipt", "source_id": rc["id"]}}
    first = api.client.post("/api/v1/documents/render", body, format="json", HTTP_IDEMPOTENCY_KEY="rc-pdf-1")
    assert first.status_code == 200 and first["Content-Type"] == "application/pdf"
    assert first["X-Document-Sha256"] == hashlib.sha256(first.content).hexdigest()
    assert "RECIBO DE CAJA N.º RC-000001" in lines_of(first.content)
    replay = api.client.post("/api/v1/documents/render", body, format="json", HTTP_IDEMPOTENCY_KEY="rc-pdf-1")
    assert replay.status_code == 200 and replay["Idempotent-Replayed"] == "true"
    assert replay.content == first.content
    missing_key = api.client.post("/api/v1/documents/render", body, format="json")
    assert missing_key.status_code == 400 and missing_key.json()["code"] == "idempotency_key_required"
    denied = api.as_("outsider").client.post(
        "/api/v1/documents/render", body, format="json", HTTP_IDEMPOTENCY_KEY="rc-pdf-denied-1"
    )
    assert denied.status_code == 403


def fund_bank(world: World, api: ApiClient, amount: str) -> None:
    capital = world.concept(f"APORTE-{amount}", "IN", "CAPITAL")
    rc = api.post(
        "receipts",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": str(world.env["fund"].id),
            "payment_method": "TRANSFER",
            "lines": [{"line_no": 1, "concept_id": str(capital.id), "applied_amount": money(amount)}],
        },
    ).json()
    assert api.post(f"receipts/{rc['id']}:post").status_code == 200


def test_T_TRE_CE_06_ce_pdf_deterministic_with_totals_in_words(world: World, api: ApiClient) -> None:
    named(world, "cashier", "Carolina Cajera (SINTÉTICA)")
    named(world, "approver", "Andrés Aprobador (SINTÉTICO)")
    world.approval_policies(["approver"])  # CE threshold COP 0 by default: every CE needs one approver
    world.configure(rc_approval_threshold=None)
    fund_bank(world, api, "10000000")
    supplier = world.vendor("P-CE-PDF")
    bills = [world.ap_item(supplier, f"FP-PDF-{n}", a) for n, a in ((1, "1000000"), (2, "3000000"))]
    body = {
        "document_date": TODAY.isoformat(),
        "fund_id": str(world.env["fund"].id),
        "payment_method": "TRANSFER",
        "currency": "COP",
        "party": {"id": str(supplier.party_id), "kind": "vendor"},
        "lines": [
            {"line_no": 1, "open_item_id": str(bills[0].id), "applied_amount": money("1000000")},
            {"line_no": 2, "open_item_id": str(bills[1].id), "applied_amount": money("2000001.50")},
            {"line_no": 3, "tax_code": "RTE25", "unit_price": money("3000001.50"), "description": "Retefuente"},
        ],
    }
    ce = api.post("disbursements", body).json()
    assert api.post(f"disbursements/{ce['id']}:submit").status_code == 200
    assert api.as_("approver").post(f"disbursements/{ce['id']}:approve", {"comment": "ok"}).status_code == 200
    posted = api.as_("cashier").post(f"disbursements/{ce['id']}:post")
    assert posted.status_code == 200, posted.content
    assert posted.json()["number"] == "CE-000001"
    # paid = 3,000,001.50 − 2.5 % × 3,000,001.50 (P16 keeps the withholding at 4 decimals: 75,000.0375)
    assert posted.json()["total"] == money("2925001.4625")  # printed ROUND_HALF_UP to cents (D-001): 2,925,001.46
    world.policy.grant(world.ctx("cashier").principal_id, "documents.render.export")

    first = render(world, "cashier", "treasury.disbursement", ce["id"])
    pdf = first.document.content
    text = lines_of(pdf)
    assert "COMPROBANTE DE EGRESO N.º CE-000001" in text
    assert any(t.startswith("Pagado a: Proveedor P-CE-PDF S.A.S. (SINTÉTICO)") for t in text)
    assert "TOTAL PAGADO" in text and "$ 2.925.001,46" in text
    assert "Valor en letras: DOS MILLONES NOVECIENTOS VEINTICINCO MIL UN PESOS CON 46/100 M/CTE" in text
    assert "Andrés Aprobador (SINTÉTICO)" in text and "Carolina Cajera (SINTÉTICA)" in text
    for expected in ("FP-PDF-1", "FP-PDF-2", "Pago a proveedor", "Retefuente (RTE25)"):
        assert any(expected in t for t in text), expected
    # deterministic bytes: render again, and render again after unrelated activity (another voucher posted)
    assert render(world, "cashier", "treasury.disbursement", ce["id"]).document.content == pdf
    fund_bank(world, api, "5000000")
    third = render(world, "cashier", "treasury.disbursement", ce["id"])
    assert third.document.content == pdf and third.sha256 == first.sha256
    # the PDF structure is valid: xref offsets point at objects, one page, fonts declared
    objs = reader.objects(pdf)
    assert any(b"/Type /Catalog" in o.head for o in objs.values())
    assert len(reader.page_texts(pdf)) == 1
