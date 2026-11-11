"""T-P18-10 and the HTTP contract of ``/api/v1/dataio/…`` + ``/api/v1/kpis/datos``: an import through the API
with a service account respects its permissions; upload → validate → commit → report; export file; templates;
mapping profiles; KPIs (zero/previous-period cases)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from dataio.testing import context as test_context
from dataio.testing import helpers as h
from kernel import ids
from kernel.tenancy import PrincipalType, TenantContext

pytestmark = pytest.mark.django_db
BASE = "/api/v1/dataio"
CSV = h.csv_bytes(
    [
        {
            "code": "BUC",
            "name": "Bucaramanga",
            "is_head_office": "false",
            "municipality_code": "68001",
            "valid_from": "2026-01-01",
        },
        {"code": "PEI", "name": "Pereira", "is_head_office": "false", "valid_from": "2026-01-01"},
    ]
)


@pytest.fixture
def api(w: Any, settings: Any) -> Any:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "dataio.testing.context.resolve_context"

    def client(ctx: TenantContext, company: str = "ALFA") -> APIClient:
        c = APIClient()
        c.credentials(HTTP_X_COMPANY_ID=str(w.companies[company]), HTTP_X_TEST_CONTEXT=test_context.register(ctx))
        return c

    return client


def idem() -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"test-{ids.new_id()}"}


def _import(c: APIClient, entity: str, data: bytes, name: str = "sucursales.csv") -> dict[str, Any]:
    r = c.post(f"{BASE}/imports", {"entity": entity}, format="json", **idem())
    assert r.status_code == 201, r.content
    jid = r.json()["id"]
    r = c.post(f"{BASE}/imports/{jid}:upload", {"file": SimpleUploadedFile(name, data)}, format="multipart", **idem())
    assert r.status_code == 200, r.content
    return r.json()


def test_T_P18_10_import_through_the_api_with_a_service_account(w, api):
    svc_acct = TenantContext(w.tenant_id, frozenset([w.companies["ALFA"]]), uuid.uuid4(), PrincipalType.SERVICE)
    w.policy.grant(svc_acct.principal_id, ["dataio.*.*", "iam.branch.read"])
    c = api(svc_acct)
    job = _import(c, "branches", CSV)
    assert job["import_status"] == "UPLOADED" and job["row_count"] == 2 and job["mapping"]["code"] == "code"
    r = c.post(f"{BASE}/imports/{job['id']}:commit", {}, format="json", **idem())
    assert r.status_code == 422 and r.json()["code"] == "import_validation_failed"
    assert {e["code"] for e in r.json()["errors"]["rows"]} == {"policy_denied"}
    # the rejection was stored (not rolled back with the error response)
    assert c.get(f"{BASE}/imports/{job['id']}").json()["import_status"] == "FAILED"
    # with the module permission the same file goes through
    w.policy.grant(svc_acct.principal_id, ["iam.branch.create"])
    job = _import(c, "branches", CSV)
    r = c.post(f"{BASE}/imports/{job['id']}:validate", {}, format="json", **idem())
    assert r.status_code == 200 and r.json()["import_status"] == "VALIDATED"
    assert [p["action"] for p in r.json()["preview"]] == ["created", "created"]
    r = c.post(f"{BASE}/imports/{job['id']}:commit", {}, format="json", **idem())
    assert r.status_code == 202 and r.json()["status"] == "SUCCEEDED" and r["Location"].endswith(job["id"])
    r2 = c.post(f"{BASE}/imports/{job['id']}:commit", {}, format="json", **idem())
    assert r2.status_code == 202 and r2.json()["import"]["counts"] == r.json()["import"]["counts"]
    rep = c.get(f"{BASE}/imports/{job['id']}/report", {"file_format": "csv"})
    assert rep.status_code == 200 and b"_resultado" in rep.content and b"created" in rep.content
    j = c.post(f"{BASE}/imports", {"entity": "branches"}, format="json", **idem()).json()
    up = c.post(
        f"{BASE}/imports/{j['id']}:upload",
        {"filename": "b.csv", "content_base64": __import__("base64").b64encode(CSV).decode()},
        format="json",
        **idem(),
    )
    assert up.status_code == 200 and up.json()["row_count"] == 2
    assert c.post(f"{BASE}/imports", {"entity": "branches"}, format="json").status_code == 400  # Idempotency-Key
    assert c.post(f"{BASE}/imports", {"entity": "nope"}, format="json", **idem()).json()["code"] == "template_unknown"
    assert (
        c.post(f"{BASE}/imports", {"entity": "branches", "company_id": "x"}, format="json", **idem()).status_code == 422
    )


def test_api_templates_exports_profiles_and_kpis(w, api):
    c = api(w.ctx())
    r = c.get(f"{BASE}/templates")
    assert r.status_code == 200 and len(r.json()["items"]) == 15
    assert c.get(f"{BASE}/templates/dimensions").json()["natural_key"] == ["dimension", "code"]
    dl = c.get(f"{BASE}/templates/dimensions/download", {"file_format": "xlsx"})
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    k0 = c.get("/api/v1/kpis/datos", {"period": "2026-11"}).json()
    assert {k["id"]: (k["value"], k["state"]) for k in k0["kpis"]} == {
        "lotes_importados": ("0", "no_data"),
        "filas_importadas": ("0", "no_data"),
        "lotes_rechazados": ("0", "no_data"),
        "exportaciones": ("0", "no_data"),
    }
    job = _import(c, "branches", CSV)
    c.post(f"{BASE}/imports/{job['id']}:commit", {}, format="json", **idem())
    bad = _import(c, "branches", h.csv_bytes([{"code": "X", "name": "", "is_head_office": "false"}]))
    c.post(f"{BASE}/imports/{bad['id']}:commit", {}, format="json", **idem())
    r = c.post(f"{BASE}/exports", {"source": "branches", "format": "csv"}, format="json", **idem())
    assert r.status_code == 201 and r.json()["export_status"] == "COMPLETED" and r.json()["row_count"] == 3
    f = c.get(f"{BASE}/exports/{r.json()['id']}/file")
    assert f.status_code == 200 and f.content.startswith(b"code,name")
    b64 = c.get(f"{BASE}/exports/{r.json()['id']}/file", {"encoding": "base64"}).json()
    assert __import__("base64").b64decode(b64["content_base64"]) == f.content and b64["filename"].endswith(".csv")
    shell = c.post(
        f"{BASE}/exports",
        {"entity": "branches", "params": {}, "company_id": str(w.companies["ALFA"]), "period_id": None},
        format="json",
        **idem(),
    )
    assert shell.status_code == 201 and shell.json()["format"] == "csv"
    k1 = c.get("/api/v1/kpis/datos", {"period": "2026-11"}).json()
    assert {k["id"]: k["value"] for k in k1["kpis"]} == {
        "lotes_importados": "1",
        "filas_importadas": "2",
        "lotes_rechazados": "1",
        "exportaciones": "2",
    }
    assert {k["id"]: k["previous"] for k in k1["kpis"]} == {
        "lotes_importados": "0",
        "filas_importadas": "0",
        "lotes_rechazados": "0",
        "exportaciones": "0",
    }
    assert c.get("/api/v1/kpis/datos", {"period": "2026-10"}).json()["kpis"][0]["value"] == "0"
    panel = c.get("/api/v1/kpis/datos/panels/lotes_por_tipo", {"period": "2026-11"}).json()
    assert panel["points"] == [{"label": "branches", "values": {"confirmado": "1", "rechazado": "1"}}]
    assert c.get(f"{BASE}/imports", {"estado": "rechazado"}).json()["items"][0]["id"] == bad["id"]
    p = c.post(
        f"{BASE}/mapping-profiles",
        {"name": "Mi ERP viejo", "entity": "branches", "mapping": {"Sucursal": "code", "Nombre": "name"}},
        format="json",
        **idem(),
    )
    assert p.status_code == 201
    legacy = h.csv_bytes([{"Sucursal": "TUN", "Nombre": "Tunja", "Extra": "ignorar"}])
    r = c.post(f"{BASE}/imports", {"entity": "branches"}, format="json", **idem())
    up = c.post(
        f"{BASE}/imports/{r.json()['id']}:upload",
        {"file": SimpleUploadedFile("v.csv", legacy), "profile_id": p.json()["id"]},
        format="multipart",
        **idem(),
    )
    assert up.json()["mapping"] == {"Sucursal": "code", "Nombre": "name", "Extra": ""}
    m = c.post(
        f"{BASE}/imports/{r.json()['id']}:map", {"parameters": {"mapping": {"Extra": ""}}}, format="json", **idem()
    )
    assert m.status_code == 200
    assert (
        c.post(f"{BASE}/imports/{r.json()['id']}:commit", {}, format="json", **idem()).status_code == 422
    )  # Extra unmapped


def test_api_denies_without_permission_and_hides_other_company(w, api):
    stranger = w.user("sin_permisos", [])
    c = api(stranger)
    assert c.get(f"{BASE}/templates").status_code == 403
    assert c.post(f"{BASE}/imports", {"entity": "branches"}, format="json", **idem()).status_code == 403
    assert {k["id"] for k in c.get("/api/v1/kpis/datos").json()["kpis"]} == set()
    owner = api(w.ctx(), "ALFA")
    job = _import(owner, "branches", CSV)
    beta_only = w.user("beta", ["*.*.*"], companies=("BETA",))
    assert api(beta_only, "BETA").get(f"{BASE}/imports/{job['id']}").status_code == 404
