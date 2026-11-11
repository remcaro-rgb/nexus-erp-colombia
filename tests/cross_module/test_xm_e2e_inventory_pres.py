"""P22c — T-INV-PRES-05 feature e2e (gate G3): Presentaciones UI → receive by presentation → stock per presentation.

Runner: ``qa_harness.e2e`` (real Django on loopback over a real PostgreSQL world, real P08 shell + P09 pages in system
Chrome). Spec: ``tests/e2e/features/T-INV-PRES-05.presentations.spec.ts``.
"""

from __future__ import annotations

from typing import Any

import pytest

from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)


def world_inv_pres(w: Any) -> dict[str, Any]:
    """Item LECHE-UHT (UND), packaging UoM CAJA, a SENT purchase order of 36 UND to receive (user ``bodega``)."""
    from inventory import masters
    from purchasing.requisitions.tests.kit import PUR, order_sent

    item = w.item("LECHE-UHT")
    w.run(lambda u: masters.create_uom(u, w.company(), {"code": "CAJA", "name": "Caja", "precision": 0}))
    wh = w.warehouse("PRINCIPAL")
    supplier = w.supplier("PROV-LACTEOS")
    r = w.api("comprador").post(
        f"{PUR}/orders",
        {
            "party": {"id": str(supplier.id)},
            "currency": "COP",
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(item.id),
                    "description": item.name,
                    "quantity": {"value": "36", "uom": "UND"},
                    "unit_price": {"amount": "3200", "currency": "COP"},
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    order = order_sent(w, r.json()["id"])
    return {
        "user": "bodega",
        "tokens": e2e.tokens(w, "bodega"),
        "data": {
            "company_id": str(w.company()),
            "item_id": str(item.id),
            "item_code": item.code,
            "item_name": item.name,
            "warehouse_id": str(wh.id),
            "order_id": order["id"],
            "order_line_id": order["lines"][0]["id"],
        },
    }


def test_T_INV_PRES_05_ui_create_presentation_receive_and_stock_per_presentation() -> None:
    e2e.run_feature("T-INV-PRES-05", world_inv_pres, known_gaps=("DEF-P22-010",))
