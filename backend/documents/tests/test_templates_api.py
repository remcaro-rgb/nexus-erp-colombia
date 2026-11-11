"""Documents API: company templates (versioned, sandbox-validated), preview against sample data, synchronous render
of explicit data and the 202 render batch — all policy-checked, idempotent."""

from __future__ import annotations

import pytest

from documents.models import DocumentTemplateVersion, GeneratedDocument
from documents.pdf import reader
from reporting.testing.support import ApiClient, World

pytestmark = pytest.mark.django_db
URL = "/api/v1/documents/templates"
BODY = (
    "# {{ company.name }}\n## COTIZACIÓN {{ number }}\n|!Ítem|Valor|\n"
    "{% for l in lines %}|{{ l.description }}|{{ l.amount|money }}|\n{% endfor %}**{{ total|words }}**\n"
)


def test_template_crud_versions_preview_and_render(rworld: World, dapi: ApiClient) -> None:
    bad = dapi.post(
        URL, {"code": "sales.quote", "name": "x", "document_type": "sales.quote", "body": "{{ ''.__class__.__mro__ }}"}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "template_render_failed"
    created = dapi.post(
        URL, {"code": "sales.quote", "name": "Cotización empresa", "document_type": "sales.quote", "body": BODY}
    )
    assert created.status_code == 201, created.content
    t = created.json()
    assert t["version"] == 1 and t["engine"] == "weasyprint" and t["locale"] == "es-CO"
    assert dapi.post(URL, {"code": "sales.quote", "name": "y", "document_type": "q", "body": "x"}).status_code == 409
    listed = dapi.get(URL).json()
    assert [x["code"] for x in listed["items"]] == ["sales.quote"] and listed["next_cursor"] is None
    etag = dapi.get(f"{URL}/{t['id']}")["ETag"]
    patched = dapi.send("patch", f"{URL}/{t['id']}", {"body": BODY + "> Gracias\n"}, if_match=etag)
    assert patched.status_code == 200 and patched.json()["version"] == 2
    with rworld.scope():
        assert DocumentTemplateVersion.objects.filter(template_id=t["id"]).count() == 2

    sample = {
        "company": {"name": "Demo"},
        "number": "COT-1",
        "lines": [{"description": "Servicio", "amount": {"amount": "1000000", "currency": "COP"}}],
        "total": {"amount": "1000000", "currency": "COP"},
    }
    preview = dapi.post(f"{URL}/{t['id']}:preview", {"parameters": {"sample": sample}})
    assert preview.status_code == 200, preview.content
    doc = preview.json()["documents"][0]
    assert preview.json()["persisted"] is False and doc["pages"] == 1 and doc["template_version"] == "c2"
    assert "<h2>COTIZACIÓN COT-1</h2>" in doc["html"] and "UN MILLÓN DE PESOS M/CTE" in doc["html"]

    # /render with explicit data uses the company template (overrides the built-in of the same code)
    r = dapi.post("/api/v1/documents/render", {"parameters": {"template_code": "sales.quote", "data": sample}})
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    text = reader.text(r.content)
    assert "COTIZACIÓN COT-1" in text and "Gracias" in text and "sales.quote vc2" in text
    # built-in template for a code without a company template
    pos = dapi.post(
        "/api/v1/documents/render",
        {
            "parameters": {
                "template_code": "pos.ticket",
                "data": {
                    "company": {"name": "Demo"},
                    "number": "POS-1",
                    "lines": [],
                    "total": {"amount": "1", "currency": "COP"},
                },
            }
        },
    )
    assert pos.status_code == 200 and "DOCUMENTO POS N.º POS-1" in reader.text(pos.content)

    batch = dapi.post(
        "/api/v1/documents/render:render-batch",
        {
            "parameters": {
                "items": [
                    {"template_code": "sales.quote", "data": sample},
                    {"template_code": "generic", "data": {"title": "Nota"}},
                ]
            }
        },
    )
    assert batch.status_code == 202, batch.content
    op = batch.json()
    assert op["status"] == "SUCCEEDED" and len(op["result_links"]) == 2 and batch["Location"].endswith(op["id"])
    with rworld.scope():
        assert GeneratedDocument.objects.count() == 3  # the identical quote is stored once (same hash)

    # archive (DELETE) keeps the history; the built-in template is used again
    assert dapi.send("delete", f"{URL}/{t['id']}").status_code == 204
    again = dapi.post("/api/v1/documents/render", {"parameters": {"template_code": "sales.quote", "data": sample}})
    assert "sales.quote v1.0.0" in reader.text(again.content)


def test_documents_api_is_deny_by_default(rworld: World, dapi: ApiClient) -> None:
    outsider = dapi.as_("outsider")
    assert outsider.get(URL).status_code == 403
    assert outsider.post(URL, {"code": "x", "name": "x", "document_type": "x", "body": "x"}).status_code == 403
    r = outsider.post("/api/v1/documents/render", {"parameters": {"template_code": "generic", "data": {}}})
    assert r.status_code == 403
    reader_only = rworld.user("reader", ("documents.template.read",))
    assert reader_only is not None
    assert (
        dapi.as_("reader").post("/api/v1/documents/render", {"parameters": {"template_code": "generic"}}).status_code
        == 403
    )
    assert dapi.as_("analyst").post("/api/v1/documents/render", {"parameters": {}}).status_code == 422
