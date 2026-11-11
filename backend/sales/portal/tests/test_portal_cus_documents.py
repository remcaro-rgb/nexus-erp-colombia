"""F-PORTAL-CUS: T-PORTAL-CUS-01 (own documents only; statement = AR subledger) and T-PORTAL-CUS-05 (synthetic
fiscal PDF/XML carry the 'SINTÉTICO — sin validez fiscal' label). Real ledger, stock, policy engine, portal tokens."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from django.db.models import Sum

from sales.models import ArOpenItem
from sales.portal.service import FISCAL_LABEL
from sales.portal.tests.kit import TODAY, C, P10bWorld, S, ok

pytestmark = pytest.mark.django_db

CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


def _setup(w: P10bWorld, fakes: dict[str, Any]) -> dict[str, Any]:
    w.item("A", "1000", cost="123.4567")
    a = w.customer("CA", "900100001")
    b = w.customer("CB", "900100002")
    inv1 = w.sell(a, [("A", "2")])  # 2 × 1000 + IVA 19 % = 2380
    inv2 = w.sell(a, [("A", "1")])  # 1190
    inv_b = w.sell(b, [("A", "3")])  # 3570
    note = w.credit_note(inv1, "1")  # 1190 against inv1
    with w.scope():
        items = {i.document_id.hex: i for i in ArOpenItem.objects.filter(party_id__in=[a.id, b.id])}
    item2 = items[inv2["id"].replace("-", "")]
    item_b = items[inv_b["id"].replace("-", "")]
    rc_a = w.receipt(a, str(item2.id), "1190.00")
    rc_b = w.receipt(b, str(item_b.id), "1000.00")
    note_b = w.credit_note(inv_b, "1")
    return {
        "a": a,
        "b": b,
        "inv1": inv1,
        "inv2": inv2,
        "inv_b": inv_b,
        "note": note,
        "note_b": note_b,
        "rc_a": rc_a,
        "rc_b": rc_b,
        "api_a": w.onboard(a, "cli-a", fakes["notifications"]),
        "api_b": w.onboard(b, "cli-b", fakes["notifications"]),
    }


def test_T_PORTAL_CUS_01_customer_sees_only_own_documents_and_statement_equals_ar_subledger(
    w: P10bWorld, fakes: dict[str, Any]
) -> None:
    env = _setup(w, fakes)
    api_a, api_b = env["api_a"], env["api_b"]

    # lists: only the customer's own posted invoices / credit notes / receipts
    invoices = ok(api_a.get(f"{C}/invoices"))["items"]
    assert {i["id"] for i in invoices} == {env["inv1"]["id"], env["inv2"]["id"]}
    assert all(i["status"] == "POSTED" and i["number"].startswith("SETT") for i in invoices)
    notes = ok(api_a.get(f"{C}/credit-notes"))["items"]
    assert [n["id"] for n in notes] == [env["note"]["id"]]
    receipts = ok(api_a.get(f"{C}/receipts"))["items"]
    assert [r["id"] for r in receipts] == [env["rc_a"]["id"]]
    assert {i["id"] for i in ok(api_b.get(f"{C}/invoices"))["items"]} == {env["inv_b"]["id"]}

    # details of the other customer's documents: 404 (same answer as a missing id, no existence oracle)
    for path in (
        f"invoices/{env['inv_b']['id']}",
        f"credit-notes/{env['note_b']['id']}",
        f"receipts/{env['rc_b']['id']}",
        f"orders/{env['inv_b']['order_id']}",
        f"quotes/{env['inv_b']['quote_id']}",
        "invoices/00000000-0000-0000-0000-000000000000",
        "invoices/not-a-uuid",
    ):
        r = api_a.get(f"{C}/{path}")
        assert r.status_code == 404, (path, r.content)
        assert r.json()["code"] in ("portal_document_not_owned", "not_found")
    assert ok(api_a.get(f"{C}/invoices/{env['inv1']['id']}"))["total"] == {"amount": "2380.0000", "currency": "COP"}

    # scope parameters naming another party / company are refused, whatever the endpoint
    for path, params in (
        ("invoices", {"party_id": str(env["b"].id)}),
        ("statement", {"party": str(env["b"].id)}),
        ("receipts", {"company_id": str(w.company("BETA"))}),
    ):
        r = api_a.get(f"{C}/{path}", **params)
        assert r.status_code == 403 and r.json()["code"] == "external_account_scope_violation", r.content
    # internal (erp-web) tokens are not accepted by the portal API; portal tokens not by the internal API
    assert w.api("vendedor").get(f"{C}/invoices").status_code == 401
    assert api_a.get(f"{S}/invoices").status_code == 401

    # statement: rows are the customer's own movements; the closing balance equals the AR subledger
    st = ok(api_a.get(f"{C}/statement", **{"from": "2026-01-01", "to": TODAY.isoformat()}))
    with w.scope():
        subledger = ArOpenItem.objects.filter(party_id=env["a"].id).aggregate(s=Sum("balance_cop"))["s"]
        other = ArOpenItem.objects.filter(party_id=env["b"].id).aggregate(s=Sum("balance_cop"))["s"]
    assert subledger == Decimal("1190.0000") and other != subledger
    totals = st["totals"]
    assert Decimal(totals["closing_balance"]) == subledger
    assert Decimal(totals["open_balance"]) == subledger
    assert Decimal(totals["opening_balance"]) == 0
    assert Decimal(totals["debit"]) == Decimal("3570") and Decimal(totals["credit"]) == Decimal("2380")
    assert Decimal(totals["debit"]) - Decimal(totals["credit"]) == subledger
    numbers = {r["number"] for r in st["rows"]}
    assert numbers == {env["inv1"]["number"], env["inv2"]["number"], env["note"]["number"], env["rc_a"]["number"]}
    assert env["inv_b"]["number"] not in str(st) and str(env["b"].id) not in str(st)
    # the same figure as the internal P10a statement of that customer
    internal = ok(
        w.api("vendedor").get(
            f"{S}/ar/statements", party_id=str(env["a"].id), date_from="2026-01-01", date_to=TODAY.isoformat()
        )
    )
    assert Decimal(internal["closing_balance"]) == Decimal(totals["closing_balance"])
    # the statement of B equals B's subledger
    st_b = ok(api_b.get(f"{C}/statement"))
    assert Decimal(st_b["totals"]["closing_balance"]) == other

    # CSV export: same closing balance, labelled
    csv_body = api_a.get(f"{C}/statement", format="csv").content.decode("utf-8-sig")
    assert FISCAL_LABEL in csv_body and totals["closing_balance"] in csv_body

    # dashboard KPIs of the party only
    kpis = {k["id"]: k for k in ok(api_a.get(f"{C}/kpis", period="2026-11"))["kpis"]}
    assert Decimal(kpis["cp_saldo_pendiente"]["value"]) == subledger
    assert kpis["cp_facturas_vencidas"]["value"] == "0"
    assert Decimal(kpis["cp_pagos_periodo"]["value"]) == Decimal("1190")
    assert Decimal(kpis["cp_pagos_periodo"]["previous"]) == 0
    kpis_b = {k["id"]: k for k in ok(api_b.get(f"{C}/kpis"))["kpis"]}
    assert Decimal(kpis_b["cp_saldo_pendiente"]["value"]) == other

    # profile: the linked party only
    prof = ok(api_a.get(f"{C}/profile"))
    assert prof["id"] == str(env["a"].id) and prof["legal_name"].startswith("Cliente CA")
    assert set(prof) <= {"id", "tenant_id", "legal_name", "nit", "email", "version", "audit"}


def _label_in_pdf(content: bytes) -> bool:
    return FISCAL_LABEL.encode("cp1252") in content or FISCAL_LABEL.encode("utf-8") in content


def test_T_PORTAL_CUS_05_fiscal_pdf_and_xml_downloads_carry_the_synthetic_label(
    w: P10bWorld, fakes: dict[str, Any]
) -> None:
    env = _setup(w, fakes)
    api_a = env["api_a"]
    inv = env["inv1"]

    pdf = api_a.get(f"{C}/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
    assert _label_in_pdf(pdf.content)
    assert inv["number"].encode() in pdf.content
    assert "SINTETICO" in pdf["Content-Disposition"] and pdf["Cache-Control"] == "no-store"

    xml = api_a.get(f"{C}/invoices/{inv['id']}/xml")
    assert xml.status_code == 200 and xml["Content-Type"] == "application/xml"
    text = xml.content.decode("utf-8")
    assert f"<!-- {FISCAL_LABEL}" in text
    root = ET.fromstring(xml.content)  # noqa: S314 - our own synthetic document
    notes = [n.text for n in root.iter(f"{CBC}Note")]
    assert FISCAL_LABEL in notes
    assert root.find(f"{CBC}ID") is not None and root.find(f"{CBC}ID").text == inv["number"]  # type: ignore[union-attr]
    assert "SINTÉTICO" in (root.find(f"{CBC}UUID").text or "")  # type: ignore[union-attr]

    for media in ("pdf", "xml"):
        note = api_a.get(f"{C}/credit-notes/{env['note']['id']}/{media}")
        assert note.status_code == 200, note.content
        body = note.content
        assert _label_in_pdf(body) if media == "pdf" else FISCAL_LABEL in body.decode("utf-8")
        assert inv["number"].encode() in body  # the referenced invoice

    st_pdf = api_a.get(f"{C}/statement", format="pdf")
    assert st_pdf.status_code == 200 and st_pdf.content.startswith(b"%PDF") and _label_in_pdf(st_pdf.content)

    # another customer's documents cannot be downloaded (404, nothing rendered)
    for path in (
        f"invoices/{env['inv_b']['id']}/pdf",
        f"invoices/{env['inv_b']['id']}/xml",
        f"credit-notes/{env['note_b']['id']}/pdf",
    ):
        r = api_a.get(f"{C}/{path}")
        assert r.status_code == 404 and not r.content.startswith(b"%PDF"), path
    # every download is audited (who: the external account's user; channel portal)
    actions = [e.action for _rid, _t, e, _h in fakes["audit"].records]
    assert "portal.customer.invoice_pdf_downloaded" in actions
    assert "portal.customer.invoice_xml_downloaded" in actions
    assert "portal.customer.credit_note_pdf_downloaded" in actions
