"""P22c — feature e2e (gate G3) of Inventario › Producción: recipes/BOM (T-INV-BOM-04) and transformations
(T-INV-XFORM-09: presentation change, portioning, manual — simulate, approve, post, print, reverse).

Runner: ``qa_harness.e2e`` (real Django on loopback over a real PostgreSQL world, real P08 shell + P09c pages in system
Chrome). The P09c pages are delivered in their folders but not registered in the inventory descriptor
(``frontend/src/features/inventory/module.ts``), hence ``augment=True`` (the wiring defect is filed separately).
Specs: ``tests/e2e/features/T-INV-BOM-04.recipes.spec.ts``, ``tests/e2e/features/T-INV-XFORM-09.transformations.spec.ts``.

All data are synthetic (codes, amounts and account codes labelled SINTÉTICO; no real PUC claim).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)

#: synthetic ledger roles the transformation engine posts to (same roles as the P09c module tests)
XFORM_ACCOUNTS = (
    ("529505", "Mermas y desperdicios (SINTÉTICO)", "expense", "debit", "SCRAP_EXPENSE"),
    ("239595", "Costos adicionales por distribuir (SINTÉTICO)", "liability", "credit", "TRANSFORMATION_COST_CLEARING"),
    ("429505", "Aprovechamientos inventario (SINTÉTICO)", "income", "credit", "INVENTORY_ADJ_GAIN"),
    ("529510", "Pérdida en inventario (SINTÉTICO)", "expense", "debit", "INVENTORY_ADJ_LOSS"),
    ("143010", "Productos cárnicos (SINTÉTICO)", "asset", "debit", "INVENTORY_CARNICOS"),
)


def _uom(w: Any, code: str, precision: int) -> None:
    from inventory import masters

    w.run(lambda u: masters.create_uom(u, w.company(), {"code": code, "name": code, "precision": precision}))


def _item(w: Any, code: str, uom: str = "UND", **extra: Any) -> Any:
    from inventory import masters

    data = {"code": code, "name": f"Producto {code} (SINTÉTICO)", "base_uom": uom, **extra}
    return w.run(lambda u: masters.create_item(u, w.company(), data))


def _receive(w: Any, item: Any, wh: Any, qty: str, cost: str, presentation: Any = None) -> None:
    from inventory.stock.service import SERVICE
    from kernel import ids
    from kernel.interfaces.common import DocumentRef
    from kernel.interfaces.stock import StockLine
    from purchasing.requisitions.tests.kit import TODAY

    line = StockLine(
        "1",
        item.id,
        wh.id,
        Decimal(qty),
        presentation_id=presentation.id if presentation is not None else None,
        unit_cost=Decimal(cost),
    )
    doc = DocumentRef("test.opening", ids.new_id(), "SALDO-SYN")
    w.run(lambda u: SERVICE.receive(u, w.company(), doc, [line], TODAY))


def _approver(w: Any) -> None:
    """``jefe_inv``: reads inventory and approves transformations; never the requester (SoD, D-023)."""
    world = w.world
    world.add_role(
        "inv_aprobador_p22", ["inventory.*.read", "inventory.transformation.approve"], max_cls="confidential"
    )
    world.add_user("jefe_inv", ["ALFA", "BETA"], subject=str(uuid.uuid4()))
    world.assign("jefe_inv", "inv_aprobador_p22", "ALFA")


def world_bom(w: Any) -> dict[str, Any]:
    """Output PAN-MOLDE and components HARINA / LEVADURA with valued stock (explosion with cost); user ``bodega``."""
    wh = w.warehouse("PLANTA")
    pan = _item(w, "PAN-MOLDE")
    harina = _item(w, "HARINA")
    levadura = _item(w, "LEVADURA")
    _receive(w, harina, wh, "100", "2500")
    _receive(w, levadura, wh, "50", "800")
    return {
        "user": "bodega",
        "tokens": e2e.tokens(w, "bodega"),
        "data": {
            "company_id": str(w.company()),
            "output_code": pan.code,
            "harina_code": harina.code,
            "levadura_code": levadura.code,
        },
    }


def world_xform(w: Any) -> dict[str, Any]:
    """Stock for one transformation of each type (user ``bodega`` creates/posts, ``jefe_inv`` approves).

    * PRESENTATION: AZUCAR (UND) with presentation CJ12 (factor 12), 2 boxes received (24 UND packaged);
    * PORTIONING: RES-CANAL 10 KG at 20 000/kg (category-less, INVENTORY) → LOMO / MOLIDA / HUESO (category CARNICOS,
      INVENTORY_CARNICOS) by a template created in the UI;
    * MANUAL: MAN-A 10 × 80 000 + MAN-B 5 × 60 000 = COP 1 100 000 (> the default 1 000 000 approval threshold)
      → MAN-C 12 (CARNICOS) + MAN-D 3.
    """
    from inventory import masters
    from inventory.models import Item
    from ledger.models import Account, AccountDetermination

    cid = w.company()

    def accounts(u: Any) -> None:
        for code, name, typ, nature, role in XFORM_ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=w.world.tenant_id, company_id=cid, code=code, name=name, type=typ, nature=nature
            )
            AccountDetermination.objects.create(tenant_id=w.world.tenant_id, company_id=cid, role_code=role, account=acc)

    w.run(accounts)
    _approver(w)
    _uom(w, "KG", 3)
    wh = w.warehouse("PLANTA")

    azucar = _item(w, "AZUCAR")
    box = w.run(
        lambda u: masters.create_presentation(
            u,
            Item.objects.select_related("base_uom").get(id=azucar.id),
            {"code": "CJ12", "name": "Caja x12 (SINTÉTICO)", "uom": "cj12", "factor": "12"},
        )
    )
    _receive(w, azucar, wh, "24", "1500", presentation=box)

    res = _item(w, "RES-CANAL", "KG")
    lomo = _item(w, "LOMO", "KG", category="CARNICOS")
    molida = _item(w, "MOLIDA", "KG", category="CARNICOS")
    hueso = _item(w, "HUESO", "KG", category="CARNICOS")
    _receive(w, res, wh, "10", "20000")

    man_a, man_b = _item(w, "MAN-A"), _item(w, "MAN-B")
    man_c, man_d = _item(w, "MAN-C", category="CARNICOS"), _item(w, "MAN-D")
    _receive(w, man_a, wh, "10", "80000")
    _receive(w, man_b, wh, "5", "60000")

    def ids(*items: Any) -> dict[str, str]:
        return {i.code: str(i.id) for i in items}

    return {
        "user": "bodega",
        "tokens": e2e.tokens(w, "bodega", "jefe_inv"),
        "data": {
            "company_id": str(cid),
            "warehouse_id": str(wh.id),
            "warehouse_code": wh.code,
            "presentation_id": str(box.id),
            "items": ids(azucar, res, lomo, molida, hueso, man_a, man_b, man_c, man_d),
            "names": {i.code: i.name for i in (azucar, res, lomo, molida, hueso, man_a, man_b, man_c, man_d)},
        },
    }


def test_T_INV_BOM_04_ui_create_recipe_v1_activate_copy_v2_with_new_dates() -> None:
    e2e.run_feature("T-INV-BOM-04", world_bom, augment=True, known_gaps=("DEF-P22-040",))


def test_T_INV_XFORM_09_ui_each_type_simulate_approve_post_print_reverse() -> None:
    e2e.run_feature(
        "T-INV-XFORM-09",
        world_xform,
        augment=True,
        known_gaps=("DEF-P22-041", "DEF-P22-042", "DEF-P22-043"),
    )
