"""T-ACC-BRANCH-03 — a warehouse requires a branch of the same company (API, service and composite FK)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from inventory import masters
from inventory.models import Location, Warehouse
from kernel import ids

pytestmark = pytest.mark.django_db


def test_T_ACC_BRANCH_03_warehouse_requires_same_company_branch(inv, clerk, login):
    api = login(clerk, "ALFA")
    alfa_bog = inv.branches["ALFA:BOG"]
    beta_bog = inv.branches["BETA:BOG"]

    # missing branch → 422 validation (required)
    r = api.post("/api/v1/inventory/warehouses", {"code": "WH0", "name": "Sin sucursal"})
    assert r.status_code == 422, r.content
    assert r.json()["code"] == "validation_failed"

    # branch of another company → branch_cross_company
    r = api.post("/api/v1/inventory/warehouses", {"code": "WHX", "name": "Cruzada", "branch_id": str(beta_bog)})
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company", r.content

    # same-company branch → created with the D-014 default tree
    r = api.post("/api/v1/inventory/warehouses", {"code": "WH1", "name": "Principal", "branch_id": str(alfa_bog)})
    assert r.status_code == 201, r.content
    wh = r.json()
    assert wh["branch_id"] == str(alfa_bog) and wh["company_id"] == str(inv.company("ALFA"))
    with inv.scope():
        codes = sorted(Location.objects.filter(warehouse_id=wh["id"]).values_list("code", "location_type"))
    assert codes == [
        ("ALMACEN", "INTERNAL"), ("DESPACHO", "INTERNAL"), ("RECEPCION", "INTERNAL"),
        ("SCRAP", "SCRAP"), ("TRANSITO", "TRANSIT"), ("WH1", "VIEW"),
    ]  # fmt: skip

    # PATCH to a foreign branch is rejected as well (If-Match required)
    detail = api.get(f"/api/v1/inventory/warehouses/{wh['id']}")
    r = api.patch(f"/api/v1/inventory/warehouses/{wh['id']}", {"branch_id": str(beta_bog)}, HTTP_IF_MATCH=detail["ETag"])
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company"
    r = api.patch(f"/api/v1/inventory/warehouses/{wh['id']}", {"branch_id": str(inv.branches["ALFA:MED"])})
    assert r.status_code == 428

    # service level: an unknown branch id is also rejected
    with pytest.raises(Exception) as exc:
        inv.run(lambda u: masters.create_warehouse(u, inv.company(), {"code": "WH9", "name": "x", "branch_id": str(ids.new_id())}))
    assert getattr(exc.value, "code", "") == "branch_cross_company"

    # DB level: the composite FK refuses an ALFA warehouse pointing at a BETA branch even through the ORM
    with inv.scope(), pytest.raises(IntegrityError), transaction.atomic():
        Warehouse.objects.create(
            tenant_id=inv.world.tenant_id, company_id=inv.company("ALFA"), code="RAW", name="raw", branch_id=beta_bog
        )

    # the branch now has a dependent ACTIVE warehouse: archiving it is blocked (P04 reference registered by P09)
    from iam.branches import services as branch_services
    from iam.models import Branch

    with inv.scope():
        refs = branch_services.active_references(Branch.objects.get(id=alfa_bog))
    assert {"type": "inventory.warehouse", "id": wh["id"], "label": "WH1"} in refs
