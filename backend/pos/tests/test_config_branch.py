"""T-ACC-BRANCH-04 — a PosConfig requires a branch of the SAME company (API, service, composite FK, P04 reference)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from kernel.testing.tenancy import bound
from pos.models import PosConfig
from pos.services import setup
from pos.testing.support import PosEnv
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_T_ACC_BRANCH_04_pos_config_requires_same_company_branch(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    api.as_("pos_admin")
    wh = str(env.warehouse.id)

    # missing branch → 422 validation_failed (required FK)
    r = api.post("configs", {"name": "Sin sucursal", "warehouse_id": wh})
    assert r.status_code == 422 and r.json()["code"] == "validation_failed", r.content

    # branch of ANOTHER company of the tenant → branch_cross_company
    r = api.post("configs", {"name": "Cruzada", "warehouse_id": wh, "branch_id": str(env.foreign_branch.id)})
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company", r.content

    # same-company branch but the warehouse belongs to another branch → refused
    r = api.post(
        "configs",
        {"name": "Bodega ajena", "warehouse_id": str(env.other_warehouse.id), "branch_id": str(env.branch.id)},
    )
    assert r.status_code == 422 and r.json()["code"] == "validation_failed", r.content

    # same-company branch → created, branch stamped
    r = api.post("configs", {"name": "Caja BOG", "warehouse_id": wh, "branch_id": str(env.branch.id)})
    assert r.status_code == 201, r.content
    cfg = r.json()
    assert cfg["branch_id"] == str(env.branch.id) and cfg["company_id"] == str(env.company)

    # PATCH to a foreign branch is rejected too (If-Match required)
    etag = api.get(f"configs/{cfg['id']}")["ETag"]
    r = api.patch(f"configs/{cfg['id']}", {"branch_id": str(env.foreign_branch.id)}, if_match=etag)
    assert r.status_code == 422 and r.json()["code"] == "branch_cross_company", r.content
    r = api.patch(f"configs/{cfg['id']}", {"name": "Caja BOG 1"})
    assert r.status_code == 428

    # an unknown branch id is rejected at the service level
    with pytest.raises(Exception) as exc:
        env.run(
            "pos_admin",
            lambda u: setup.create_config(
                u, env.company, {"name": "x", "warehouse_id": wh, "branch_id": "0190f0aa-0000-7000-8000-000000000001"}
            ),
        )
    assert getattr(exc.value, "code", "") == "branch_cross_company"

    # DB level: the composite FK refuses a POS of company 1 pointing at a branch of company 2, even through the ORM
    with bound(env.world.ctx("admin")), pytest.raises(IntegrityError), transaction.atomic():
        PosConfig.objects.create(
            tenant_id=env.world.tenant_id,
            company_id=env.company,
            name="raw",
            branch_id=env.foreign_branch.id,
            warehouse_id=env.warehouse.id,
        )

    # the branch now has an ACTIVE POS: P04's archive check lists it (branch_in_use dependency list)
    from iam.branches import services as branch_services
    from iam.models import Branch

    with bound(env.world.ctx("admin")):
        refs = branch_services.active_references(Branch.objects.get(id=env.branch.id))
    assert {"type": "pos.config", "id": cfg["id"], "label": "Caja BOG"} in refs

    # a cashier cannot manage POS configurations (cajero_pos denies pos.config.*)
    api.as_("cashier")
    r = api.post("configs", {"name": "Intento", "warehouse_id": wh, "branch_id": str(env.branch.id)})
    assert r.status_code == 403
